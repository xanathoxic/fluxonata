"""Suppression d'une action ou d'un workflow : refusée (409 + liste) tant qu'un AUTRE workflow l'appelle — brouillon ou version publiée —, permise sinon.
L'historique d'exécutions n'est jamais effacé en silence."""

import pytest

from audit.models import AuditEvent
from catalog.models import Action
from tests.conftest import client_for
from tests.wf_helpers import drain, launch, node, publish, schema
from workflows.models import Workflow

WS = "/api/v1/workspaces/team-a"


@pytest.fixture
def c(world):
    return client_for(world["designer"])


def draft(ws, slug, nodes, **kw):
    wf, _ = Workflow.objects.get_or_create(workspace=ws, slug=slug, defaults={"name": slug.title()})
    wf.draft_graph = {"inputs": schema(), "nodes": nodes, "edges": [], "outputs": {}}
    for k, v in kw.items():
        setattr(wf, k, v)
    wf.save()
    return wf


def echo(i="e"):
    return node(i, action="echo", inputs={"value": "x"})


# --- actions ---------------------------------------------------------------------------------------------------------------------------
def test_an_unused_action_is_deleted_and_audited(ws, c):
    Action.objects.create(workspace=ws, slug="orpheline", name="Orpheline")
    assert c.get(f"{WS}/actions/orpheline/usage/").json() == {
        "used_by": [],
        "hidden": 0,
        "total": 0,
        "older_versions": [],
        "executions": 0,
        "running": 0,
        "triggers": {},
    }
    assert c.delete(f"{WS}/actions/orpheline/delete/").status_code == 204
    assert not Action.objects.filter(workspace=ws, slug="orpheline").exists()
    assert AuditEvent.objects.filter(action="action.delete").exists()


def test_an_action_called_by_a_draft_or_a_published_version_lists_them_and_stays(ws, world, c):
    draft(ws, "brouillon", [echo()])
    publish(ws, "publie", [echo()], [], user=world["designer"])
    Workflow.objects.filter(slug="publie").update(
        draft_graph={"inputs": schema(), "nodes": [], "edges": [], "outputs": {}}
    )
    r = c.delete(f"{WS}/actions/echo/delete/")
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "in_use" and "2 workflows" in body["message"]
    used = {u["slug"]: u["where"] for u in body["details"]["used_by"]}
    assert used["brouillon"] == ["brouillon"]
    assert used["publie"] == ["v1.0.0"]  # le brouillon de « publie » est vide : seule la version publiée l'appelle
    assert Action.objects.filter(workspace=ws, slug="echo").exists()
    assert c.get(f"{WS}/actions/echo/usage/").json()["total"] == 2


def test_an_action_only_called_by_a_replaced_version_can_be_deleted(ws, world, c):
    """Seul l'usage courant protège : le brouillon et la DERNIÈRE version. Une version plus ancienne, remplacée depuis, ne bloque plus."""
    Action.objects.filter(workspace=ws, slug="log").update(name="Journal")
    publish(ws, "w", [echo()], [], user=world["designer"])  # v1.0.0 appelle « echo »
    publish(
        ws, "w", [node("l", action="log", inputs={"path": "/tmp/x", "label": "x"})], [], user=world["designer"]
    )  # v1.1.0 : plus « echo »
    Workflow.objects.filter(slug="w").update(draft_graph={"inputs": schema(), "nodes": [], "edges": [], "outputs": {}})
    usage = c.get(f"{WS}/actions/echo/usage/").json()
    assert usage["total"] == 0 and usage["used_by"] == []
    assert usage["older_versions"] == [
        {"slug": "w", "name": "w", "versions": ["v1.0.0"]}
    ]  # dit ce qui ne s'exécutera plus
    assert c.delete(f"{WS}/actions/echo/delete/").status_code == 204
    assert not Action.objects.filter(workspace=ws, slug="echo").exists()
    assert Action.objects.filter(
        workspace=ws, slug="log"
    ).exists()  # l'action de la version courante, elle, reste protégée
    assert c.delete(f"{WS}/actions/log/delete/").status_code == 409


def test_the_latest_version_still_protects_and_an_old_version_cannot_start_once_its_action_is_gone(ws, world, c):
    from rest_framework.exceptions import ValidationError

    from runs import engine
    from tenancy.models import Environment

    old = publish(ws, "w", [echo()], [], user=world["designer"])
    publish(ws, "w", [node("l", action="log", inputs={"path": "/tmp/x", "label": "x"})], [], user=world["designer"])
    Workflow.objects.filter(slug="w").update(draft_graph={"inputs": schema(), "nodes": [], "edges": [], "outputs": {}})
    assert c.delete(f"{WS}/actions/echo/delete/").status_code == 204
    with pytest.raises(ValidationError) as exc:
        engine.start_run(
            workspace=ws,
            environment=Environment.objects.get(workspace=ws, kind="test"),
            workflow_version=old,
            user=world["designer"],
            inputs={},
        )
    assert "n'existe plus" in str(exc.value.detail) and "echo" in str(
        exc.value.detail
    )  # une erreur claire, pas une erreur interne


def test_an_agent_tool_and_a_compensation_count_as_uses(ws, c):
    draft(ws, "agent", [node("a", "agent", tools=["log"])])
    assert [u["slug"] for u in c.get(f"{WS}/actions/log/usage/").json()["used_by"]] == ["agent"]
    draft(ws, "agent", [echo()])
    draft(ws, "comp", [node("x", action="echo", inputs={"value": "x"}, compensation={"action": "add", "inputs": {}})])
    assert [u["slug"] for u in c.get(f"{WS}/actions/add/usage/").json()["used_by"]] == ["comp"]


