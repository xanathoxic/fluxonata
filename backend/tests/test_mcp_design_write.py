"""ADR 0025 — outils d'ÉCRITURE du MCP de conception : des brouillons et des propositions, jamais d'effet réel.

Chaque test ferme un chemin : rien ne se publie ni ne se lance, une proposition reste inutilisable, les droits de la personne et les dossiers
privés s'appliquent, les quotas tiennent, les erreurs sont structurées, et tout est audité.
"""

import json
from types import SimpleNamespace

import pytest

from ai.models import AiSkill
from audit.models import AuditEvent
from catalog.models import Action, ActionVersion
from formbuilder.models import Form, FormVersion
from runs.models import Execution, WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, make_action, node, schema
from vault.models import Connection, Secret
from workflows.mcp_design_write import ASSISTANT_TAG
from workflows.models import FolderAccess, FolderGrant, Workflow, WorkflowVersion
from workflows.validation import validate

URL = "/api/v1/workspaces/team-a/mcp/design/"


def rpc(c, method, params=None):
    return c.post(
        URL,
        {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})},
        format="json",
    )


def call(c, tool, args=None):
    r = rpc(c, "tools/call", {"name": tool, "arguments": args or {}})
    assert r.status_code == 200, r.content
    res = r.json()["result"]
    return res, (res.get("structuredContent") if not res["isError"] else None)


def error_text(res) -> str:
    assert res["isError"] is True, res
    return res["content"][0]["text"]


GRAPH = {"inputs": schema(), "nodes": [node("e", action="echo", inputs={"value": "x"})], "edges": [], "outputs": {}}
FORM_DEF = {
    "version": 1,
    "sections": [
        {
            "id": "s1",
            "title": "Demande",
            "fields": [{"key": "nom", "type": "text", "label": "Nom", "required": True}],
        }
    ],
}
ACTION_ARGS = {
    "slug": "compter-voyelles",
    "name": "Compter les voyelles",
    "description": "Compte le nombre de voyelles d'un texte fourni en entrée.",
    "version": {
        "source": "def run(inputs):\n    return {'n': sum(c in 'aeiouy' for c in inputs['texte'].lower())}",
        "input_schema": {
            "type": "object",
            "required": ["texte"],
            "properties": {"texte": {"type": "string", "title": "Texte", "description": "Le texte à analyser."}},
        },
        "output_schema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "title": "Nombre", "description": "Nombre de voyelles trouvées."}},
        },
    },
}
SKILL_ARGS = {
    "slug": "resumer",
    "name": "Résumer un texte",
    "version": {
        "instructions": "Résume en trois phrases : {{ inputs.texte }}",
        "input_schema": {"type": "object", "required": ["texte"], "properties": {"texte": {"type": "string"}}},
        "output_schema": {"type": "object", "required": ["resume"], "properties": {"resume": {"type": "string"}}},
    },
}


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def snapshot():
    return {
        "versions": (WorkflowVersion.objects.count(), FormVersion.objects.count()),
        "runs": (WorkflowRun.objects.count(), Execution.objects.count()),
        "active_actions": Action.objects.filter(status="active").count(),
        "active_skills": AiSkill.objects.filter(status="active").count(),
        "secrets": (Secret.objects.count(), Connection.objects.count()),
    }


# --- workflows ---------------------------------------------------------------------------------------------------------------
def test_create_workflow_makes_a_marked_draft_and_nothing_else(ws, world):
    before, c = snapshot(), client_for(world["designer"])
    res, out = call(c, "create_workflow", {"slug": "arrivee", "name": "Arrivée", "graph": GRAPH, "tags": ["rh"]})
    assert out["status"] == "brouillon" and out["revision"] == 2 and out["issues"] == []
    wf = Workflow.objects.get(slug="arrivee")
    assert (
        wf.draft_graph["nodes"][0]["id"] == "e"
        and set(wf.tags) == {"rh", ASSISTANT_TAG}
        and wf.owner == world["designer"]
    )
    assert not wf.versions.exists() and snapshot() == before  # rien de publié, rien de lancé
    ev = AuditEvent.objects.get(action="mcp.design.write", data__tool="create_workflow")
    assert ev.data["external_assistant"] is True and ev.data["slug"] == "arrivee" and "graph" not in json.dumps(ev.data)


