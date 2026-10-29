import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.utils import timezone

from runs import alerts
from runs.models import AlertDelivery, AlertRule, WorkflowRun
from tenancy.models import Role
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish

BASE = "/api/v1/workspaces/team-a"
RECEIVED: list[dict] = []


class Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        RECEIVED.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def sink(settings):
    srv = HTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    RECEIVED.clear()
    yield f"http://127.0.0.1:{srv.server_port}/hook-secret-path"
    srv.shutdown()


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def _admin(world):
    from tests.conftest import make_user

    return client_for(make_user("ada-admin", world["ws_a"], Role.ADMIN))


def test_failed_run_alert_is_queued_in_the_same_transaction_and_delivered(ws, world, sink):
    c = _admin(world)
    r = c.post(
        f"{BASE}/ops/alerts/",
        {"name": "Prod KO", "event": "run_failed", "environment": "test", "url": sink},
        format="json",
    )
    assert (
        r.status_code == 201 and "hook-secret-path" not in json.dumps(r.data) and r.data["destination"] == "127.0.0.1"
    )
    v = publish(ws, "casse", [node("s", action="boom")], [], user=world["designer"])
    run = drain(launch(ws, v, world["designer"]))
    assert run.status == "failed"
    d = AlertDelivery.objects.get()
    assert "casse" in d.payload["text"] and d.status == "pending"
    assert alerts.deliver_due() == 1 and RECEIVED[0]["text"].startswith("❌ Exécution échouée")
    assert AlertDelivery.objects.get().status == "delivered"
    ok = publish(ws, "bon", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    drain(launch(ws, ok, world["designer"]))
    assert AlertDelivery.objects.count() == 1  # a success alerts nobody


def test_filters_and_failure_rate_rule_with_cooldown(ws, world, sink):
    c = _admin(world)
    c.post(
        f"{BASE}/ops/alerts/",
        {"name": "Autre env", "event": "run_failed", "environment": "prod", "url": sink},
        format="json",
    )
    v = publish(ws, "casse", [node("s", action="boom")], [], user=world["designer"])
    drain(launch(ws, v, world["designer"]))
    assert AlertDelivery.objects.count() == 0  # the rule watches production only
    c.post(
        f"{BASE}/ops/alerts/",
        {
            "name": "Taux",
            "event": "failure_rate",
            "threshold_percent": 50,
            "min_runs": 2,
            "window_minutes": 60,
            "url": sink,
        },
        format="json",
    )
    drain(launch(ws, v, world["designer"]))
    assert alerts.evaluate_rates() == 1
    assert "100 %" in AlertDelivery.objects.latest("id").payload["text"]
    assert alerts.evaluate_rates() == 0  # cooldown


def test_alert_management_is_admin_only_and_validates_the_destination(ws, world, sink):
    assert client_for(world["designer"]).get(f"{BASE}/ops/alerts/").status_code == 403
    c = _admin(world)
    assert (
        c.post(
            f"{BASE}/ops/alerts/", {"name": "x", "event": "run_failed", "url": "http://127.0.0.1:1/x"}, format="json"
        ).status_code
        == 201
    )  # allowlisted here
    assert (
        c.post(f"{BASE}/ops/alerts/", {"name": "x", "event": "run_failed", "url": "ftp://x"}, format="json").status_code
        == 400
    )
    assert c.post(f"{BASE}/ops/alerts/", {"name": "x", "event": "nope", "url": sink}, format="json").status_code == 400
    rule = AlertRule.objects.first()
    assert c.post(f"{BASE}/ops/alerts/{rule.pk}/test/").status_code == 202
    assert c.patch(f"{BASE}/ops/alerts/{rule.pk}/", {"enabled": False}, format="json").data["enabled"] is False
    assert c.delete(f"{BASE}/ops/alerts/{rule.pk}/").status_code == 204


def test_dashboard_figures(ws, world):
    user = world["designer"]
    ok = publish(ws, "bon", [node("e", action="echo", inputs={"value": "x"})], [], user=user)
    bad = publish(ws, "casse", [node("s", action="boom")], [], user=user)
    for _ in range(2):
        drain(launch(ws, ok, user))
    drain(launch(ws, bad, user))
    d = client_for(user).get(f"{BASE}/ops/dashboard/", {"days": 7}).data
    assert d["totals"]["runs"] == 3 and d["totals"]["failed"] == 1 and d["totals"]["failure_rate"] == 33.3
    assert d["top_failing"][0]["workflow"] == "casse" and d["failing_steps"][0] == {
        "workflow": "casse",
        "step": "s",
        "count": 1,
    }
    assert d["per_day"][0]["succeeded"] == 2 and d["durations"]["p50_s"] is not None and "tokens" in d["ai"]
    assert client_for(world["viewer"]).get(f"{BASE}/ops/dashboard/").status_code == 200


def test_retention_deletes_old_finished_runs_only(ws, world):
    user = world["designer"]
    ok = publish(ws, "bon", [node("e", action="echo", inputs={"value": "x"})], [], user=user)
    old, recent = drain(launch(ws, ok, user)), drain(launch(ws, ok, user))
    WorkflowRun.objects.filter(pk=old.pk).update(finished_at=timezone.now() - timedelta(days=40))
    c = _admin(world)
    assert c.put(f"{BASE}/ops/retention/", {"retention_days": 0}, format="json").status_code == 400
    assert c.put(f"{BASE}/ops/retention/", {"retention_days": 30}, format="json").data == {"retention_days": 30}
    assert alerts.apply_retention() == 1
    assert list(WorkflowRun.objects.filter(workspace=ws).values_list("pk", flat=True)) == [recent.pk]
    assert c.put(f"{BASE}/ops/retention/", {"retention_days": None}, format="json").status_code == 200
    assert alerts.apply_retention() == 0
