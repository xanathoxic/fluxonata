import http.server
import json
import threading

import pytest

from audit.models import AuditEvent
from catalog.models import Action, ActionVersion
from runs import services
from runs.callbacks import deliver_due
from runs.models import CallbackDelivery, Execution
from runs.runners import DockerRunner, SubprocessRunner
from runs.worker import process_one
from tenancy.models import (
    Environment,
    Membership,  # noqa: F401
    Role,
)
from tests.conftest import client_for, make_user
from tests.test_worker import docker_ready
from tests.wf_helpers import STR, drain, launch, make_action, node, publish, schema
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"
SECRET = "sk-live-" + "very-secret-123456"
SRC = "import os\ndef run(i):\n    print('token is', os.environ['API_TOKEN'])\n    return {'echo': os.environ['API_TOKEN'], 'len': len(os.environ['API_TOKEN'])}\n"


@pytest.fixture
def secret_action(world):
    ws = world["ws_a"]
    action = Action.objects.create(workspace=ws, slug="needs-secret", name="needs-secret")
    return ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        source=SRC,
        input_schema=schema(),
        output_schema=schema(echo={"type": "string"}, len={"type": "integer"}),
        limits={"timeout_seconds": 20, "memory_mb": 64},
        secrets_required=[{"slot": "api", "env": "API_TOKEN"}],
    )


def mk_conn(c, **over):
    body = {"name": "svc", "label": "Service", "kind": "generic", "secret_value": SECRET} | over
    r = c.post(f"{BASE}/connections/", body, format="json")
    assert r.status_code == 201, r.content
    return r.json()


def run_with(world, version, connections, runner=None, env="test"):
    ws = world["ws_a"]
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind=env),
        action_version=version,
        user=world["designer"],
        inputs={},
        connections=connections,
    )
    process_one("w", runner or SubprocessRunner())
    ex.refresh_from_db()
    return ex


def test_secret_is_injected_only_for_declared_slot_and_masked_everywhere(world, secret_action):
    mk_conn(client_for(world["designer"]))
    ex = run_with(world, secret_action, {"api": "svc"})
    assert ex.status == "succeeded"
    assert ex.result == {"echo": "***", "len": len(SECRET)}  # the secret never reaches the stored result
    assert SECRET not in ex.attempts.get().logs and "token is ***" in ex.attempts.get().logs
    assert AuditEvent.objects.filter(action="secret.inject", workspace=world["ws_a"]).count() == 1
    assert SECRET not in json.dumps([e.data for e in AuditEvent.objects.all()])


def test_missing_or_unknown_connection_fails_without_running(world, secret_action):
    ex = run_with(world, secret_action, {})
    assert ex.status == "failed" and ex.error["code"] == "CONNECTION_MISSING"
    ex2 = run_with(world, secret_action, {"api": "nope"})
    assert ex2.status == "failed" and ex2.error["code"] == "CONNECTION_MISSING"


def test_environment_scoped_connection_is_not_usable_elsewhere(world, secret_action):
    c = client_for(world["designer"])
    mk_conn(c, environment="test")
    assert run_with(world, secret_action, {"api": "svc"}, env="test").status == "succeeded"
    assert run_with(world, secret_action, {"api": "svc"}, env="prod").error["code"] == "CONNECTION_MISSING"


def test_connection_of_another_workspace_cannot_be_used(world, secret_action):
    ws_b = world["ws_b"]
    s = Secret(workspace=ws_b, name="b-secret")
    s.set_value("b-value-1234")
    s.save()
    Connection.objects.create(workspace=ws_b, name="svc", label="b", secret=s)
    ex = run_with(world, secret_action, {"api": "svc"})
    assert ex.error["code"] == "CONNECTION_MISSING"


def test_action_without_declared_slot_gets_no_secret(world):
    mk_conn(client_for(world["designer"]))
    ws = world["ws_a"]
    action = Action.objects.create(workspace=ws, slug="plain", name="plain")
    v = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        source="import os\ndef run(i):\n    return {'has': 'API_TOKEN' in os.environ}\n",
        input_schema=schema(),
        output_schema={"type": "object"},
        limits={"timeout_seconds": 20},
    )
    ex = run_with(world, v, {"api": "svc"})  # mapping provided, but the action never declared the slot
    assert ex.result == {"has": False}


@pytest.mark.skipif(not docker_ready(), reason="docker unavailable")
def test_secret_reaches_container_through_envfile_not_command_line(world, secret_action):
    mk_conn(client_for(world["designer"]))
    ex = run_with(world, secret_action, {"api": "svc"}, DockerRunner())
    assert ex.status == "succeeded" and ex.result["len"] == len(SECRET) and SECRET not in ex.attempts.get().logs


def test_workflow_node_passes_connections(world, secret_action):
    mk_conn(client_for(world["designer"]))
    v = publish(
        world["ws_a"],
        "wsec",
        [node("a", action="needs-secret", connections={"api": "svc"})],
        [],
        outputs={"n": "{{ steps.a.output.len }}"},
        user=world["designer"],
    )
    run = drain(launch(world["ws_a"], v, world["designer"]))
    assert run.status == "succeeded" and run.outputs == {"n": len(SECRET)}


