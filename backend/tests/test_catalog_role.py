import pytest

from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import publish

BASE = "/api/v1/workspaces/team-a"
pytestmark = pytest.mark.django_db
NODES = [{"id": "a", "type": "wait", "name": "Pause", "seconds": 1}]
INPUTS = {"type": "object", "properties": {"sujet": {"type": "string"}, "mdp": {"type": "string"}}, "required": []}


def _offer(ws, slug):
    publish(ws, slug, NODES, [], inputs=INPUTS)
    ws.workflows.filter(slug=slug).update(in_catalog=True)


def _setup(world):
    ws = world["ws_a"]
    admin = client_for(make_user("ada", ws, Role.ADMIN))
    for slug in ("ouvert", "ferme"):
        _offer(ws, slug)
    cathy_u = make_user("cathy", ws, Role.CATALOG)
    carl_u = make_user("carl", ws, Role.CATALOG)
    g = admin.post(f"{BASE}/groups/", {"name": "Compta", "members": ["cathy"], "workflows": ["ouvert"]}, format="json")
    assert g.status_code == 201, g.data
    return admin, client_for(world["designer"]), client_for(cathy_u), client_for(carl_u)


def test_catalog_role_sees_and_launches_only_granted_items(world):
    admin, d, cathy, carl = _setup(world)
    names = [r["slug"] for r in cathy.get(f"{BASE}/catalog/").data["results"]]
    assert names == ["ouvert"] and cathy.get(f"{BASE}/catalog/").data["can_run"] is True
    assert cathy.get(f"{BASE}/catalog/ferme/").status_code == 404
    assert carl.get(f"{BASE}/catalog/").data["results"] == []  # in no group
    ok = cathy.post(
        f"{BASE}/catalog/ouvert/runs/",
        {"environment": "test", "inputs": {"sujet": "x", "mdp": "s3cret"}},
        format="json",
    )
    assert ok.status_code == 202 and ok.data["workflow"]["slug"] == "ouvert" and "progress" in ok.data
    assert (
        cathy.post(f"{BASE}/catalog/ferme/runs/", {"environment": "test", "inputs": {}}, format="json").status_code
        == 404
    )
    assert (
        carl.post(f"{BASE}/catalog/ouvert/runs/", {"environment": "test", "inputs": {}}, format="json").status_code
        == 404
    )
    assert (
        cathy.post(f"{BASE}/catalog/ouvert/runs/", {"environment": "dev", "inputs": {}}, format="json").status_code
        == 400
    )


def test_catalog_role_is_denied_everywhere_else(world):
    _, d, cathy, _ = _setup(world)
    for url in (
        "workflows/",
        "actions/",
        "workflow-runs/",
        "executions/",
        "control-library/",
        "form-library/",
        "connections/",
        "ai/skills/",
        "folders/?kind=workflow",
        "groups/",
        "members/",
    ):
        assert cathy.get(f"{BASE}/{url}").status_code in (403, 404), url
    assert (
        cathy.post(f"{BASE}/workflows/ouvert/runs/", {"environment": "test", "inputs": {}}, format="json").status_code
        == 403
    )
    assert cathy.post(f"{BASE}/workflows/ouvert/form-lookup/x/", {}, format="json").status_code in (403, 404)


def test_runs_page_shows_only_own_runs_masked_and_reduced(world):
    _, d, cathy, carl = _setup(world)
    run = cathy.post(
        f"{BASE}/catalog/ouvert/runs/",
        {"environment": "test", "inputs": {"sujet": "x", "mdp": "s3cret"}},
        format="json",
    ).data
    assert [r["id"] for r in cathy.get(f"{BASE}/catalog-runs/").data["results"]] == [run["id"]]
    assert carl.get(f"{BASE}/catalog-runs/").data["results"] == []
    assert carl.get(f"{BASE}/catalog-runs/{run['id']}/").status_code == 404  # not even acknowledged
    one = cathy.get(f"{BASE}/catalog-runs/{run['id']}/").data
    assert one["inputs"]["sujet"] == "x" and one["inputs"]["mdp"] != "s3cret" and one["masked_keys"] == ["mdp"]
    assert "s3cret" not in str(one)
    assert not {"steps", "events", "graph", "runtime"} & set(one) and one["can_relaunch"] is True
    assert set(one["progress"]) == {"done", "total"}
    # the generic run API stays closed to this role
    assert cathy.get(f"{BASE}/workflow-runs/{run['id']}/").status_code == 403


def test_group_admin_is_for_administrators_only_and_validates(world):
    admin, d, cathy, _ = _setup(world)
    assert (
        d.get(f"{BASE}/groups/").status_code == 403
        and cathy.post(f"{BASE}/groups/", {"name": "X"}, format="json").status_code == 403
    )
    assert admin.post(f"{BASE}/groups/", {"name": "Compta"}, format="json").status_code == 400  # duplicate
    assert admin.post(f"{BASE}/groups/", {"name": "Z", "members": ["fantome"]}, format="json").status_code == 400
    gid = admin.get(f"{BASE}/groups/").data["results"][0]["id"]
    assert admin.patch(f"{BASE}/groups/{gid}/", {"workflows": ["ouvert", "ferme"]}, format="json").status_code == 200
    assert sorted(r["slug"] for r in cathy.get(f"{BASE}/catalog/").data["results"]) == ["ferme", "ouvert"]
    assert admin.patch(f"{BASE}/groups/{gid}/", {"members": []}, format="json").status_code == 200
    assert cathy.get(f"{BASE}/catalog/").data["results"] == []  # leaving the group removes the rights at once
    assert admin.delete(f"{BASE}/groups/{gid}/").status_code == 204


def test_other_roles_keep_their_catalog_and_runs(world):
    ws = world["ws_a"]
    _offer(ws, "ouvert")
    op = client_for(make_user("olga", ws, Role.OPERATOR))
    assert [r["slug"] for r in op.get(f"{BASE}/catalog/").data["results"]] == [
        "ouvert"
    ]  # groups do not narrow other roles
    assert (
        op.post(f"{BASE}/catalog/ouvert/runs/", {"environment": "test", "inputs": {}}, format="json").status_code == 202
    )
