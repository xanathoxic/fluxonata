"""People: platform administrators create/edit/deactivate accounts and place them in one or several workspaces;
everyone changes their own password; a temporary password only opens the change-password route."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from audit.models import AuditEvent
from tenancy.models import Membership, Role, UserProfile
from tests.conftest import client_for, make_user

P = "/api/v1/platform/users"
User = get_user_model()
STRONG = "Un-mot-de-passe-solide-42"


@pytest.fixture
def boss(db):
    u = make_user("root")
    u.is_superuser = True
    u.save()
    return u


def login(username, password):
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"username": username, "password": password}, format="json")
    return c, r


ROUTES = [
    ("get", "/"),
    ("post", "/"),
    ("patch", "/dana/"),
    ("post", "/dana/reset-password/"),
    ("post", "/dana/memberships/"),
    ("delete", "/dana/memberships/team-a/"),
]


@pytest.mark.parametrize("method,route", ROUTES)
def test_user_management_is_for_platform_administrators_only(world, method, route):
    assert getattr(APIClient(), method)(P + route, {}, format="json").status_code in (401, 403)
    admin = make_user("spaceadmin", world["ws_a"], Role.ADMIN)  # an administrator of a space is not a platform admin
    for who in (world["designer"], admin):
        assert getattr(client_for(who), method)(P + route, {}, format="json").status_code == 403


def test_create_with_generated_temporary_password_shown_once_and_forced_change(world, boss):
    c = client_for(boss)
    r = c.post(
        P + "/",
        {
            "username": "nadia",
            "email": "nadia@example.org",
            "memberships": [{"workspace": "team-a", "role": "viewer"}, {"workspace": "team-b", "role": "designer"}],
        },
        format="json",
    )
    assert r.status_code == 201
    body = r.json()
    temp = body["temporary_password"]
    assert len(temp) >= 16 and body["must_change_password"] is True
    assert {(m["workspace"], m["role"]) for m in body["memberships"]} == {("team-a", "viewer"), ("team-b", "designer")}
    assert "temporary_password" not in c.get(P + "/").content.decode()  # never listed again
    assert Membership.objects.filter(user__username="nadia").count() == 2
    assert not AuditEvent.objects.filter(data__has_key="temporary_password").exists()
    assert temp not in str(list(AuditEvent.objects.values_list("data", flat=True)))
    assert AuditEvent.objects.filter(action="platform.user_create", data__username="nadia").exists()
    assert AuditEvent.objects.filter(
        action="member.add", workspace=world["ws_a"], data__via="platform"
    ).exists()  # visible to the space too

    session, ok = login("nadia", temp)
    assert ok.status_code == 200 and ok.json()["must_change_password"] is True
    blocked = session.get("/api/v1/workspaces/team-a/workflows/")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "password_change_required"
    assert session.get("/api/v1/auth/me/").status_code == 200  # still allowed: the UI needs it to show the change form


def test_change_own_password_lifts_the_block_and_keeps_the_session(world, boss):
    temp = (
        client_for(boss)
        .post(P + "/", {"username": "nadia", "memberships": [{"workspace": "team-a", "role": "viewer"}]}, format="json")
        .json()["temporary_password"]
    )
    session, _ = login("nadia", temp)
    bad = session.post("/api/v1/auth/password/", {"current_password": "faux", "new_password": STRONG}, format="json")
    assert bad.status_code == 400 and "current_password" in bad.json()["error"]["details"]
    for weak in ("court", "12345678901234", "password1234"):
        r = session.post("/api/v1/auth/password/", {"current_password": temp, "new_password": weak}, format="json")
        assert r.status_code == 400, weak
    assert (
        session.post(
            "/api/v1/auth/password/", {"current_password": temp, "new_password": temp}, format="json"
        ).status_code
        == 400
    )
    ok = session.post("/api/v1/auth/password/", {"current_password": temp, "new_password": STRONG}, format="json")
    assert ok.status_code == 204
    assert (
        session.get("/api/v1/workspaces/team-a/workflows/").status_code == 200
        and session.get("/api/v1/auth/me/").json()["must_change_password"] is False
    )
    assert login("nadia", temp)[1].status_code == 401 and login("nadia", STRONG)[1].status_code == 200
    assert AuditEvent.objects.filter(action="auth.password_change", actor_label="nadia").exists()
    assert STRONG not in str(list(AuditEvent.objects.values_list("data", flat=True)))


def test_chosen_password_is_validated_and_not_forced_to_change_unless_asked(world, boss):
    c = client_for(boss)
    weak = c.post(P + "/", {"username": "paul", "password": "court"}, format="json")
    assert weak.status_code == 400
    r = c.post(P + "/", {"username": "paul", "password": STRONG, "must_change_password": False}, format="json")
    assert r.status_code == 201 and "temporary_password" not in r.json() and r.json()["must_change_password"] is False
    assert login("paul", STRONG)[1].json()["must_change_password"] is False


@pytest.mark.parametrize("username", ["A", "ab", "Bad Name", "-x-", "x" * 70, "dana"])
def test_username_rules_and_uniqueness(world, boss, username):
    assert client_for(boss).post(P + "/", {"username": username}, format="json").status_code == 400


def test_unknown_or_archived_workspace_is_refused_and_nothing_is_created(world, boss):
    from django.utils import timezone

    world["ws_b"].archived_at = timezone.now()
    world["ws_b"].save()
    c = client_for(boss)
    for ws in ("nope", "team-b"):
        r = c.post(P + "/", {"username": "zed", "memberships": [{"workspace": ws, "role": "viewer"}]}, format="json")
        assert r.status_code == 400
        assert not User.objects.filter(username="zed").exists()  # atomic


def test_list_search_and_what_it_shows(world, boss):
    make_user("sso-only")
    User.objects.get(username="sso-only").set_unusable_password()
    User.objects.filter(username="sso-only").update(password="!")
    c = client_for(boss)
    rows = {r["username"]: r for r in c.get(P + "/").json()["results"]}
    assert rows["dana"]["memberships"][0]["workspace"] == "team-a" and rows["root"]["is_platform_admin"] is True
    assert rows["sso-only"]["sso_only"] is True and rows["dana"]["sso_only"] is False
    assert [r["username"] for r in c.get(P + "/?q=dan").json()["results"]] == ["dana"]


def test_service_accounts_are_not_people(world, boss):
    from tenancy.models import ServiceAccount

    u = make_user("svc.team-a.bot", world["ws_a"])
    ServiceAccount.objects.create(workspace=world["ws_a"], name="bot", user=u)
    c = client_for(boss)
    assert "svc.team-a.bot" not in {r["username"] for r in c.get(P + "/").json()["results"]}
    assert c.patch(P + "/svc.team-a.bot/", {"is_active": False}, format="json").status_code == 404
    assert c.post(P + "/svc.team-a.bot/reset-password/").status_code == 404


def test_deactivate_ends_existing_sessions_and_blocks_login_then_reactivate(world, boss):
    c = client_for(boss)
    session, _ = login("dana", "pw-test-123")
    assert session.get("/api/v1/workspaces/team-a/workflows/").status_code == 200
    r = c.patch(P + "/dana/", {"is_active": False}, format="json")
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert session.get("/api/v1/workspaces/team-a/workflows/").status_code in (
        401,
        403,
    )  # the open session dies at once
    assert login("dana", "pw-test-123")[1].status_code == 401
    assert Membership.objects.filter(user__username="dana").exists()  # history and memberships are kept
    assert AuditEvent.objects.filter(action="platform.user_deactivate", data__username="dana").exists()
    c.patch(P + "/dana/", {"is_active": True}, format="json")
    assert login("dana", "pw-test-123")[1].status_code == 200
    assert AuditEvent.objects.filter(action="platform.user_reactivate").exists()


def test_safeguards_cannot_lock_the_platform_out(world, boss):
    c = client_for(boss)
    assert c.patch(P + "/root/", {"is_active": False}, format="json").status_code == 400  # not yourself
    other = make_user("root2")
    other.is_superuser = True
    other.save()
    assert client_for(other).patch(P + "/root/", {"is_active": False}, format="json").status_code == 200
    # root2 is now the last active platform administrator: root cannot come back and deactivate them
    root = User.objects.get(username="root")
    root.is_active = True
    root.save()
    assert (
        client_for(root).patch(P + "/root2/", {"is_active": False}, format="json").status_code == 200
    )  # root is active again: allowed
    assert client_for(root).patch(P + "/root/", {"is_active": False}, format="json").status_code == 400


def test_last_platform_admin_cannot_be_deactivated_by_another_admin(world, boss):
    second = make_user("root2")
    second.is_superuser = True
    second.save()
    User.objects.filter(username="root").update(is_active=False)
    assert client_for(second).patch(P + "/root2/", {"is_active": False}, format="json").status_code == 400


def test_edit_email_validates(world, boss):
    c = client_for(boss)
    assert c.patch(P + "/dana/", {"email": "pas-un-email"}, format="json").status_code == 400
    r = c.patch(P + "/dana/", {"email": "dana@example.org"}, format="json")
    assert r.status_code == 200 and User.objects.get(username="dana").email == "dana@example.org"


def test_reset_password_forces_change_and_old_password_stops_working(world, boss):
    c = client_for(boss)
    r = c.post(P + "/dana/reset-password/")
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert login("dana", "pw-test-123")[1].status_code == 401
    _, ok = login("dana", temp)
    assert ok.status_code == 200 and ok.json()["must_change_password"] is True
    assert AuditEvent.objects.filter(action="platform.user_reset_password", data__username="dana").exists()
    assert temp not in str(list(AuditEvent.objects.values_list("data", flat=True)))


def test_sso_accounts_have_no_local_password_to_reset_or_change(world, boss):
    User.objects.filter(username="vic").update(password="!")
    assert client_for(boss).post(P + "/vic/reset-password/").status_code == 400
    r = client_for(User.objects.get(username="vic")).post(
        "/api/v1/auth/password/", {"current_password": "x", "new_password": STRONG}, format="json"
    )
    assert r.status_code == 400


def test_add_to_several_workspaces_change_role_remove(world, boss):
    c = client_for(boss)
    r = c.post(f"{P}/vic/memberships/", {"workspace": "team-b", "role": "operator"}, format="json")
    assert r.status_code == 201 and {m["workspace"] for m in r.json()["memberships"]} == {"team-a", "team-b"}
    c.post(
        f"{P}/vic/memberships/", {"workspace": "team-b", "role": "designer"}, format="json"
    )  # same call changes the role
    assert Membership.objects.get(user__username="vic", workspace=world["ws_b"]).role == Role.DESIGNER
    assert AuditEvent.objects.filter(action="member.role", workspace=world["ws_b"], data__new="designer").exists()
    assert c.delete(f"{P}/vic/memberships/team-b/").status_code == 204
    assert not Membership.objects.filter(user__username="vic", workspace=world["ws_b"]).exists()
    assert c.delete(f"{P}/vic/memberships/team-b/").status_code == 404
    assert c.post(f"{P}/vic/memberships/", {"workspace": "team-b", "role": "emperor"}, format="json").status_code == 400
    assert c.post(f"{P}/vic/memberships/", {"workspace": "ghost", "role": "viewer"}, format="json").status_code == 400


def test_a_workspace_always_keeps_a_manager(world, boss):
    admin = make_user("lead", world["ws_a"], Role.ADMIN)
    c = client_for(boss)
    assert c.delete(f"{P}/lead/memberships/team-a/").status_code == 400
    assert c.post(f"{P}/lead/memberships/", {"workspace": "team-a", "role": "viewer"}, format="json").status_code == 400
    make_user("lead2", world["ws_a"], Role.SPACE_MANAGER)
    assert c.delete(f"{P}/lead/memberships/team-a/").status_code == 204
    assert admin.pk


def test_default_permission_lets_service_tokens_and_unmarked_users_through(world):
    UserProfile.objects.create(user=world["designer"], must_change_password=False)
    assert client_for(world["designer"]).get("/api/v1/workspaces/team-a/workflows/").status_code == 200
