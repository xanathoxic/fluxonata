import pytest
from rest_framework.test import APIClient

from tests.conftest import client_for

RUN = {"action": "text-stats", "environment": "test", "inputs": {"text": "bonjour le monde"}}


def test_login_logout_and_me(world):
    c = APIClient(enforce_csrf_checks=True)
    token = c.get("/api/v1/auth/csrf/").json()["csrfToken"]
    bad = c.post("/api/v1/auth/login/", {"username": "dana", "password": "x"}, format="json", HTTP_X_CSRFTOKEN=token)
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    no_csrf = c.post("/api/v1/auth/login/", {"username": "dana", "password": "pw-test-123"}, format="json")
    assert no_csrf.status_code == 403
    ok = c.post(
        "/api/v1/auth/login/", {"username": "dana", "password": "pw-test-123"}, format="json", HTTP_X_CSRFTOKEN=token
    )
    assert ok.status_code == 200 and ok.json()["workspaces"][0]["role"] == "designer"
    assert c.get("/api/v1/auth/me/").status_code == 200


def test_login_is_rate_limited(world, monkeypatch):
    from rest_framework.throttling import ScopedRateThrottle

    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"login": "3/min"})
    c = APIClient()
    codes = [
        c.post("/api/v1/auth/login/", {"username": "dana", "password": "x"}, format="json").status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_anonymous_is_rejected(world):
    assert APIClient().get("/api/v1/workspaces/team-a/actions/").status_code in (401, 403)


def test_catalog_lists_only_own_workspace(world):
    r = client_for(world["designer"]).get("/api/v1/workspaces/team-a/actions/")
    assert r.status_code == 200 and r.json()["results"][0]["latest_version"]["version"] == "1.0.0"
    # cross-workspace access: 404, no existence leak
    assert client_for(world["outsider"]).get("/api/v1/workspaces/team-a/actions/").status_code == 404
    assert client_for(world["outsider"]).get("/api/v1/workspaces/team-a/actions/text-stats/").status_code == 404


def test_run_returns_202_and_validates_inputs(world):
    c = client_for(world["designer"])
    r = c.post("/api/v1/workspaces/team-a/executions/", RUN, format="json")
    assert r.status_code == 202 and r.json()["status"] == "queued" and r.json()["url"].endswith("/")
    bad = c.post("/api/v1/workspaces/team-a/executions/", RUN | {"inputs": {}}, format="json")
    assert bad.status_code == 400 and "text" in bad.json()["error"]["details"]


def test_viewer_cannot_run_but_can_see(world):
    c = client_for(world["viewer"])
    assert c.post("/api/v1/workspaces/team-a/executions/", RUN, format="json").status_code == 403
    assert c.get("/api/v1/workspaces/team-a/executions/").status_code == 200


def test_executions_are_isolated_between_workspaces(world):
    r = client_for(world["designer"]).post("/api/v1/workspaces/team-a/executions/", RUN, format="json")
    exec_id = r.json()["id"]
    other = client_for(world["outsider"])
    assert other.get(f"/api/v1/workspaces/team-a/executions/{exec_id}/").status_code == 404
    # right id, wrong workspace in the URL: still 404
    assert other.get(f"/api/v1/workspaces/team-b/executions/{exec_id}/").status_code == 404
    assert other.post("/api/v1/workspaces/team-a/executions/", RUN, format="json").status_code == 404
    assert other.get("/api/v1/workspaces/team-b/executions/").json()["results"] == []


def test_idempotency_key_returns_same_execution(world):
    c = client_for(world["designer"])
    a = c.post("/api/v1/workspaces/team-a/executions/", RUN, format="json", HTTP_IDEMPOTENCY_KEY="k1")
    b = c.post("/api/v1/workspaces/team-a/executions/", RUN, format="json", HTTP_IDEMPOTENCY_KEY="k1")
    assert a.status_code == 202 and b.status_code == 200 and a.json()["id"] == b.json()["id"]


def test_audit_is_append_only(world):
    from audit.models import AuditEvent, record

    ev = record("x.test")
    with pytest.raises(PermissionError):
        ev.save()
    with pytest.raises(PermissionError):
        ev.delete()
    assert AuditEvent.objects.count() == 1


def test_action_version_is_immutable(world):
    v = world["version_a"]
    v.source = "def run(i): return {}"
    with pytest.raises(PermissionError):
        v.save()


def test_openapi_schema_served(world):
    r = client_for(world["designer"]).get("/api/v1/schema/")
    assert r.status_code == 200 and b"/api/v1/workspaces/" in r.content


def test_workspace_slug_is_globally_unique(world):
    from django.db import IntegrityError, transaction

    from tenancy.models import Organization, Workspace

    other_org = Organization.objects.create(name="autre", slug="autre-org")
    with pytest.raises(IntegrityError), transaction.atomic():
        Workspace.objects.create(
            organization=other_org, name="x", slug="team-a"
        )  # same slug as another org's workspace
