"""Jetons personnels de l'assistant externe (ADR 0025, amendement) : libre-service depuis « Mon compte », au nom de la personne.

Ce que ces tests ferment : portées plafonnées à view/edit ET par le rôle, durée bornée, valeur montrée une fois (empreinte seule en base),
pas de jeton fabriqué par un jeton, isolation entre personnes et entre espaces, révocation immédiate, rôle perdu = droits perdus.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from tenancy.models import ApiToken, Membership, Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import make_action, node, publish
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a/me/mcp-tokens/"
DESIGN = "/api/v1/workspaces/team-a/mcp/design/"


def bearer(raw):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return c


def mk(c, **body):
    return c.post(BASE, body, format="json")


def rpc(c, method, params=None, url=DESIGN):
    return c.post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, format="json")


def test_designer_gets_a_token_acting_as_themselves(world):
    r = mk(client_for(world["designer"]), name="Mon Claude", hours=2, write=True)
    assert r.status_code == 201, r.content
    body = r.json()
    t = ApiToken.objects.get(pk=body["id"])
    assert t.user == world["designer"] and t.service_account is None
    assert t.scopes == ["view", "edit"] and t.environments == []
    assert abs((t.expires_at - timezone.now()) - timedelta(hours=2)) < timedelta(minutes=1)
    assert body["token"].startswith("orch_") and body["token"] not in t.token_hash
    assert t.token_hash != body["token"]
    # la valeur n'est jamais relue
    listing = client_for(world["designer"]).get(BASE).json()
    assert "token" not in str(listing) and listing["allowed_scopes"] == ["view", "edit"]
    assert [x["id"] for x in listing["results"]] == [t.pk]
    assert AuditEvent.objects.filter(action="token.create", actor=world["designer"]).exists()


def test_token_works_on_design_profile_as_the_person(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    raw = mk(client_for(world["designer"]), write=True).json()["token"]
    c = bearer(raw)
    assert rpc(c, "initialize", {"protocolVersion": "2025-06-18"}).status_code == 200
    tools = rpc(c, "tools/list").json()["result"]["tools"]
    assert "create_workflow" in {x["name"] for x in tools}
    # écrit réellement, au nom de la personne
    r = rpc(c, "tools/call", {"name": "create_workflow", "arguments": {"slug": "brouillon-a", "name": "Brouillon A"}})
    assert r.status_code == 200 and not r.json()["result"].get("isError"), r.content
    assert Workflow.objects.get(workspace=ws, slug="brouillon-a").owner == world["designer"]
    assert AuditEvent.objects.filter(action="mcp.design.write", actor=world["designer"]).exists()


def test_viewer_gets_read_only_and_cannot_ask_for_edit(world):
    c = client_for(world["viewer"])
    assert c.get(BASE).json()["allowed_scopes"] == ["view"]
    assert mk(c, write=True).status_code == 403
    r = mk(c)
    assert r.status_code == 201
    assert ApiToken.objects.get(pk=r.json()["id"]).scopes == ["view"]
    # lecture seule : aucun outil d'écriture ne passe
    b = bearer(r.json()["token"])
    res = rpc(b, "tools/call", {"name": "create_workflow", "arguments": {"slug": "x", "name": "x"}})
    assert res.status_code == 200 and res.json()["result"]["isError"] is True
    assert not Workflow.objects.filter(slug="x").exists()


def test_scopes_never_exceed_view_edit_even_for_admin(world):
    admin = make_user("ada", world["ws_a"], Role.ADMIN)
    r = mk(client_for(admin), write=True, scopes=["run", "publish", "approve"])
    assert r.status_code == 201
    assert ApiToken.objects.get(pk=r.json()["id"]).scopes == ["view", "edit"]


@pytest.mark.parametrize("hours", [0, -1, 169, 100000, "x"])
def test_duration_is_bounded(world, hours):
    assert mk(client_for(world["designer"]), hours=hours).status_code == 400


def test_default_duration_is_short(world):
    r = mk(client_for(world["designer"])).json()
    t = ApiToken.objects.get(pk=r["id"])
    assert t.expires_at - timezone.now() <= timedelta(hours=8, minutes=1)


def test_at_most_five_active_tokens(world):
    c = client_for(world["designer"])
    for i in range(5):
        assert mk(c, name=f"t{i}").status_code == 201
    assert mk(c, name="de-trop").status_code == 400
    first = c.get(BASE).json()["results"][0]["id"]
    assert c.delete(f"{BASE}{first}/").status_code == 204
    assert mk(c, name="ok-apres-revocation").status_code == 201


def test_a_token_cannot_mint_or_list_or_revoke_tokens(world):
    raw = mk(client_for(world["designer"]), write=True).json()["token"]
    b = bearer(raw)
    assert b.post(BASE, {}, format="json").status_code == 403
    assert b.get(BASE).status_code == 403
    assert b.delete(f"{BASE}1/").status_code == 403


def test_people_do_not_see_or_revoke_each_others_tokens(world):
    other = make_user("dora", world["ws_a"], Role.DESIGNER)
    mine = mk(client_for(world["designer"])).json()
    assert client_for(other).get(BASE).json()["results"] == []
    assert client_for(other).delete(f"{BASE}{mine['id']}/").status_code == 404
    assert ApiToken.objects.get(pk=mine["id"]).revoked_at is None


def test_other_workspace_is_invisible(world):
    c = client_for(world["outsider"])
    assert c.get(BASE).status_code == 404
    assert c.post(BASE, {}, format="json").status_code == 404


def test_token_is_bound_to_its_workspace(world):
    raw = mk(client_for(world["designer"]), write=True).json()["token"]
    assert rpc(bearer(raw), "initialize", url="/api/v1/workspaces/team-b/mcp/design/").status_code == 404


def test_revocation_is_immediate(world):
    c = client_for(world["designer"])
    r = mk(c).json()
    assert rpc(bearer(r["token"]), "ping").status_code == 200
    assert c.delete(f"{BASE}{r['id']}/").status_code == 204
    assert rpc(bearer(r["token"]), "ping").status_code == 401
    assert AuditEvent.objects.filter(action="token.revoke", actor=world["designer"]).exists()


def test_expired_token_is_refused(world):
    r = mk(client_for(world["designer"])).json()
    ApiToken.objects.filter(pk=r["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert rpc(bearer(r["token"]), "ping").status_code == 401
    assert client_for(world["designer"]).get(BASE).json()["results"] == []


def test_losing_the_role_loses_the_rights_at_once(world):
    raw = mk(client_for(world["designer"]), write=True).json()["token"]
    Membership.objects.filter(user=world["designer"], workspace=world["ws_a"]).update(role=Role.VIEWER)
    b = bearer(raw)
    res = rpc(b, "tools/call", {"name": "create_workflow", "arguments": {"slug": "x", "name": "x"}})
    assert res.status_code == 200 and res.json()["result"]["isError"] is True
    assert not Workflow.objects.filter(slug="x").exists()
    Membership.objects.filter(user=world["designer"], workspace=world["ws_a"]).delete()
    assert rpc(b, "ping").status_code == 404


def test_deactivated_person_token_is_refused(world):
    raw = mk(client_for(world["designer"])).json()["token"]
    world["designer"].is_active = False
    world["designer"].save()
    assert rpc(bearer(raw), "ping").status_code == 401


def test_personal_token_cannot_run_or_publish_via_execution_profile(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(ws, "wf", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    raw = mk(client_for(world["designer"]), write=True).json()["token"]
    b = bearer(raw)
    r = b.post("/api/v1/workspaces/team-a/workflows/wf/run/", {"inputs": {}, "environment": "test"}, format="json")
    assert r.status_code in (403, 404)


def test_admin_list_shows_personal_tokens_and_cannot_rotate_them(world):
    admin = make_user("ada", world["ws_a"], Role.ADMIN)
    mine = mk(client_for(world["designer"])).json()
    a = client_for(admin)
    rows = a.get("/api/v1/workspaces/team-a/tokens/").json()["results"]
    row = next(x for x in rows if x["id"] == mine["id"])
    assert row["personal"] is True and "dana" in row["service_account"]
    assert a.post(f"/api/v1/workspaces/team-a/tokens/{mine['id']}/rotate/", {}, format="json").status_code == 404
    assert a.delete(f"/api/v1/workspaces/team-a/tokens/{mine['id']}/").status_code == 204  # l'admin peut révoquer
    assert rpc(bearer(mine["token"]), "ping").status_code == 401


def test_token_has_exactly_one_owner(world):
    from django.db import IntegrityError, transaction

    from tenancy.auth import new_token

    raw, prefix, digest = new_token()
    with pytest.raises(IntegrityError), transaction.atomic():
        ApiToken.objects.create(
            workspace=world["ws_a"], name="x", prefix=prefix, token_hash=digest, scopes=[], expires_at=timezone.now()
        )