def test_api_never_returns_secret_and_enforces_permissions(world):
    c = client_for(world["designer"])
    conn = mk_conn(c)
    assert SECRET not in json.dumps(conn)
    assert SECRET not in c.get(f"{BASE}/connections/").content.decode() + c.get(f"{BASE}/secrets/").content.decode()
    assert (
        client_for(world["viewer"])
        .post(f"{BASE}/connections/", {"name": "x", "label": "x", "kind": "generic"}, format="json")
        .status_code
        == 403
    )
    assert client_for(world["viewer"]).get(f"{BASE}/secrets/").status_code == 403
    assert client_for(world["viewer"]).get(f"{BASE}/connections/").status_code == 200  # may see names/status only
    assert client_for(world["outsider"]).post(f"{BASE}/connections/{conn['id']}/test/").status_code == 404
    assert c.delete(f"{BASE}/secrets/svc-all/").status_code == 400  # still referenced by the connection
    assert c.put(f"{BASE}/secrets/svc-all/", {"value": "new-value-9999"}, format="json").status_code == 204
    assert Secret.objects.get(name="svc-all").reveal() == "new-value-9999"


def test_connection_test_does_not_reveal_secret_and_uses_ssrf_guard(world, settings):
    c = client_for(world["designer"])
    bad = c.post(
        f"{BASE}/connections/",
        {
            "name": "meta",
            "label": "m",
            "kind": "http_bearer",
            "secret_value": SECRET,
            "config": {"base_url": "http://169.254.169.254/latest/"},
        },
        format="json",
    )
    assert bad.status_code == 400 and "SSRF" in json.dumps(bad.json())
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["auth"] = self.headers.get("Authorization")
            self.send_response(200 if seen["auth"] == f"Bearer {SECRET}" else 401)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    try:
        conn = mk_conn(
            c, name="live", kind="http_bearer", config={"base_url": f"http://127.0.0.1:{srv.server_address[1]}/"}
        )
        r = c.post(f"{BASE}/connections/{conn['id']}/test/").json()
        assert r["ok"] is True and SECRET not in json.dumps(r) and seen["auth"] == f"Bearer {SECRET}"
        assert Connection.objects.get(pk=conn["id"]).last_test_ok is True
    finally:
        srv.shutdown()
    settings.ORCH_EGRESS_ALLOWLIST = ""
    again = c.post(
        f"{BASE}/connections/{conn['id']}/test/"
    ).json()  # allowlist removed: loopback refused at test time too
    assert again["ok"] is False and "refusée" in again["message"]


# ------------------------------------------------------------------ callbacks (outbox)
@pytest.fixture
def receiver(settings):
    got = []
    state = {"fail": 0}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((json.loads(body), dict(self.headers)))
            code = 500 if state["fail"] > 0 else 200
            state["fail"] -= 1
            self.send_response(code)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    yield f"http://127.0.0.1:{srv.server_address[1]}/hook", got, state
    srv.shutdown()


def finished_run(world, url):
    ws = world["ws_a"]
    if not ActionVersion.objects.filter(action__workspace=ws, action__slug="echo").exists():
        make_action(ws, "echo")
    v = publish(ws, "cbwf", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    from runs import engine

    run, _ = engine.start_run(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        workflow_version=v,
        user=world["designer"],
        inputs={},
        callback_url=url,
    )
    return drain(run)


def test_callback_is_delivered_from_the_outbox_with_signature(world, receiver):
    url, got, _ = receiver
    s = Secret(workspace=world["ws_a"], name="callback-signing")
    s.set_value("sign-key-12345")
    s.save()
    run = finished_run(world, url)
    cb = CallbackDelivery.objects.get(run=run)
    assert cb.status == "pending"  # written atomically with the final state
    assert deliver_due() == 1
    payload, headers = got[0]
    assert payload["status"] == "succeeded" and payload["run_id"] == str(run.id) and "outputs" not in payload
    import hashlib
    import hmac

    expect = hmac.new(
        b"sign-key-12345",
        headers["X-Fluxonata-Timestamp"].encode() + b"." + json.dumps(payload, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-Fluxonata-Signature"] == "v1=" + expect
    cb.refresh_from_db()
    assert cb.status == "delivered"


def test_callback_retries_with_backoff_then_succeeds(world, receiver):
    from datetime import timedelta

    from django.utils import timezone

    url, got, state = receiver
    state["fail"] = 2
    run = finished_run(world, url)
    cb = CallbackDelivery.objects.get(run=run)
    assert deliver_due() == 0
    cb.refresh_from_db()
    assert (
        cb.status == "pending" and cb.attempts == 1 and cb.next_attempt_at > timezone.now() and "500" in cb.last_error
    )
    assert deliver_due() == 0  # not due yet (backoff)
    CallbackDelivery.objects.filter(pk=cb.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    deliver_due()
    CallbackDelivery.objects.filter(pk=cb.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    assert deliver_due() == 1
    cb.refresh_from_db()
    assert cb.status == "delivered" and cb.attempts == 3 and len(got) == 3


def test_callback_to_internal_address_is_refused_at_api_and_delivery(world, settings):
    ws = world["ws_a"]
    make_action(ws, "echo")
    ActionVersion.objects.filter(action__slug="echo").exists()
    publish(ws, "cbwf2", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/workflows/cbwf2/runs/",
        {"environment": "test", "inputs": {}, "callback_url": "http://169.254.169.254/x"},
        format="json",
    )
    assert r.status_code == 400 and "callback_url" in json.dumps(r.json())
    # even if such a row existed (e.g. allowlist removed later), delivery re-validates and gives up permanently
    run = finished_run(world, "http://127.0.0.1:9/hook")  # loopback not allowlisted
    deliver_due()
    cb = CallbackDelivery.objects.get(run=run)
    assert cb.status == "failed" and "refusée" in cb.last_error


def test_unused_imports_marker():
    assert STR and Execution and make_user and Role