def test_an_action_that_was_executed_is_not_deleted_even_when_unused(ws, world, c):
    version = publish(ws, "w", [echo()], [], user=world["designer"])
    drain(launch(ws, version, world["designer"]))
    # plus aucun workflow n'appelle l'action (ni brouillon ni version publiée) mais l'historique d'exécutions en dépend
    Workflow.objects.filter(slug="w").update(draft_graph={"inputs": schema(), "nodes": [], "edges": [], "outputs": {}})
    type(version).objects.filter(pk=version.pk).update(
        graph={"nodes": [], "edges": []}
    )  # contourne l'immuabilité : cas limite voulu
    assert c.get(f"{WS}/actions/echo/usage/").json()["total"] == 0
    r = c.delete(f"{WS}/actions/echo/delete/")
    assert r.status_code == 400 and "déjà été exécutée" in r.content.decode()
    assert Action.objects.filter(workspace=ws, slug="echo").exists()


def test_deleting_an_action_needs_the_edit_right_and_an_existing_action(ws, world, c):
    Action.objects.create(workspace=ws, slug="orpheline", name="Orpheline")
    assert (
        client_for(world["viewer"]).delete(f"{WS}/actions/orpheline/delete/").status_code == 403
    )  # il faut le droit de publier
    assert client_for(world["outsider"]).delete(f"{WS}/actions/orpheline/delete/").status_code in (403, 404)
    assert c.delete(f"{WS}/actions/absente/delete/").status_code == 404
    assert Action.objects.filter(workspace=ws, slug="orpheline").exists()


# --- workflows -------------------------------------------------------------------------------------------------------------------------
def test_an_unused_workflow_is_deleted_a_used_one_lists_its_callers(ws, world, c):
    draft(ws, "enfant", [echo()])
    assert c.get(f"{WS}/workflows/enfant/usage/").json()["total"] == 0
    draft(ws, "parent", [node("c", "subworkflow", workflow="enfant", inputs={})])
    draft(ws, "boucle", [node("l", "foreach", workflow="enfant", items="inputs.x", item_input="v")])
    draft(ws, "tant-que", [node("t", "while", workflow="enfant", condition="iteration >= 1")])
    r = c.delete(f"{WS}/workflows/enfant/delete/")
    assert r.status_code == 409
    details = r.json()["error"]["details"]
    assert sorted(u["slug"] for u in details["used_by"]) == ["boucle", "parent", "tant-que"] and details["hidden"] == 0
    assert Workflow.objects.filter(slug="enfant").exists()
    for gone in ("parent", "boucle", "tant-que"):
        assert c.delete(f"{WS}/workflows/{gone}/delete/").status_code == 204
    assert c.delete(f"{WS}/workflows/enfant/delete/").status_code == 204
    assert not Workflow.objects.filter(slug="enfant").exists()


def test_a_published_version_of_another_workflow_also_blocks_the_deletion(ws, world, c):
    publish(ws, "enfant", [echo()], [], user=world["designer"])
    publish(ws, "parent", [node("c", "subworkflow", workflow="enfant", inputs={})], [], user=world["designer"])
    Workflow.objects.filter(slug="parent").update(
        draft_graph={"inputs": schema(), "nodes": [], "edges": [], "outputs": {}}
    )  # plus dans le brouillon…
    body = c.delete(f"{WS}/workflows/enfant/delete/").json()["error"]["details"]
    assert body["used_by"][0]["slug"] == "parent" and body["used_by"][0]["where"] == [
        "v1.0.0"
    ]  # … mais dans la version publiée


def test_the_error_and_success_workflow_settings_count_as_uses(ws, c):
    draft(ws, "alerte", [echo()])
    draft(ws, "p", [echo()], error_workflow="alerte")
    r = c.delete(f"{WS}/workflows/alerte/delete/")
    assert r.status_code == 409 and r.json()["error"]["details"]["used_by"][0]["where"] == ["workflow d'erreur"]


def test_a_workflow_does_not_block_its_own_deletion_and_other_guards_remain(ws, world, c):
    publish(ws, "seul", [echo()], [], user=world["designer"])
    drain(launch(ws, Workflow.objects.get(slug="seul").latest_version(), world["designer"]))
    r = c.delete(f"{WS}/workflows/seul/delete/")
    assert r.status_code == 400 and "déjà été exécuté" in r.content.decode()  # l'historique reste protégé


def test_callers_in_a_private_folder_are_counted_but_not_named(ws, world, c):
    from workflows.models import FolderAccess, FolderGrant

    draft(ws, "enfant", [echo()])
    draft(ws, "prive", [node("c", "subworkflow", workflow="enfant", inputs={})], folder="RH")
    draft(ws, "public", [node("c", "subworkflow", workflow="enfant", inputs={})])
    access = FolderAccess.objects.create(workspace=ws, folder="RH")
    FolderGrant.objects.create(
        access=access, user=world["viewer"], level="view"
    )  # le concepteur n'est PAS dans la liste
    details = c.delete(f"{WS}/workflows/enfant/delete/").json()["error"]["details"]
    assert [u["slug"] for u in details["used_by"]] == ["public"] and details["hidden"] == 1  # « prive » n'est pas nommé
    assert "prive" not in str(details) and Workflow.objects.filter(slug="enfant").exists()