def test_update_workflow_needs_the_current_revision_and_reports_issues(ws, world):
    c = client_for(world["designer"])
    call(c, "create_workflow", {"slug": "w", "name": "W"})
    assert "current_revision" in error_text(
        call(c, "update_workflow", {"slug": "w", "revision": 99, "graph": GRAPH})[0]
    )
    bad = {**GRAPH, "nodes": [node("e", action="n-existe-pas", inputs={})]}
    _, out = call(c, "update_workflow", {"slug": "w", "revision": 1, "graph": bad})
    assert out["revision"] == 2 and "unknown_action" in {
        i["code"] for i in out["issues"]
    }  # l'assistant voit quoi corriger
    _, ok = call(c, "update_workflow", {"slug": "w", "revision": 2, "graph": GRAPH})
    assert ok["revision"] == 3 and ok["issues"] == []
    assert Workflow.objects.get(slug="w").versions.count() == 0


def test_structured_errors_let_the_assistant_correct_itself(ws, world):
    c = client_for(world["designer"])
    msg = error_text(call(c, "create_workflow", {"slug": "pas un slug", "name": ""})[0])
    assert msg.startswith("Refusé : ") and "slug" in msg and "name" in msg
    assert "graph" in error_text(call(c, "create_workflow", {"slug": "g", "name": "G", "graph": "pas un objet"})[0])
    assert not Workflow.objects.filter(slug__in=["g", "pas un slug"]).exists()


def test_validate_workflow_is_read_only_and_works_for_a_viewer(ws, world):
    Workflow.objects.create(workspace=ws, slug="v", name="V", draft_graph=GRAPH)
    v = client_for(world["viewer"])
    assert call(v, "validate_workflow", {"slug": "v"})[1]["issues"] == []
    assert "unknown_action" in {
        i["code"]
        for i in call(v, "validate_workflow", {"graph": {**GRAPH, "nodes": [node("e", action="zzz", inputs={})]}})[1][
            "issues"
        ]
    }
    assert Workflow.objects.get(slug="v").draft_revision == 1
    assert "slug" in error_text(call(v, "validate_workflow", {})[0])


# --- droits, jetons, dossiers privés --------------------------------------------------------------------------------------------
def test_a_person_who_cannot_edit_cannot_write(ws, world):
    before, v = snapshot(), client_for(world["viewer"])
    for tool, args in [
        ("create_workflow", {"slug": "x", "name": "X"}),
        ("create_form", {"slug": "x", "name": "X"}),
        ("propose_action", ACTION_ARGS),
        ("propose_skill", SKILL_ARGS),
    ]:
        assert "modifier" in error_text(call(v, tool, args)[0]), tool
    assert not Workflow.objects.filter(slug="x").exists() and not Form.objects.exists() and snapshot() == before
    assert (
        not Action.objects.filter(slug="compter-voyelles").exists()
        and not AiSkill.objects.filter(slug="resumer").exists()
    )


def test_the_design_profile_refuses_tokens_that_can_run_or_publish(ws, world):
    user = world["designer"]
    for scopes, status in (
        (["view", "edit"], 200),
        (["view", "edit", "run"], 403),
        (["view", "edit", "publish"], 403),
        (["view"], 200),
    ):
        user._token = SimpleNamespace(scopes=scopes, workspace_id=ws.id, workspace=ws, environments=[])
        assert rpc(client_for(user), "tools/list").status_code == status, scopes
    user._token = SimpleNamespace(
        scopes=["view"], workspace_id=ws.id, environments=[]
    )  # lecture seule : lit, n'écrit pas
    assert "modifier" in error_text(call(client_for(user), "create_workflow", {"slug": "y", "name": "Y"})[0])
    assert not Workflow.objects.filter(slug="y").exists()


def test_private_folders_are_respected_when_writing(ws, world):
    Workflow.objects.create(workspace=ws, slug="cache", name="Caché", folder="RH/Prive", draft_graph=GRAPH)
    acc = FolderAccess.objects.create(workspace=ws, folder="RH")
    FolderGrant.objects.create(access=acc, user=world["viewer"], level="view")  # le concepteur n'est PAS dans la liste
    c = client_for(world["designer"])
    assert call(c, "update_workflow", {"slug": "cache", "revision": 1, "graph": GRAPH})[0]["isError"] is True
    assert call(c, "create_workflow", {"slug": "nouveau", "name": "N", "folder": "RH/Prive"})[0]["isError"] is True
    assert (
        Workflow.objects.get(slug="cache").draft_revision == 1 and not Workflow.objects.filter(slug="nouveau").exists()
    )


