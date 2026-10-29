import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.wf_helpers import ACTIONS, drain, edge, launch, make_action, node, publish, schema
from vault.models import Connection, Secret

SEEN: list[dict] = []
STATE: dict = {}


class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        SEEN.append(
            {
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "ctype": self.headers.get("Content-Type"),
                "body": body,
            }
        )
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(self.path)
        q = parse_qs(parts.query)
        if parts.path == "/pages":
            n = int(q.get("page", ["1"])[0])
            data = {"data": {"items": [f"p{n}-a", f"p{n}-b"] if n <= 3 else []}}
            return self._json(200, data)
        if parts.path == "/cursor":
            cur = q.get("cursor", ["c1"])[0]
            nxt = {"c1": "c2", "c2": None}[cur]
            return self._json(200, {"rows": [cur], "meta": {"next": nxt}})
        if parts.path == "/linked":
            n = int(q.get("n", ["1"])[0])
            extra = (
                {"Link": f'<http://127.0.0.1:{self.server.server_port}/linked?n={n + 1}>; rel="next"'} if n < 3 else {}
            )
            return self._json(200, [n], extra)
        if parts.path == "/flaky":
            STATE["flaky"] = STATE.get("flaky", 0) + 1
            if STATE["flaky"] == 1:
                return self._json(503, {"error": "busy"}, {"Retry-After": "1"})
            return self._json(200, {"ok": True})
        if self.path.startswith("/missing"):
            code, out = 404, {"error": "absent"}
        else:
            code, out = 200, {"echo": self.headers.get("Authorization"), "path": self.path, "sent": body}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, out, headers=None):
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _reply

    def log_message(self, *a):
        pass


