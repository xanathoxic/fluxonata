import pytest
from rest_framework.test import APIClient

from audit.models import AuditEvent
from tenancy.models import Membership, Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import make_action, node, publish

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def admin_ws(world):
    ws = world["ws_a"]
    admin = make_user("ada", ws, Role.ADMIN)
    make_action(ws, "echo")
    publish(ws, "svc-wf", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    return ws, admin


def make_token(admin, **over):
    c = client_for(admin)
    sa = c.post(f"{BASE}/service-accounts/", {"name": "ci-bot", "role": "operator"}, format="json").json()
    body = {
        "service_account": sa["id"],
        "name": "ci",
        "scopes": ["view", "run"],
        "environments": ["test"],
        "days": 7,
    } | over
    r = c.post(f"{BASE}/tokens/", body, format="json")
    assert r.status_code == 201, r.content
    return r.json()["token"], r.json()


def bearer(token):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def test_service_account_token_can_start_a_run_with_idempotency(admin_ws):
    ws, admin = admin_ws
    token, body = make_token(admin)
    assert token.startswith("orch_") and "ne sera plus jamais" in body["notice"]
    c = bearer(token)
    r = c.post(
        f"{BASE}/workflows/svc-wf/runs/", {"environment": "test", "inputs": {}}, format="json", HTTP_IDEMPOTENCY_KEY="k"
    )
    assert r.status_code == 202
    again = c.post(
        f"{BASE}/workflows/svc-wf/runs/", {"environment": "test", "inputs": {}}, format="json", HTTP_IDEMPOTENCY_KEY="k"
    )
    assert again.status_code == 200 and again.json()["id"] == r.json()["id"]
    assert c.get(r.json()["url"]).status_code == 200


def test_token_limits_scope_environment_workspace_and_admin(admin_ws, world):
    ws, admin = admin_ws
    token, _ = make_token(admin, scopes=["view"])
    c = bearer(token)
    assert c.get(f"{BASE}/workflows/").status_code == 200
    assert (
        c.post(f"{BASE}/workflows/svc-wf/runs/", {"environment": "test", "inputs": {}}, format="json").status_code
        == 403
    )  # no run scope
    assert c.get(f"{BASE}/members/").status_code == 403  # never administer
    assert c.get("/api/v1/workspaces/team-b/workflows/").status_code == 404  # other workspace
    token2, _ = make_token_second(admin)
    assert (
        bearer(token2)
        .post(f"{BASE}/workflows/svc-wf/runs/", {"environment": "prod", "inputs": {}}, format="json")
        .status_code
        == 403
    )


def make_token_second(admin):
    c = client_for(admin)
    sa = c.get(f"{BASE}/service-accounts/").json()["results"][0]
    r = c.post(
        f"{BASE}/tokens/",
        {"service_account": sa["id"], "name": "t2", "scopes": ["view", "run"], "environments": ["test"], "days": 1},
        format="json",
    )
    return r.json()["token"], r.json()


def test_revoked_expired_and_unknown_tokens_are_rejected(admin_ws):
    ws, admin = admin_ws
    token, body = make_token(admin)
    c = bearer(token)
    assert c.get(f"{BASE}/workflows/").status_code == 200
    assert client_for(admin).delete(f"{BASE}/tokens/{body['id']}/").status_code == 204
    assert c.get(f"{BASE}/workflows/").status_code == 401
    assert bearer("orch_" + "x" * 40).get(f"{BASE}/workflows/").status_code == 401
    from datetime import timedelta

    from django.utils import timezone

    from tenancy.models import ApiToken

    token2, b2 = make_token_second(admin)
    ApiToken.objects.filter(pk=b2["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert bearer(token2).get(f"{BASE}/workflows/").status_code == 401


def test_token_secret_is_hashed_and_never_listed(admin_ws):
    ws, admin = admin_ws
    token, _ = make_token(admin)
    from tenancy.models import ApiToken

    t = ApiToken.objects.get()
    assert token not in (t.token_hash, t.prefix) and len(t.token_hash) == 64
    listing = client_for(admin).get(f"{BASE}/tokens/").content.decode()
    assert token not in listing and "orch_" in listing  # only the short prefix
    assert not any(token in str(e.data) for e in AuditEvent.objects.all())


def test_only_administrators_manage_tokens_and_members(admin_ws, world):
    c = client_for(world["designer"])
    assert c.post(f"{BASE}/service-accounts/", {"name": "x"}, format="json").status_code == 403
    assert c.get(f"{BASE}/tokens/").status_code == 403
    assert c.get(f"{BASE}/audit/").status_code == 403
    assert client_for(world["outsider"]).get(f"{BASE}/members/").status_code == 404


def test_member_management_and_last_manager_protection(admin_ws, world):
    ws, admin = admin_ws
    c = client_for(admin)
    assert c.post(f"{BASE}/members/", {"username": "otto", "role": "viewer"}, format="json").status_code == 201
    assert c.post(f"{BASE}/members/", {"username": "otto", "role": "viewer"}, format="json").status_code == 400
    assert c.patch(f"{BASE}/members/otto/", {"role": "operator"}, format="json").status_code == 204
    assert Membership.objects.get(user__username="otto", workspace=ws).role == "operator"
    # ada is the only admin/manager: cannot demote or remove herself
    assert c.patch(f"{BASE}/members/ada/", {"role": "viewer"}, format="json").status_code == 400
    assert c.delete(f"{BASE}/members/ada/").status_code == 400
    assert c.delete(f"{BASE}/members/otto/").status_code == 204
    assert AuditEvent.objects.filter(action__startswith="member.", workspace=ws).count() == 3


def test_audit_listing_filters_and_ndjson_export(admin_ws):
    ws, admin = admin_ws
    c = client_for(admin)
    make_token(admin)
    rows = c.get(f"{BASE}/audit/?action=token").json()["results"]
    assert rows and all(r["action"].startswith("token") for r in rows)
    r = c.get(f"{BASE}/audit/export/")
    lines = b"".join(r.streaming_content).decode().strip().splitlines()
    assert r["Content-Type"] == "application/x-ndjson" and len(lines) >= 2
    import json

    assert json.loads(lines[0])["action"]