# --- formulaires -------------------------------------------------------------------------------------------------------------
def test_create_and_update_a_form_draft(ws, world):
    c = client_for(world["designer"])
    assert "definition" in error_text(
        call(c, "create_form", {"slug": "f", "name": "F", "definition": {"sections": "non"}})[0]
    )
    assert not Form.objects.filter(slug="f").exists()
    _, out = call(c, "create_form", {"slug": "demande", "name": "Demande", "definition": FORM_DEF})
    form = Form.objects.get(slug="demande")
    assert out["status"].startswith("brouillon") and ASSISTANT_TAG in form.tags and not form.versions.exists()
    assert "current_revision" in error_text(
        call(c, "update_form", {"slug": "demande", "revision": 7, "definition": FORM_DEF})[0]
    )
    assert (
        call(c, "update_form", {"slug": "demande", "revision": 1, "definition": FORM_DEF, "name": "Demande d'accès"})[
            1
        ]["revision"]
        == 2
    )
    assert Form.objects.get(slug="demande").name == "Demande d'accès"


def test_attach_an_unpublished_form_copies_it_and_a_published_one_is_referenced(ws, world):
    c = client_for(world["designer"])
    g = {**GRAPH, "inputs": schema(["nom"], nom={"type": "string"})}
    call(c, "create_workflow", {"slug": "w", "name": "W", "graph": g})
    call(c, "create_form", {"slug": "demande", "name": "Demande", "definition": FORM_DEF})
    _, out = call(c, "attach_form", {"workflow": "w", "revision": 2, "form": "demande"})
    assert "copie" in out["attached"] and out["issues"] == []
    assert Workflow.objects.get(slug="w").draft_graph["input_form"]["sections"][0]["fields"][0]["key"] == "nom"
    # publié (par une personne) : le rattachement devient une référence à la version exacte
    form = Form.objects.get(slug="demande")
    FormVersion.objects.create(form=form, version="1.0.0", definition=form.draft_definition, digest="sha256:x")
    _, out = call(c, "attach_form", {"workflow": "w", "revision": 3, "form": "demande", "map": {"nom": "nom"}})
    assert "référence à la version 1.0.0" in out["attached"]
    assert Workflow.objects.get(slug="w").draft_graph["input_form"]["ref"] == {"form": "demande", "version": "1.0.0"}


def test_attach_form_refuses_bad_input(ws, world):
    c = client_for(world["designer"])
    call(c, "create_workflow", {"slug": "w", "name": "W", "graph": GRAPH})
    call(c, "create_form", {"slug": "demande", "name": "Demande", "definition": FORM_DEF})
    Form.objects.create(workspace=ws, slug="modele", name="Modèle", is_template=True)
    assert "map" in error_text(
        call(c, "attach_form", {"workflow": "w", "revision": 2, "form": "demande", "map": {"A B": 3}})[0]
    )
    assert "modèle" in error_text(call(c, "attach_form", {"workflow": "w", "revision": 2, "form": "modele"})[0])
    assert call(c, "attach_form", {"workflow": "w", "revision": 2, "form": "absent"})[0]["isError"] is True
    assert "current_revision" in error_text(
        call(c, "attach_form", {"workflow": "w", "revision": 1, "form": "demande"})[0]
    )
    assert "input_form" not in Workflow.objects.get(slug="w").draft_graph


# --- propositions ------------------------------------------------------------------------------------------------------------
def test_a_proposed_action_is_inert_until_a_person_activates_it(ws, world):
    before, c = snapshot(), client_for(world["designer"])
    _, out = call(c, "propose_action", ACTION_ARGS)
    assert out["status"] == "proposed"
    a = Action.objects.get(slug="compter-voyelles")
    assert a.status == "proposed" and ASSISTANT_TAG in a.tags and a.versions.count() == 1 and snapshot() == before
    # absente du catalogue ordinaire ; un workflow qui l'utilise est refusé à la publication
    assert "compter-voyelles" not in {
        r["slug"] for r in client_for(world["viewer"]).get("/api/v1/workspaces/team-a/actions/").json()["results"]
    }
    g = {**GRAPH, "nodes": [node("n", action="compter-voyelles", version="1.0.0", inputs={"texte": "x"})]}
    codes = {i["code"] for i in validate(g, ws, "w") if i["level"] == "error"}
    assert "proposed_action" in codes
    # une proposition encore à relire se révise (nouvelle version) ; une action active ne s'écrase jamais
    assert call(c, "propose_action", ACTION_ARGS)[1]["version"] == "1.1.0"
    Action.objects.filter(slug="compter-voyelles").update(status="active")
    assert "n'est pas une proposition" in error_text(call(c, "propose_action", ACTION_ARGS)[0])
    assert (
        call(c, "propose_action", {**ACTION_ARGS, "slug": "echo", "name": "Echo"})[0]["isError"] is True
    )  # action active existante


