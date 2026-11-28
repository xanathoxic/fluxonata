"""« Débridage total » : sans lui, les outils de publication n'existent pas pour l'assistant ; avec lui, l'assistant publie et active comme le ferait la
personne propriétaire du jeton (rôle, validations, audit), jamais en production et sans toucher aux décisions humaines."""

import json

import pytest

from audit.models import AuditEvent
from catalog.models import Action
from runs.models import WorkflowRun
from tenancy.models import Environment, Role
from tests.conftest import make_user
from tests.test_mcp_design_runs import auto_url, human, token_client
from tests.test_mcp_design_write import ACTION_ARGS, FORM_DEF, SKILL_ARGS, call, error_text, rpc
from tests.wf_helpers import node, schema
from workflows.mcp_design_unrestricted import NAMES
from workflows.models import Workflow, WorkflowVersion
from workflows.validation import validate_graph


@pytest.fixture
def ws(world):
    ws = world["ws_a"]
    Environment.objects.get_or_create(workspace=ws, kind="dev", defaults={"name": "dev"})
    return ws


@pytest.fixture
def c(world, ws):
    user = world["designer"]
    yield token_client(user, ws)
    del user._token


@pytest.fixture
def admin(world, ws):
    return make_user("adele", ws, Role.ADMIN)


TOOLS = {
    "publish_workflow",
    "publish_form",
    "publish_control",
    "activate_action",
    "new_action_version",
    "activate_skill",
}


def open_up(ws, admin_user, unrestricted=True):
    r = human(admin_user).put(auto_url(ws), {"enabled": True, "unrestricted": unrestricted}, format="json")
    assert r.status_code == 200, r.content
    return r.json()


def names(c):
    return {t["name"] for t in rpc(c, "tools/list").json()["result"]["tools"]}


def test_without_the_unbridling_the_publishing_tools_do_not_exist(ws, world, c, admin):
    assert TOOLS == set(NAMES) and not (TOOLS & names(c))
    res, _ = call(c, "publish_workflow", {"slug": "salue"})
    assert "inconnu" in error_text(res)
    assert AuditEvent.objects.filter(action="mcp.design.refused", data__tool="publish_workflow").exists()
    open_up(ws, admin, unrestricted=False)  # la validation automatique seule ne les ouvre pas
    assert not (TOOLS & names(c))


def test_the_setting_needs_the_automatic_validation_and_is_cut_with_it(ws, world, admin):
    on = open_up(ws, admin)
    assert on["enabled"] is True and on["unrestricted"] is True
    assert human(world["designer"]).get(auto_url(ws)).json()["unrestricted"] is True
    off = human(admin).put(auto_url(ws), {"enabled": False, "unrestricted": True}, format="json").json()
    assert off["enabled"] is False and off["unrestricted"] is False
    ev = AuditEvent.objects.filter(action="run_request.auto_setting").order_by("-id").first()
    assert ev.data["unrestricted"] is False


def test_the_tools_appear_when_enabled_and_vanish_when_it_is_cut(ws, world, c, admin):
    open_up(ws, admin)
    assert TOOLS <= names(c)
    human(admin).put(auto_url(ws), {"enabled": True, "unrestricted": False}, format="json")
    assert not (TOOLS & names(c))
    res, _ = call(c, "publish_form", {"slug": "x"})
    assert "inconnu" in error_text(res)


def test_the_assistant_designs_publishes_and_runs_a_workflow_on_its_own(ws, world, c, admin):
    open_up(ws, admin)
    call(c, "create_form", {"slug": "demande", "name": "Demande", "definition": FORM_DEF})
    _, out = call(c, "publish_form", {"slug": "demande", "changelog": "première"})
    assert out["status"] == "publié" and out["version"]
    call(c, "propose_action", ACTION_ARGS)
    assert Action.objects.get(slug="compter-voyelles").status == "proposed"
    _, out = call(c, "activate_action", {"slug": "compter-voyelles"})
    assert out["status"] == "active" and Action.objects.get(slug="compter-voyelles").status == "active"
    graph = {
        "inputs": schema(["texte"], texte={"type": "string"}),
        "nodes": [node("n", action="compter-voyelles", inputs={"texte": "{{ inputs.texte }}"})],
        "edges": [],
        "outputs": {},
    }
    call(c, "create_workflow", {"slug": "voyelles", "name": "Voyelles", "graph": graph})
    res, out = call(c, "publish_workflow", {"slug": "voyelles", "bump": "minor", "changelog": "v1"})
    assert out and out["status"] == "publié", res
    wv = WorkflowVersion.objects.get(workflow__slug="voyelles")
    assert wv.created_by == world["designer"]  # au nom de la personne propriétaire du jeton
    _, run = call(
        c, "request_run", {"slug": "voyelles", "environment": "test", "inputs": {"texte": "Ada"}, "reason": "essai"}
    )
    assert run["automatic"] is True and WorkflowRun.objects.filter(pk=run["run_id"]).exists()
    assert AuditEvent.objects.filter(
        action="mcp.design.write", data__tool="publish_workflow", data__unrestricted=True
    ).exists()


