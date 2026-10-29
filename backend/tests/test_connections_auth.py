import base64
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from django.utils import timezone

from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from vault.models import Connection, OAuthToken, Secret

BASE = "/api/v1/workspaces/team-a"
LOG: list[dict] = []
STATE = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        parts = urlsplit(self.path)
        LOG.append({"path": parts.path, "query": parse_qs(parts.query), "headers": dict(self.headers), "body": body})
        if parts.path == "/token":
            STATE["n"] += 1
            form = parse_qs(body)
            out = {"access_token": f"at-{STATE['n']}-secretvalue", "expires_in": 3600, "token_type": "Bearer"}
            if form.get("grant_type") == ["authorization_code"]:
                out["refresh_token"] = "rt-secretvalue"
            if form.get("grant_type") == ["refresh_token"] and form.get("refresh_token") != ["rt-secretvalue"]:
                self._send(400, {"error": "invalid_grant"})
                return
            self._send(200, out)
            return
        self._send(200, {"seen": {k: v for k, v in self.headers.items()}, "query": parse_qs(parts.query)})

    def _send(self, code, out):
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _reply

    def log_message(self, *a):
        pass


@pytest.fixture
def server(settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    LOG.clear()
    STATE["n"] = 0
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def ws(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    return ws


def _conn(ws, kind, secret="s3cret-value", **config):
    s, _ = Secret.objects.get_or_create(workspace=ws, name=f"s-{kind}")
    s.set_value(secret)
    s.save()
    return Connection.objects.create(workspace=ws, name="svc", label="S", kind=kind, config=config, secret=s)


def _call(ws, world, path="/ping"):
    user = world["designer"]
    v = publish(ws, "w", [node("h", "http", connection="svc", mode="reel", path=path)], [], user=user)
    run = drain(launch(ws, v, user))
    return run, run.steps.get(node_id="h")


def test_api_key_in_header_and_in_query_and_masked(ws, world, server):
    _conn(ws, "api_key", base_url=server, name="X-Key")
    run, sr = _call(ws, world)
    assert run.status == "succeeded" and LOG[-1]["headers"]["X-Key"] == "s3cret-value"
    assert "s3cret-value" not in json.dumps(sr.output)
    Connection.objects.all().delete()
    _conn(ws, "api_key", base_url=server, name="apikey", **{"in": "query"})
    run, sr = _call(ws, world, "/ping?x=1")
    assert LOG[-1]["query"]["apikey"] == ["s3cret-value"] and LOG[-1]["query"]["x"] == ["1"]
    assert "s3cret-value" not in json.dumps(sr.output)


def test_basic_auth(ws, world, server):
    _conn(ws, "basic", secret="pa55word", base_url=server, username="robot")
    run, sr = _call(ws, world)
    expected = "Basic " + base64.b64encode(b"robot:pa55word").decode()
    assert LOG[-1]["headers"]["Authorization"] == expected
    assert "pa55word" not in json.dumps(sr.output) and expected not in json.dumps(sr.output)


def test_oauth2_client_credentials_token_is_fetched_once_cached_and_refreshed(ws, world, server):
    conn = _conn(ws, "oauth2_client", base_url=server, token_url=server + "/token", client_id="cid", scope="read")
    _call(ws, world)
    _call(ws, world)
    tokens = [e for e in LOG if e["path"] == "/token"]
    assert (
        len(tokens) == 1
        and "grant_type=client_credentials" in tokens[0]["body"]
        and "client_secret=s3cret-value" in tokens[0]["body"]
    )
    pings = [e for e in LOG if e["path"] == "/ping"]
    assert all(p["headers"]["Authorization"] == "Bearer at-1-secretvalue" for p in pings)
    OAuthToken.objects.filter(connection=conn).update(expires_at=timezone.now() - timedelta(seconds=1))
    run, sr = _call(ws, world)
    assert LOG[-1]["headers"]["Authorization"] == "Bearer at-2-secretvalue"
    assert "secretvalue" not in json.dumps(sr.output)


def test_oauth2_authorization_code_flow(ws, world, server):
    conn = _conn(
        ws,
        "oauth2_code",
        base_url=server,
        authorize_url=server + "/authorize",
        token_url=server + "/token",
        client_id="cid",
        scope="read",
    )
    run, _ = _call(ws, world)
    assert run.status == "failed" and run.error["code"] == "OAUTH_NOT_AUTHORIZED"
    c = client_for(world["designer"])
    start = c.post(f"{BASE}/connections/{conn.pk}/oauth/start/")
    assert start.status_code == 200
    q = parse_qs(urlsplit(start.data["authorize_url"]).query)
    assert (
        q["client_id"] == ["cid"]
        and q["response_type"] == ["code"]
        and q["redirect_uri"][0].endswith("/api/v1/oauth/callback/")
    )
    from rest_framework.test import APIClient

    anon = APIClient()
    assert anon.get("/api/v1/oauth/callback/?code=abc&state=forged").headers["Location"].endswith("oauth=invalid")
    done = anon.get(f"/api/v1/oauth/callback/?code=abc&state={q['state'][0]}")
    assert done.status_code == 302 and done.headers["Location"].endswith("oauth=ok")
    assert OAuthToken.objects.get(connection=conn).refresh() == "rt-secretvalue"
    run, sr = _call(ws, world)
    assert run.status == "succeeded" and LOG[-1]["headers"]["Authorization"].startswith("Bearer at-")
    OAuthToken.objects.filter(connection=conn).update(expires_at=timezone.now() - timedelta(seconds=1))
    _call(ws, world)
    assert any("grant_type=refresh_token" in e["body"] for e in LOG if e["path"] == "/token")
    assert "secretvalue" not in json.dumps(c.get(f"{BASE}/connections/").data)  # tokens never leave the server


def test_connection_settings_are_validated_per_kind(ws, world):
    c = client_for(world["designer"])
    body = {
        "name": "x",
        "label": "X",
        "kind": "oauth2_client",
        "config": {"base_url": "https://example.org"},
        "secret_value": "abcdefgh",
    }
    assert c.post(f"{BASE}/connections/", body, format="json").status_code == 400  # token_url / client_id missing
    body["config"] = {
        "base_url": "https://example.org",
        "token_url": "https://example.org/t",
        "client_id": "cid",
        "other": "dropped",
    }
    ok = c.post(f"{BASE}/connections/", body, format="json")
    assert ok.status_code == 201 and "other" not in ok.data["config"]
    bad = {
        "name": "y",
        "label": "Y",
        "kind": "api_key",
        "config": {"base_url": "https://example.org", "in": "cookie"},
        "secret_value": "abcdefgh",
    }
    assert c.post(f"{BASE}/connections/", bad, format="json").status_code == 400