def test_a_proposed_action_needs_a_complete_contract_and_no_secret(ws, world):
    c = client_for(world["designer"])
    weak = {**ACTION_ARGS, "description": "court"}
    assert "contract" in error_text(call(c, "propose_action", weak)[0])
    leaky = {
        **ACTION_ARGS,
        "version": {
            **ACTION_ARGS["version"],
            "source": "KEY = '-----BEGIN "
            + "PRIVATE KEY-----'\ndef run(i):\n    return {}",  # scindé : le scanner de secrets du dépôt ignore ainsi ce faux positif
        },
    }
    assert "secret" in error_text(call(c, "propose_action", leaky)[0]).lower()
    broken = {**ACTION_ARGS, "version": {**ACTION_ARGS["version"], "source": "def run(:"}}
    assert "syntaxe" in error_text(call(c, "propose_action", broken)[0])
    assert (
        not Action.objects.filter(slug="compter-voyelles").exists()
        and not ActionVersion.objects.filter(action__slug="compter-voyelles").exists()
    )


def test_a_proposed_skill_is_inert_and_revisable(ws, world):
    c = client_for(world["designer"])
    assert call(c, "propose_skill", SKILL_ARGS)[1]["status"] == "proposed"
    s = AiSkill.objects.get(slug="resumer")
    assert s.status == "proposed" and ASSISTANT_TAG in s.tags
    assert call(c, "propose_skill", SKILL_ARGS)[1]["version"] == "1.1.0"
    AiSkill.objects.filter(slug="resumer").update(status="active")
    assert "n'est pas une proposition" in error_text(call(c, "propose_skill", SKILL_ARGS)[0])


# --- quota, audit, étanchéité ------------------------------------------------------------------------------------------------
def test_the_hourly_write_budget_holds_and_reads_are_not_counted(ws, world, settings):
    settings.ORCH_MCP_DESIGN_WRITES_PER_HOUR = 3
    c = client_for(world["designer"])
    for i in range(3):
        assert call(c, "create_workflow", {"slug": f"w{i}", "name": f"W{i}"})[0]["isError"] is False
    assert "Limite" in error_text(call(c, "create_workflow", {"slug": "w3", "name": "W3"})[0])
    assert not Workflow.objects.filter(slug="w3").exists()
    assert (
        call(c, "list_workflows")[0]["isError"] is False
        and call(c, "validate_workflow", {"slug": "w0"})[0]["isError"] is False
    )
    assert "Limite" not in json.dumps(
        call(client_for(world["viewer"]), "list_workflows")[0]
    )  # le quota est par personne


def test_all_write_tools_together_never_publish_run_or_activate(ws, world):
    before, c = snapshot(), client_for(world["designer"])
    call(c, "create_form", {"slug": "demande", "name": "Demande", "definition": FORM_DEF})
    call(
        c,
        "create_workflow",
        {"slug": "w", "name": "W", "graph": {**GRAPH, "inputs": schema(["nom"], nom={"type": "string"})}},
    )
    call(c, "attach_form", {"workflow": "w", "revision": 2, "form": "demande"})
    call(c, "propose_action", ACTION_ARGS)
    call(c, "propose_skill", SKILL_ARGS)
    call(c, "update_workflow", {"slug": "w", "revision": 3, "graph": GRAPH})
    call(c, "validate_workflow", {"slug": "w"})
    assert snapshot() == before
    assert set(Action.objects.filter(status="proposed").values_list("slug", flat=True)) == {"compter-voyelles"}
    assert (
        AuditEvent.objects.filter(action="mcp.design.write").count() == 6
    )  # validate_workflow ne modifie rien : pas d'écriture auditée


def test_failed_calls_are_traced_without_the_submitted_content(ws, world):
    """Pour relire où un assistant a tâtonné : le message d'erreur est gardé (tronqué), jamais le contenu envoyé."""
    c = client_for(world["designer"])
    call(c, "create_workflow", {"slug": "pas un slug", "name": "N", "description": "texte-confidentiel-xyz"})
    ev = AuditEvent.objects.get(action="mcp.design.error", data__tool="create_workflow")
    assert "slug" in ev.data["message"] and len(ev.data["message"]) <= 200
    assert "texte-confidentiel-xyz" not in json.dumps(ev.data) and ev.data["external_assistant"] is True
    call(c, "create_workflow", {"slug": "ok", "name": "OK"})
    assert AuditEvent.objects.filter(action="mcp.design.error").count() == 1  # un appel réussi n'en laisse pas