def test_an_active_action_gets_a_new_version_and_a_proposed_skill_is_activated(ws, world, c, admin):
    open_up(ws, admin)
    call(c, "propose_action", ACTION_ARGS)
    call(c, "activate_action", {"slug": "compter-voyelles"})
    v2 = {**ACTION_ARGS["version"], "version": "1.1.0", "changelog": "v2"}
    res, out = call(c, "new_action_version", {"slug": "compter-voyelles", "version": v2})
    assert out and out["version"] == "1.1.0", res
    call(c, "propose_skill", SKILL_ARGS)
    _, out = call(c, "activate_skill", {"slug": "resumer"})
    assert out["status"] == "active"


def test_steps_written_by_the_assistant_and_marked_for_review_are_cleared_at_publication(ws, world, c, admin):
    open_up(ws, admin)
    script = {
        "id": "s",
        "type": "script",
        "name": "S",
        "language": "python",
        "script": "x = 1\n",
        "bindings": {},
        "outputs": [{"name": "x", "type": "integer"}],
    }
    graph = {"inputs": schema(), "nodes": [script], "edges": [], "outputs": {}}
    call(c, "create_workflow", {"slug": "w", "name": "W", "graph": graph})
    wf = Workflow.objects.get(slug="w")
    wf.draft_graph = {**wf.draft_graph, "nodes": [{**n, "ai_review": True} for n in wf.draft_graph["nodes"]]}
    wf.save()
    blocked = [i["code"] for i in validate_graph(wf.draft_graph, ws, "w") if i["level"] == "error"]
    assert blocked == ["ai_review_required"]  # sans débridage, une personne devrait relire ce script
    res, out = call(c, "publish_workflow", {"slug": "w"})
    assert out, res
    assert out["status"] == "publié" and out["steps_marked_reviewed"] == ["s"]
    ev = AuditEvent.objects.get(action="workflow.ai_review_cleared")
    assert ev.data["automatic"] is True and ev.data["unrestricted"] is True


def test_the_rights_are_those_of_the_token_owner_role(ws, world, admin):
    open_up(ws, admin)
    viewer = world["viewer"]
    res, _ = call(token_client(viewer, ws), "publish_form", {"slug": "demande"})
    assert res["isError"] is True and not WorkflowVersion.objects.filter(workflow__slug="x").exists()
    designer_client = token_client(world["designer"], ws)
    call(designer_client, "create_form", {"slug": "f", "name": "F", "definition": FORM_DEF})
    # un lecteur ne publie pas le formulaire d'un autre, même débridé
    res, _ = call(token_client(viewer, ws), "publish_form", {"slug": "f"})
    assert res["isError"] is True and "version" not in json.dumps(res)


def test_an_invalid_workflow_is_refused_like_in_the_application(ws, world, c, admin):
    open_up(ws, admin)
    bad = {"inputs": schema(), "nodes": [node("n", action="inexistante", inputs={})], "edges": [], "outputs": {}}
    call(c, "create_workflow", {"slug": "mauvais", "name": "M", "graph": bad})
    res, _ = call(c, "publish_workflow", {"slug": "mauvais"})
    assert res["isError"] is True and not WorkflowVersion.objects.filter(workflow__slug="mauvais").exists()


def test_the_production_and_the_human_decisions_stay_out_of_reach(ws, world, c, admin):
    open_up(ws, admin)
    listed = names(c)
    assert not [n for n in listed if any(w in n for w in ("promote", "approve", "delete", "secret", "connection_"))]
    res, _ = call(c, "request_run", {"slug": "x", "environment": "prod", "reason": "non"})
    assert res["isError"] is True and WorkflowRun.objects.count() == 0
    assert make_user("zoe", ws, Role.ADMIN)  # un administrateur reste le seul à régler l'option (session humaine)
    assert (
        token_client(admin, ws, scopes=("view", "edit", "administer"))
        .put(auto_url(ws), {"enabled": True, "unrestricted": True}, format="json")
        .status_code
        == 403
    )
