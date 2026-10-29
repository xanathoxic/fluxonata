import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from catalog import integrations
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish, schema
from vault.models import Connection, Secret

LOG: list[dict] = []
ANSWER: dict = {}


class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        parts = urlsplit(self.path)
        LOG.append(
            {
                "method": self.command,
                "path": parts.path,
                "query": parse_qs(parts.query),
                "headers": dict(self.headers),
                "body": body,
            }
        )
        out = ANSWER.get("body", {"ok": True})
        data = json.dumps(out).encode()
        self.send_response(ANSWER.get("status", 200))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
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
    LOG.clear()
    ANSWER.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def _connect(ws, server, kind, secret="tok-123456", **config):
    s, _ = Secret.objects.get_or_create(workspace=ws, name="s")
    s.set_value(secret)
    s.save()
    Connection.objects.all().delete()
    Connection.objects.create(
        workspace=ws, name="svc", label="S", kind=kind, config={"base_url": server, **config}, secret=s
    )


def _step(ws, world, integration, operation, params, slug="i", **extra):
    user = world["designer"]
    v = publish(
        ws,
        slug,
        [
            node(
                "h",
                "http",
                connection="svc",
                mode="reel",
                integration=integration,
                operation=operation,
                params=params,
                **extra,
            )
        ],
        [],
        inputs=schema(),
        user=user,
    )
    run = drain(launch(ws, v, user))
    return run, run.steps.get(node_id="h")


def test_the_catalogue_is_well_formed_and_big_enough():
    assert integrations.check() == []
    assert len(integrations.INTEGRATIONS) >= 20
    assert sum(len(i["operations"]) for i in integrations.INTEGRATIONS) >= 60


def test_the_catalogue_is_served_to_people_who_can_view(ws, world):
    r = client_for(world["viewer"]).get("/api/v1/workspaces/team-a/integrations/")
    assert r.status_code == 200 and {i["slug"] for i in r.data["results"]} >= {"slack", "jira", "github", "msgraph"}


def test_every_operation_renders_with_its_examples():
    for i in integrations.INTEGRATIONS:
        for op in i["operations"]:
            params = {
                p["name"]: p.get("example", "x")
                for p in op["params"]
                if p.get("required") or p.get("example") is not None
            }
            parts = integrations.render(op, params)
            assert parts["path"].startswith("/") and "{" not in parts["path"], (i["slug"], op["id"])


def test_missing_required_parameter_is_reported_in_french():
    _, op = integrations.find("slack", "post_message")
    with pytest.raises(ValueError, match="Canal"):
        integrations.render(op, {"text": "x"})


def test_slack_message_goes_through_and_an_ok_false_answer_fails_the_step(ws, world, server):
    _connect(ws, server, "http_bearer")
    run, sr = _step(ws, world, "slack", "post_message", {"channel": "#ops", "text": "Bonjour"})
    sent = LOG[-1]
    assert (
        run.status == "succeeded"
        and sent["path"] == "/chat.postMessage"
        and sent["headers"]["Authorization"] == "Bearer tok-123456"
    )
    assert json.loads(sent["body"]) == {"channel": "#ops", "text": "Bonjour"}
    ANSWER["body"] = {"ok": False, "error": "channel_not_found"}
    run, sr = _step(ws, world, "slack", "post_message", {"channel": "#x", "text": "t"}, slug="i2")
    assert run.status == "failed" and run.error["code"] == "API_ERROR" and "channel_not_found" in run.error["message"]


def test_path_parameters_are_url_encoded_and_object_params_merge_into_the_body(ws, world, server):
    _connect(ws, server, "basic", username="u")
    run, _ = _step(
        ws,
        world,
        "servicenow",
        "create_record",
        {"table": "inc ident/x", "fields": {"short_description": "Disque plein"}},
    )
    sent = LOG[-1]
    assert sent["path"] == "/table/inc%20ident%2Fx" and json.loads(sent["body"]) == {
        "short_description": "Disque plein"
    }


def test_form_text_and_defaults_bodies(ws, world, server):
    _connect(ws, server, "basic", username="AC1")
    _step(
        ws,
        world,
        "twilio",
        "send_sms",
        {"account_sid": "AC1", "to": "+33600000000", "from_number": "+33100000000", "text": "Salut"},
    )
    sent = LOG[-1]
    assert sent["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(sent["body"]) == {"To": ["+33600000000"], "From": ["+33100000000"], "Body": ["Salut"]}
    _connect(ws, server, "api_key", name="Authorization", prefix="Token ")
    _step(ws, world, "influxdb", "write", {"org": "o", "bucket": "b", "lines": "m,h=a v=1"}, slug="i2")
    sent = LOG[-1]
    assert (
        sent["body"] == "m,h=a v=1"
        and sent["query"]["precision"] == ["s"]
        and sent["headers"]["Authorization"] == "Token tok-123456"
    )
    _connect(ws, server, "http_bearer")
    ANSWER["body"] = {"jsonrpc": "2.0", "result": [], "id": 1}
    _step(ws, world, "zabbix", "problems", {}, slug="i3")
    assert json.loads(LOG[-1]["body"]) == {
        "jsonrpc": "2.0",
        "method": "problem.get",
        "id": 1,
        "params": {"output": "extend", "recent": True, "limit": 50},
    }
    ANSWER["body"] = {"jsonrpc": "2.0", "error": {"code": -32602, "data": "Invalid params"}, "id": 1}
    run, _ = _step(ws, world, "zabbix", "call", {"method": "x", "params": {}}, slug="i4")
    assert run.status == "failed" and "Invalid params" in run.error["message"]


def test_cursor_pagination_of_an_integration_and_secret_prefix_masking(ws, world, server):
    _connect(ws, server, "api_key", name="Authorization", prefix="GenieKey ", secret="geniesecret1")
    ANSWER["body"] = {"data": [], "echo": "GenieKey geniesecret1"}
    run, sr = _step(ws, world, "opsgenie", "list_alerts", {"query": "status:open"})
    assert LOG[-1]["headers"]["Authorization"] == "GenieKey geniesecret1" and "geniesecret1" not in json.dumps(
        sr.output
    )


def test_test_mode_returns_the_sample_without_sending(ws, world, server):
    _connect(ws, server, "http_bearer")
    user = world["designer"]
    v = publish(
        ws,
        "t",
        [node("h", "http", connection="svc", mode="test", integration="slack", operation="list_channels", params={})],
        [],
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert LOG == [] and run.steps.get(node_id="h").output["body"] == {"ok": True, "channels": []}


def test_validator_checks_integration_steps(ws, world, server):
    from workflows.validation import validate_graph

    _connect(ws, server, "http_bearer")

    def codes(**node_fields):
        g = {"inputs": {"type": "object"}, "nodes": [node("h", "http", connection="svc", **node_fields)], "edges": []}
        return {i["code"] for i in validate_graph(g, ws, "t")}

    assert "missing_param" in codes(integration="slack", operation="post_message", params={"text": "x"})
    assert "bad_http" in codes(integration="slack", operation="nope", params={})
    assert "missing_param" not in codes(
        integration="slack", operation="post_message", params={"channel": "c", "text": "x"}
    )
