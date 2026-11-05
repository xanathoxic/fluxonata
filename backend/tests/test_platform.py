from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from runs.models import ServiceInstance
from tenancy.models import Membership, Role, Workspace
from tests.conftest import client_for, make_user
from tests.test_triggers import create_hook, mk_schedule, post, signed
from tests.wf_helpers import STR, make_action, node, publish, schema
from triggers import scheduler

P = "/api/v1/platform"
User = get_user_model()


@pytest.fixture
def space(world):
    w = world["ws_a"]
    make_action(w, "echo")
    publish(
        w,
        "wf1",
        [node("a", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v=STR),
        user=world["designer"],
    )
    return w


@pytest.fixture
def boss(db):
    u = make_user("root")
    u.is_superuser = True
    u.save()
    return u


ROUTES = [
    ("get", "/workspaces/"),
    ("post", "/workspaces/"),
    ("patch", "/workspaces/team-a/"),
    ("post", "/workspaces/team-a/admins/"),
    ("get", "/workers/"),
    ("post", "/workers/purge/"),
    ("get", "/admins/"),
    ("post", "/admins/"),
    ("delete", "/admins/root/"),
    ("get", "/egress/"),
    ("post", "/egress/"),
    ("delete", "/egress/1/"),
    ("get", "/audit/"),
]


@pytest.mark.parametrize("method,route", ROUTES)
def test_every_platform_route_refuses_everyone_but_platform_admins(world, method, route):
    anon = getattr(APIClient(), method)(P + route, {}, format="json")
    assert anon.status_code in (401, 403)
    for who in ("designer", "outsider"):  # workspace roles, even an administrator of a space, are not platform rights
        assert getattr(client_for(world[who]), method)(P + route, {}, format="json").status_code == 403
    admin = make_user("spaceadmin", world["ws_a"], Role.ADMIN)
    assert getattr(client_for(admin), method)(P + route, {}, format="json").status_code == 403


def test_service_account_tokens_never_carry_platform_rights(boss):
    boss._token = object()  # what token authentication attaches
    c = APIClient()
    c.force_authenticate(boss)
    assert c.get(f"{P}/workspaces/").status_code == 403


def test_me_exposes_the_flag_only_for_platform_admins(world, boss):
    assert client_for(boss).get("/api/v1/auth/me/").json()["is_platform_admin"] is True
    assert client_for(world["designer"]).get("/api/v1/auth/me/").json()["is_platform_admin"] is False


def test_create_workspace_with_environments_first_admin_and_audit(world, boss):
    c = client_for(boss)
    r = c.post(f"{P}/workspaces/", {"name": "Réseau", "slug": "reseau", "admin_username": "dana"}, format="json")
    assert (
        r.status_code == 201 and r.json()["admins"] == ["dana"] and r.json()["environments"] == ["dev", "prod", "test"]
    )
    ws = Workspace.objects.get(slug="reseau")
    assert Membership.objects.get(workspace=ws, user__username="dana").role == Role.ADMIN
    ev = AuditEvent.objects.get(action="platform.workspace_create")
    assert ev.actor_label == "root" and ev.workspace == ws
    # the new administrator reaches the space, the platform administrator does NOT (no implicit access to data)
    assert client_for(world["designer"]).get("/api/v1/workspaces/reseau/system/health/").status_code == 200
    assert c.get("/api/v1/workspaces/reseau/system/health/").status_code == 404
    # validation: duplicate slug, bad slug, unknown administrator
    assert c.post(f"{P}/workspaces/", {"name": "x", "slug": "reseau"}, format="json").status_code == 400
    assert c.post(f"{P}/workspaces/", {"name": "x", "slug": "Pas valide!"}, format="json").status_code == 400
    assert (
        c.post(f"{P}/workspaces/", {"name": "x", "slug": "ok", "admin_username": "ghost"}, format="json").status_code
        == 400
    )
    # default administrator = the requester
    c.post(f"{P}/workspaces/", {"name": "Solo", "slug": "solo"}, format="json")
    assert Membership.objects.get(workspace__slug="solo", user=boss).role == Role.ADMIN


def test_list_rename_and_slug_stays_stable(world, boss):
    c = client_for(boss)
    rows = {w["slug"]: w for w in c.get(f"{P}/workspaces/").json()["results"]}
    assert {"team-a", "team-b"} <= set(rows) and rows["team-a"]["members"] == 2
    r = c.patch(f"{P}/workspaces/team-a/", {"name": "Équipe A", "slug": "hack"}, format="json")
    assert r.status_code == 200 and r.json()["name"] == "Équipe A" and r.json()["slug"] == "team-a"
    assert c.patch(f"{P}/workspaces/team-a/", {"name": "  "}, format="json").status_code == 400
    assert c.patch(f"{P}/workspaces/nope/", {"name": "x"}, format="json").status_code == 404
    assert AuditEvent.objects.filter(action="platform.workspace_rename").exists()


def test_archive_blocks_access_stops_schedules_and_webhooks_and_restore_brings_back(space, world, boss):
    sch = mk_schedule(world, space)  # due right now
    hook = create_hook(world)
    c = client_for(boss)
    assert c.patch(f"{P}/workspaces/team-a/", {"archived": True}, format="json").json()["archived"] is True
    dana = client_for(world["designer"])
    assert dana.get("/api/v1/workspaces/team-a/workflows/").status_code == 404  # unreachable, existence not revealed
    assert dana.get("/api/v1/auth/me/").json()["workspaces"] == []
    sch.refresh_from_db()
    assert sch.enabled is False and scheduler.fire_due() == 0
    raw, h = signed(hook["secret"], {"v": "x"})
    assert post(hook, raw, h).status_code == 401  # same refusal as any bad signature
    assert c.patch(f"{P}/workspaces/team-a/", {"archived": False}, format="json").json()["archived"] is False
    assert dana.get("/api/v1/workspaces/team-a/workflows/").status_code == 200
    raw, h = signed(hook["secret"], {"v": "y"})
    assert post(hook, raw, h).status_code == 202
    assert {"platform.workspace_archive", "platform.workspace_restore"} <= set(
        AuditEvent.objects.values_list("action", flat=True)
    )


def test_grant_workspace_admin_is_the_recovery_path_and_is_audited(world, boss):
    c = client_for(boss)
    r = c.post(f"{P}/workspaces/team-a/admins/", {"username": "vic"}, format="json")
    assert r.status_code == 200 and "vic" in r.json()["admins"]
    assert Membership.objects.get(workspace=world["ws_a"], user=world["viewer"]).role == Role.ADMIN
    assert c.post(f"{P}/workspaces/team-a/admins/", {"username": "ghost"}, format="json").status_code == 400
    assert AuditEvent.objects.filter(action="platform.workspace_admin_grant", data__user="vic").exists()


def test_worker_fleet_is_platform_level_and_purge_keeps_live_processes(world, boss):
    old = timezone.now() - timedelta(hours=2)
    ServiceInstance.objects.create(kind="worker", name="w-dead", hostname="h1", pid=1, last_seen_at=old)
    ServiceInstance.objects.create(kind="worker", name="w-live", hostname="h2", pid=2, last_seen_at=timezone.now())
    c = client_for(boss)
    rows = {w["name"]: w for w in c.get(f"{P}/workers/").json()["results"]}
    assert rows["w-live"]["alive"] is True and rows["w-dead"]["alive"] is False
    assert c.post(f"{P}/workers/purge/").json() == {"removed": 1}
    assert set(ServiceInstance.objects.values_list("name", flat=True)) == {"w-live"}
    assert AuditEvent.objects.filter(action="platform.workers_purge").exists()


def test_platform_admins_can_be_granted_and_revoked_but_never_the_last(world, boss):
    c = client_for(boss)
    assert c.post(f"{P}/admins/", {"username": "dana"}, format="json").status_code == 201
    assert {a["username"] for a in c.get(f"{P}/admins/").json()["results"]} == {"root", "dana"}
    assert c.delete(f"{P}/admins/dana/").status_code == 204
    assert c.delete(f"{P}/admins/root/").status_code == 400  # last one
    assert c.post(f"{P}/admins/", {"username": "ghost"}, format="json").status_code == 400
    assert {"platform.admin_grant", "platform.admin_revoke"} <= set(AuditEvent.objects.values_list("action", flat=True))


def test_service_accounts_cannot_become_platform_admins(world, boss):
    from tenancy.models import ServiceAccount

    sa_user = User.objects.create_user("bot", password=None)
    ServiceAccount.objects.create(user=sa_user, workspace=world["ws_a"], name="bot")
    assert client_for(boss).post(f"{P}/admins/", {"username": "bot"}, format="json").status_code == 400


def test_platform_audit_lists_only_platform_events(world, boss):
    c = client_for(boss)
    c.post(f"{P}/workspaces/", {"name": "Z", "slug": "z"}, format="json")
    AuditEvent.objects.create(action="action.create", actor_label="dana")
    actions = [e["action"] for e in c.get(f"{P}/audit/").json()["results"]]
    assert "platform.workspace_create" in actions and "action.create" not in actions


def test_cli_grant_revoke_and_last_admin_protection(db):
    make_user("a1")
    call_command("platform_admin", "a1")
    assert User.objects.get(username="a1").is_superuser
    with pytest.raises(CommandError):
        call_command("platform_admin", "a1", "--revoke")  # last platform administrator
    make_user("a2")
    call_command("platform_admin", "a2")
    call_command("platform_admin", "a1", "--revoke")
    assert not User.objects.get(username="a1").is_superuser
    with pytest.raises(CommandError):
        call_command("platform_admin", "nobody")


def test_bootstrap_makes_the_first_administrator_a_platform_admin(db):
    call_command("bootstrap", "--workspace", "prod1", "--admin-user", "first")
    assert User.objects.get(username="first").is_superuser
    call_command("bootstrap", "--workspace", "prod2", "--admin-user", "second", "--no-platform-admin")
    assert not User.objects.get(username="second").is_superuser
