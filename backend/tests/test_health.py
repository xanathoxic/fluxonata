import json
import logging
from datetime import timedelta

from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from core import health
from core.logging import JsonFormatter, bind
from runs import registry
from runs.models import ServiceInstance
from tenancy.models import Role
from tests.conftest import client_for, make_user

BASE = "/api/v1/workspaces/team-a"


def test_liveness_and_readiness_are_distinct(db):
    c = APIClient()
    live = c.get("/api/v1/health/live/").json()
    assert live["status"] == "alive" and {"version", "revision", "roles"} <= set(
        live
    )  # AR6.6 : version et rôle annoncés
    r = c.get("/api/v1/health/ready/")
    assert r.status_code == 200 and r.json()["database"] == "up"


def test_readiness_is_503_when_database_is_unreachable(db, monkeypatch):
    class Boom:
        def __enter__(self):
            raise OSError("db down")

        def __exit__(self, *a):
            return False

    c = APIClient()
    with monkeypatch.context() as m:
        m.setattr(connection, "cursor", lambda: Boom())
        assert c.get("/api/v1/health/ready/").status_code == 503
        assert c.get("/api/v1/health/live/").status_code == 200  # still alive: restarting would not help


def test_summary_explains_missing_orchestrator_and_workers(world):
    ws = world["ws_a"]
    items = {i["key"]: i for i in health.summary(ws)}
    assert (
        items["orchestrator"]["status"] == "error" and "ne donne plus signe de vie" in items["orchestrator"]["message"]
    )
    assert items["workers"]["status"] == "error" and items["workers"]["hint"]
    registry._last.clear()
    registry.beat("orchestrator", "orchestrator")
    registry.beat("worker", "w-1")
    items = {i["key"]: i for i in health.summary(ws)}
    assert items["orchestrator"]["status"] == "ok" and items["workers"]["message"] == "1 worker(s) actif(s)."
    ServiceInstance.objects.filter(name="w-1").update(last_seen_at=timezone.now() - timedelta(minutes=5))
    assert {i["key"]: i for i in health.summary(ws)}["workers"]["status"] == "error"  # stale heartbeat


def test_system_health_api_is_admin_only(world):
    admin = make_user("ada", world["ws_a"], Role.ADMIN)
    assert client_for(world["designer"]).get(f"{BASE}/system/health/").status_code == 403
    assert client_for(world["outsider"]).get(f"{BASE}/system/health/").status_code == 404
    r = client_for(admin).get(f"{BASE}/system/health/").json()
    assert r["status"] in ("ok", "warn", "error") and all({"label", "status", "message"} <= set(i) for i in r["items"])
    # the process list is platform-level: a workspace administrator no longer sees the shared worker fleet
    assert client_for(admin).get(f"{BASE}/system/workers/").status_code == 404


def test_metrics_disabled_without_token_and_protected_with_one(db, settings):
    c = APIClient()
    settings.ORCH_METRICS_TOKEN = ""
    assert c.get("/api/v1/metrics/").status_code == 404
    settings.ORCH_METRICS_TOKEN = "metrics-token-123"
    assert c.get("/api/v1/metrics/").status_code == 401
    assert c.get("/api/v1/metrics/", HTTP_AUTHORIZATION="Bearer wrong").status_code == 401
    r = c.get("/api/v1/metrics/", HTTP_AUTHORIZATION="Bearer metrics-token-123")
    assert r.status_code == 200 and b"fluxonata_executions" in r.content and b"fluxonata_workers_alive" in r.content


def test_json_logs_carry_correlation_ids():
    rec = logging.LogRecord("t", logging.INFO, "x", 1, "hello %s", ("world",), None)
    with bind(run_id="r-1", correlation_id="c-1"):
        from core.logging import ContextFilter

        ContextFilter().filter(rec)
        out = json.loads(JsonFormatter().format(rec))
    assert (
        out["msg"] == "hello world"
        and out["run_id"] == "r-1"
        and out["correlation_id"] == "c-1"
        and out["level"] == "INFO"
    )


def test_request_id_is_echoed_or_generated_and_sanitised(db):
    c = APIClient()
    assert c.get("/api/v1/health/live/", HTTP_X_REQUEST_ID="abc-123")["X-Request-ID"] == "abc-123"
    generated = c.get("/api/v1/health/live/")["X-Request-ID"]
    assert len(generated) == 32
    evil = c.get("/api/v1/health/live/", HTTP_X_REQUEST_ID="x\r\nSet-Cookie: a=b")["X-Request-ID"]
    assert "\n" not in evil and evil != "x\r\nSet-Cookie: a=b"


# --- AR6.6 : version et rôle visibles ; signaux d'autoscaling -----------------------------------------------------------------------
def test_live_announces_version_revision_and_role(client, settings):
    settings.ORCH_VERSION, settings.ORCH_REVISION, settings.ORCH_ROLES = "0.9.1", "abc1234", "ingest"
    body = client.get("/api/v1/health/live/").json()
    assert body == {"status": "alive", "version": "0.9.1", "revision": "abc1234", "roles": "ingest"}
    settings.ORCH_ROLES = ""
    assert client.get("/api/v1/health/live/").json()["roles"] == "all"


def test_metrics_expose_the_queue_age_instances_and_build_info(client, settings, db):
    from django.utils import timezone

    from runs.models import ServiceInstance

    settings.ORCH_METRICS_TOKEN, settings.ORCH_VERSION, settings.ORCH_REVISION = "t0ken", "0.9.1", "abc1234"
    ServiceInstance.objects.create(kind="worker-aux", name="a", hostname="h", last_seen_at=timezone.now())
    text = client.get("/api/v1/metrics/", HTTP_AUTHORIZATION="Bearer t0ken").content.decode()
    assert "fluxonata_queue_oldest_age_seconds 0" in text
    assert (
        'fluxonata_service_instances{kind="worker-aux"} 1' in text
        and 'fluxonata_service_instances{kind="orchestrator"} 0' in text
    )
    assert 'fluxonata_build_info{version="0.9.1",revision="abc1234",roles="all"} 1' in text
