import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from runs.models import WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, make_action, node, publish, schema
from triggers import poller
from triggers.models import PollTrigger
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"
FEED: dict = {"items": []}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps(FEED["body"] if "body" in FEED else {"data": FEED["items"]}).encode()
        self.send_response(FEED.get("status", 200))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    FEED.clear()
    FEED["items"] = []
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def ws(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="tok")
    s.set_value("tok-123456")
    s.save()
    Connection.objects.create(
        workspace=ws, name="svc", label="S", kind="http_bearer", config={"base_url": server}, secret=s
    )
    publish(
        ws,
        "traite",
        [node("e", action="echo", inputs={"value": "{{ inputs.item.titre }}"})],
        [],
        inputs=schema(["item"], item={"type": "object"}),
        user=world["designer"],
    )
    publish(
        ws,
        "lot",
        [node("e", action="echo", inputs={"value": "{{ len(inputs.items) }}"})],
        [],
        inputs=schema(["items"], items={"type": "array"}),
        user=world["designer"],
    )
    return ws


def _create(world, **extra):
    body = {
        "name": "Nouveaux tickets",
        "workflow": "traite",
        "environment": "test",
        "interval_seconds": 60,
        "request": {"connection": "svc", "method": "GET", "path": "/tickets"},
        "items_path": "data",
        "id_path": "id",
        **extra,
    }
    r = client_for(world["designer"]).post(f"{BASE}/poll-triggers/", body, format="json")
    assert r.status_code == 201, r.data
    return PollTrigger.objects.get(pk=r.data["id"])


def _poll(t):
    t = PollTrigger.objects.get(pk=t.pk)
    poller.poll_once(t)
    return PollTrigger.objects.get(pk=t.pk)


def test_first_poll_memorises_then_only_new_items_start_runs(ws, world):
    FEED["items"] = [{"id": 1, "titre": "a"}, {"id": 2, "titre": "b"}]
    t = _create(world)
    t = _poll(t)
    assert t.last_ok and "mémorisé" in t.last_note and WorkflowRun.objects.filter(workspace=ws).count() == 0
    FEED["items"] += [{"id": 3, "titre": "c"}]
    t = _poll(t)
    runs = list(WorkflowRun.objects.filter(workspace=ws))
    assert (
        len(runs) == 1
        and runs[0].inputs == {"item": {"id": 3, "titre": "c"}}
        and runs[0].requested_by == world["designer"]
    )
    assert drain(runs[0]).status == "succeeded"
    t = _poll(t)
    assert WorkflowRun.objects.filter(workspace=ws).count() == 1  # nothing new: nothing started
    assert t.next_poll_at > t.last_poll_at


def test_fire_existing_and_batch_mode_and_preview(ws, world):
    FEED["items"] = [{"id": 1, "titre": "a"}, {"id": 2, "titre": "b"}]
    t = _create(world, fire_existing=True, mode="batch", workflow="lot")
    PollTrigger.objects.filter(pk=t.pk).update(preview_next=True)
    t = _poll(t)
    assert (
        "seraient lancées" in t.last_note and WorkflowRun.objects.filter(workspace=ws).count() == 0 and t.seen_ids == []
    )
    t = _poll(t)
    run = WorkflowRun.objects.get(workspace=ws)
    assert run.inputs == {"items": [{"id": 1, "titre": "a"}, {"id": 2, "titre": "b"}]}


def test_errors_are_reported_and_the_trigger_stays_scheduled(ws, world):
    FEED["items"] = []
    t = _create(world)
    FEED["status"] = 500
    t = _poll(t)
    assert t.last_ok is False and "500" in t.last_note and t.next_poll_at is not None
    FEED["status"] = 200
    FEED["body"] = {"autre": []}
    t = _poll(t)
    assert t.last_ok is False and "liste" in t.last_note
    FEED["body"] = {"data": [{"titre": "sans id"}]}
    t = _poll(t)
    assert t.last_ok is False and "identifiant" in t.last_note


def test_creation_is_validated(ws, world):
    c = client_for(world["designer"])
    base = {
        "name": "x",
        "workflow": "traite",
        "interval_seconds": 60,
        "request": {"connection": "svc", "path": "/t"},
        "id_path": "id",
    }
    assert c.post(f"{BASE}/poll-triggers/", {**base, "interval_seconds": 5}, format="json").status_code == 400
    assert (
        c.post(
            f"{BASE}/poll-triggers/", {**base, "request": {"connection": "absente", "path": "/t"}}, format="json"
        ).status_code
        == 400
    )
    assert (
        c.post(f"{BASE}/poll-triggers/", {**base, "workflow": "lot"}, format="json").status_code == 400
    )  # needs « item »
    assert c.post(f"{BASE}/poll-triggers/", {**base, "id_path": ""}, format="json").status_code == 400
    assert client_for(world["viewer"]).post(f"{BASE}/poll-triggers/", base, format="json").status_code == 403


def test_poll_now_and_claiming(ws, world):
    t = _create(world)
    c = client_for(world["designer"])
    PollTrigger.objects.filter(pk=t.pk).update(next_poll_at=None)
    assert poller.claim_due() is None
    assert c.post(f"{BASE}/poll-triggers/{t.pk}/poll-now/", {"preview": True}, format="json").status_code == 202
    claimed = poller.claim_due()
    assert claimed is not None and claimed.preview_next is True
    assert poller.claim_due() is None  # leased: another worker does not take it
