from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from tenancy.models import ApiToken, Role
from tests.conftest import client_for, make_user

BASE = "/api/v1/workspaces/team-a"


def _admin(world):
    return client_for(make_user("ada-admin", world["ws_a"], Role.ADMIN))


def test_rotation_issues_a_new_token_keeps_the_old_one_for_the_overlap_then_expires_it(world):
    c = _admin(world)
    sa = c.post(f"{BASE}/service-accounts/", {"name": "ci", "role": "operator"}, format="json").json()
    made = c.post(
        f"{BASE}/tokens/",
        {"name": "ci", "service_account": sa["id"], "scopes": ["view", "run"], "environments": ["test"], "days": 30},
        format="json",
    ).data
    old = ApiToken.objects.get(pk=made["id"])
    r = c.post(f"{BASE}/tokens/{old.pk}/rotate/", {"overlap_hours": 2}, format="json")
    assert r.status_code == 201 and r.data["token"] != made["token"] and "plus jamais" in r.data["notice"]
    new, old = ApiToken.objects.get(pk=r.data["id"]), ApiToken.objects.get(pk=old.pk)
    assert new.scopes == ["view", "run"] and new.environments == ["test"] and new.service_account_id == sa["id"]
    assert 29 <= (new.expires_at - timezone.now()).days <= 30 and old.expires_at < timezone.now() + timedelta(
        hours=2, minutes=1
    )
    assert (
        APIClient().get(f"{BASE}/workflow-runs/", HTTP_AUTHORIZATION=f"Bearer {made['token']}").status_code == 200
    )  # overlap
    assert APIClient().get(f"{BASE}/workflow-runs/", HTTP_AUTHORIZATION=f"Bearer {r.data['token']}").status_code == 200
    ApiToken.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert APIClient().get(f"{BASE}/workflow-runs/", HTTP_AUTHORIZATION=f"Bearer {made['token']}").status_code in (
        401,
        403,
    )  # overlap over


def test_rotation_rules_admin_only_revoked_and_validation(world):
    c = _admin(world)
    sa = c.post(f"{BASE}/service-accounts/", {"name": "ci2", "role": "operator"}, format="json").json()
    made = c.post(
        f"{BASE}/tokens/",
        {"name": "x", "service_account": sa["id"], "scopes": ["view"], "environments": [], "days": 10},
        format="json",
    ).data
    assert (
        client_for(world["designer"]).post(f"{BASE}/tokens/{made['id']}/rotate/", {}, format="json").status_code == 403
    )
    assert c.post(f"{BASE}/tokens/{made['id']}/rotate/", {"overlap_hours": 999}, format="json").status_code == 400
    c.delete(f"{BASE}/tokens/{made['id']}/")
    assert (
        c.post(f"{BASE}/tokens/{made['id']}/rotate/", {}, format="json").status_code == 404
    )  # a revoked token is not renewed