@pytest.fixture
def server(settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    SEEN.clear()
    STATE.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def ws(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="tok")
    s.set_value("sekret-token-123")
    s.save()
    Connection.objects.create(
        workspace=ws, name="svc", label="Service", kind="http_bearer", config={"base_url": server}, secret=s
    )
    return ws


def _run(ws, world, http, slug="h", inputs=None):
    user = world["designer"]
    props = {k: {"type": "string"} for k in (inputs or {})}
    v = publish(
        ws, slug, [node("h", "http", name="Appel", connection="svc", **http)], [], inputs=schema(**props), user=user
    )
    return drain(launch(ws, v, user, inputs=inputs))


def test_real_request_sends_the_secret_and_masks_it_in_the_output(ws, world):
    run = _run(ws, world, {"mode": "reel", "method": "POST", "path": "/ping", "body": {"a": 1}, "query": {"x": "1"}})
    out = run.steps.get(node_id="h").output
    assert run.status == "succeeded" and out["status"] == 200 and out["ok"] and out["simulated"] is False
    assert SEEN[0]["auth"] == "Bearer sekret-token-123" and SEEN[0]["path"] == "/ping?x=1"
    assert json.loads(SEEN[0]["body"]) == {"a": 1}
    assert "sekret-token-123" not in json.dumps(out) and out["body"]["echo"] == "Bearer ***"
    assert "sekret-token-123" not in json.dumps(run.steps.get(node_id="h").http_job["spec"])


def test_test_mode_sends_nothing(ws, world):
    run = _run(ws, world, {"mode": "test", "path": "/ping"})
    out = run.steps.get(node_id="h").output
    assert run.status == "succeeded" and out["simulated"] is True and SEEN == []


def test_error_status_fails_the_step_unless_told_otherwise(ws, world):
    run = _run(ws, world, {"mode": "reel", "path": "/missing"})
    assert run.status == "failed" and run.error["code"] == "HTTP_404"
    run = _run(ws, world, {"mode": "reel", "path": "/missing", "fail_on_status": False}, slug="h2")
    out = run.steps.get(node_id="h").output
    assert run.status == "succeeded" and out["status"] == 404 and out["ok"] is False


def test_path_cannot_change_the_host_and_headers_are_checked(ws, world):
    run = _run(ws, world, {"mode": "reel", "path": "{{ inputs.p }}"}, inputs={"p": "@evil.example/x"})
    assert run.status == "failed" and run.error["code"] == "HTTP_CONFIG"
    run = _run(ws, world, {"mode": "reel", "path": "/x", "headers": {"Authorization": "Bearer other"}}, slug="h2")
    assert run.status == "failed" and "interdit" in run.error["message"]
    assert SEEN == []


def test_missing_connection_is_a_validation_error_and_unreachable_host_is_blocked(ws, world, settings):
    from workflows.validation import validate_graph

    graph = {"inputs": {"type": "object"}, "nodes": [node("h", "http", path="/x", connection="nope")], "edges": []}
    codes = {i["code"] for i in validate_graph(graph, ws, "t")}
    assert "unknown_connection" in codes
    settings.ORCH_EGRESS_ALLOWLIST = ""  # loopback is no longer allowed: SSRF guard applies at send time
    run = _run(ws, world, {"mode": "reel", "path": "/ping"})
    assert run.status == "failed" and run.error["code"] == "HTTP_BLOCKED"


def test_response_can_feed_the_next_step(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "chain",
        [
            node("h", "http", connection="svc", mode="reel", path="/ping"),
            node("e", action="echo", inputs={"value": "{{ steps.h.output.body.path }}"}),
        ],
        [edge("h", "e")],
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "succeeded" and run.steps.get(node_id="e").output == {"value": "/ping"}


def test_pagination_page_cursor_and_link(ws, world):
    run = _run(
        ws,
        world,
        {"mode": "reel", "path": "/pages", "pagination": {"type": "page", "items_path": "data.items", "max_pages": 10}},
    )
    out = run.steps.get(node_id="h").output
    assert out["body"] == ["p1-a", "p1-b", "p2-a", "p2-b", "p3-a", "p3-b"] and out["pages"] == 4
    run = _run(
        ws,
        world,
        {
            "mode": "reel",
            "path": "/cursor",
            "pagination": {"type": "cursor", "items_path": "rows", "next_path": "meta.next"},
        },
        slug="h2",
    )
    assert run.steps.get(node_id="h").output["body"] == ["c1", "c2"]
    run = _run(ws, world, {"mode": "reel", "path": "/linked", "pagination": {"type": "link"}}, slug="h3")
    assert run.steps.get(node_id="h").output["body"] == [1, 2, 3]
    run = _run(
        ws,
        world,
        {"mode": "reel", "path": "/pages", "pagination": {"type": "page", "items_path": "data.items", "max_pages": 2}},
        slug="h4",
    )
    out = run.steps.get(node_id="h").output
    assert len(out["body"]) == 4 and out["truncated"] is True


def test_busy_service_is_retried_after_retry_after_and_post_network_errors_are_not(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "retry",
        [
            node(
                "h",
                "http",
                connection="svc",
                mode="reel",
                path="/flaky",
                retry={"max_attempts": 3, "backoff_seconds": 1},
            )
        ],
        [],
        user=user,
    )
    run = drain(launch(ws, v, user), max_rounds=600)
    sr = run.steps.get(node_id="h")
    assert run.status == "succeeded" and sr.output["status"] == 200 and sr.attempt == 1
    assert run.events.filter(type="http_retry").exists()
    from runs import http_node

    spec = {"method": "POST", "retry": {"max_attempts": 3, "backoff_seconds": 2}}
    net = {"code": "HTTP_ERROR"}
    assert http_node.retry_delay(spec, 1, net, None) is None  # a POST may have been received
    assert http_node.retry_delay({**spec, "method": "GET"}, 1, net, None) == 2.0
    assert http_node.retry_delay(spec, 1, None, {"status": 503, "retry_after": 30}) == 30.0
    assert http_node.retry_delay(spec, 3, None, {"status": 503}) is None  # attempts exhausted


def test_multipart_form_and_text_files(ws, world):
    run = _run(
        ws,
        world,
        {
            "mode": "reel",
            "method": "POST",
            "path": "/up",
            "body_format": "multipart",
            "body": {"titre": "Rapport"},
            "files": [{"field": "fichier", "filename": "r.csv", "content": "a;b\n1;2", "content_type": "text/csv"}],
        },
    )
    sent = SEEN[-1]
    assert run.status == "succeeded" and sent["ctype"].startswith("multipart/form-data; boundary=fluxonata-")
    assert (
        'name="titre"\r\n\r\nRapport' in sent["body"]
        and 'filename="r.csv"' in sent["body"]
        and "Content-Type: text/csv" in sent["body"]
        and "a;b\n1;2" in sent["body"]
    )
    bad = _run(
        ws,
        world,
        {
            "mode": "reel",
            "method": "POST",
            "path": "/up",
            "body_format": "multipart",
            "files": [{"field": 'x"', "filename": "a"}],
        },
        slug="m2",
    )
    assert bad.status == "failed" and bad.error["code"] == "HTTP_CONFIG" and len(SEEN) == 1
    _run(
        ws,
        world,
        {"mode": "reel", "method": "POST", "path": "/f", "body_format": "form", "body": {"a": "1 2", "b": "é"}},
        slug="m3",
    )
    assert SEEN[-1]["ctype"] == "application/x-www-form-urlencoded" and SEEN[-1]["body"] == "a=1+2&b=%C3%A9"
