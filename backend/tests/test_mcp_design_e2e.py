"""ADR 0025 — scénario COMPLEXE de bout en bout : un assistant construit, par le MCP seul, un workflow riche (script, décision, approbation,
jonction, aiguillage, actions et skill créés à la volée) et un formulaire à champs conditionnels ; une PERSONNE active, publie, puis le workflow
s'exécute pour de vrai. Preuve que ce que le MCP produit est utilisable, pas seulement accepté.

Les étapes « personne » passent par l'API ordinaire, session humaine ; les étapes « assistant » par le seul MCP de conception."""

import json
from types import SimpleNamespace

import pytest

from ai.models import AiSkill
from audit.models import AuditEvent
from catalog.models import Action
from formbuilder.models import Form
from runs.models import Execution
from tests.conftest import client_for
from tests.wf_helpers import drain, launch
from triggers import formengine
from workflows.models import Workflow, WorkflowVersion

BASE = "/api/v1/workspaces/team-a"
URL = f"{BASE}/mcp/design/"


def call(c, tool, args=None):
    r = c.post(
        URL,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}},
        format="json",
    )
    assert r.status_code == 200, r.content
    res = r.json()["result"]
    assert not res["isError"], f"{tool} : {res['content'][0]['text']}"
    return res["structuredContent"]


def S(**props):
    return {"type": "object", "properties": props}


def prop(type_, title, description, **extra):
    return {"type": type_, "title": title, "description": description, **extra}


# --- ce que l'assistant construit ------------------------------------------------------------------------------------------------
ACTION_TOTAL = {
    "slug": "calculer-total",
    "name": "Calculer le total d'une dépense",
    "description": "Calcule le total toutes taxes comprises d'une dépense à partir du montant hors taxe et du taux de TVA.",
    "domain": "finance",
    "version": {
        "source": "def run(inputs):\n    ht = float(inputs['montant'])\n    taux = float(inputs.get('taux', 0.2))\n    return {'total': round(ht * (1 + taux), 2), 'tva': round(ht * taux, 2)}",
        "input_schema": {
            "type": "object",
            "required": ["montant"],
            "properties": {
                "montant": prop("number", "Montant HT", "Le montant hors taxe de la dépense, en euros."),
                "taux": prop("number", "Taux de TVA", "Le taux de TVA appliqué, par exemple 0.2 pour 20 %."),
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["total", "tva"],
            "properties": {
                "total": prop("number", "Total TTC", "Le montant toutes taxes comprises, arrondi au centime."),
                "tva": prop("number", "TVA", "Le montant de la TVA, arrondi au centime."),
            },
        },
    },
}
ACTION_ARCHIVE = {
    "slug": "formater-reference",
    "name": "Formater une référence de dépense",
    "description": "Fabrique la référence lisible d'une dépense à partir du service et du total calculé.",
    "version": {
        "source": "def run(inputs):\n    return {'reference': f\"{inputs['service'].upper()}-{int(inputs['total'] * 100):08d}\"}",
        "input_schema": {
            "type": "object",
            "required": ["service", "total"],
            "properties": {
                "service": prop("string", "Service", "Le service qui porte la dépense."),
                "total": prop("number", "Total", "Le total toutes taxes comprises de la dépense."),
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["reference"],
            "properties": {
                "reference": prop("string", "Référence", "La référence lisible de la dépense, par exemple RH-00012000.")
            },
        },
    },
}
SKILL = {
    "slug": "qualifier-depense",
    "name": "Qualifier une dépense",
    "description": "Classe une dépense selon son motif.",
    "version": {
        "instructions": "Classe cette dépense (motif : {{ inputs.motif }}) en : courante, exceptionnelle ou suspecte.",
        "input_schema": {"type": "object", "required": ["motif"], "properties": {"motif": {"type": "string"}}},
        "output_schema": {"type": "object", "required": ["categorie"], "properties": {"categorie": {"type": "string"}}},
    },
}


def when(field, op, **kw):
    return {"field": field, "op": op, **kw}


FORM = {
    "version": 1,
    "sections": [
        {
            "id": "identite",
            "title": "Qui et combien",
            "fields": [
                {"key": "employe", "type": "text", "label": "Employé", "required": True},
                {
                    "key": "service",
                    "type": "select",
                    "label": "Service",
                    "required": True,
                    "options": [
                        {"value": "rh", "label": "RH"},
                        {"value": "it", "label": "Informatique"},
                        {"value": "finance", "label": "Finance"},
                    ],
                },
                {"key": "montant", "type": "number", "label": "Montant HT (€)", "required": True, "min": 0},
                {"key": "urgent", "type": "checkbox", "label": "Dépense urgente"},
            ],
        },
        {
            "id": "details",
            "title": "Détails conditionnels",
            "fields": [
                # visible seulement si la dépense est urgente, et alors obligatoire
                {
                    "key": "motif_urgence",
                    "type": "textarea",
                    "label": "Motif de l'urgence",
                    "rules": [
                        {
                            "when": when("urgent", "is_true"),
                            "then": [{"do": "show"}, {"do": "require"}],
                            "else": [{"do": "hide"}],
                            "name": "Urgence",
                        }
                    ],
                },
                # obligatoire au-dessus de 1000 €, caché sinon
                {
                    "key": "justificatif",
                    "type": "textarea",
                    "label": "Justificatif",
                    "rules": [
                        {
                            "when": when("montant", "gt", value=1000),
                            "then": [{"do": "show"}, {"do": "require"}],
                            "else": [{"do": "hide"}],
                            "name": "Gros montant",
                        }
                    ],
                },
                # seulement pour la finance ; combine deux conditions
                {
                    "key": "email_validateur",
                    "type": "email",
                    "label": "E-mail du validateur",
                    "rules": [
                        {
                            "when": {
                                "op": "all",
                                "items": [
                                    when("service", "equals", value="finance"),
                                    when("montant", "gte", value=500),
                                ],
                            },
                            "then": [{"do": "show"}, {"do": "require"}],
                            "else": [{"do": "hide"}],
                        }
                    ],
                },
                {"key": "commentaire", "type": "textarea", "label": "Commentaire", "help": "Facultatif."},
            ],
        },
    ],
}
INPUTS = {
    "type": "object",
    "required": ["employe", "service", "montant"],
    "properties": {
        "employe": {"type": "string", "title": "Employé"},
        "service": {"type": "string", "title": "Service"},
        "montant": {"type": "number", "title": "Montant HT"},
        "urgent": {"type": "boolean", "title": "Urgent"},
        "motif_urgence": {"type": "string", "title": "Motif de l'urgence"},
        "justificatif": {"type": "string", "title": "Justificatif"},
        "email_validateur": {"type": "string", "title": "E-mail du validateur"},
        "commentaire": {"type": "string", "title": "Commentaire"},
    },
}


def graph():
    def n(nid, type_, name, **kw):
        return {"id": nid, "type": type_, "name": name, **kw}

    nodes = [
        n(
            "nettoyer",
            "script",
            "Normaliser le nom",
            language="python",
            script="nom_propre = nom.strip().title()\nlongueur = len(nom_propre)\n",
            bindings={"nom": "{{ inputs.employe }}"},
            outputs=[{"name": "nom_propre", "type": "string"}, {"name": "longueur", "type": "integer"}],
        ),
        n("gros_montant", "condition", "Montant supérieur à 1000 € ?", expr="inputs.montant > 1000"),
        n(
            "approbation",
            "approval",
            "Validation d'un gros montant",
            title="Valider la dépense de {{ steps.nettoyer.output.nom_propre }}",
        ),
        n("jonction", "join", "Reprise"),
        n(
            "total",
            "action",
            "Calculer le total",
            action="calculer-total",
            version="1.0.0",
            inputs={"montant": "{{ inputs.montant }}"},
        ),
        n(
            "tri",
            "switch",
            "Selon le service",
            cases=[{"id": "rh", "expr": "inputs.service == 'rh'"}, {"id": "it", "expr": "inputs.service == 'it'"}],
        ),
        n(
            "note_rh",
            "script",
            "Note RH",
            language="python",
            script="note = 'RH : ' + nom\n",
            bindings={"nom": "{{ steps.nettoyer.output.nom_propre }}"},
            outputs=[{"name": "note", "type": "string"}],
        ),
        n(
            "note_it",
            "script",
            "Note informatique",
            language="python",
            script="note = 'IT : ' + nom\n",
            bindings={"nom": "{{ steps.nettoyer.output.nom_propre }}"},
            outputs=[{"name": "note", "type": "string"}],
        ),
        n(
            "note_autre",
            "script",
            "Note générale",
            language="python",
            script="note = 'Autre : ' + nom\n",
            bindings={"nom": "{{ steps.nettoyer.output.nom_propre }}"},
            outputs=[{"name": "note", "type": "string"}],
        ),
        n("fin_tri", "join", "Fin de l'aiguillage"),
        n(
            "reference",
            "action",
            "Fabriquer la référence",
            action="formater-reference",
            version="1.0.0",
            inputs={"service": "{{ inputs.service }}", "total": "{{ steps.total.output.total }}"},
        ),
    ]
    edges = [
        {"from": "nettoyer", "to": "gros_montant"},
        {"from": "gros_montant", "to": "approbation", "label": "true"},
        {"from": "gros_montant", "to": "jonction", "label": "false"},
        {"from": "approbation", "to": "jonction"},
        {"from": "jonction", "to": "total"},
        {"from": "total", "to": "tri"},
        {"from": "tri", "to": "note_rh", "label": "rh"},
        {"from": "tri", "to": "note_it", "label": "it"},
        {"from": "tri", "to": "note_autre", "label": "default"},
        {"from": "note_rh", "to": "fin_tri"},
        {"from": "note_it", "to": "fin_tri"},
        {"from": "note_autre", "to": "fin_tri"},
        {"from": "fin_tri", "to": "reference"},
    ]
    outputs = {
        "total": "{{ steps.total.output.total }}",
        "tva": "{{ steps.total.output.tva }}",
        "reference": "{{ steps.reference.output.reference }}",
        "employe": "{{ steps.nettoyer.output.nom_propre }}",
    }
    return {"inputs": INPUTS, "nodes": nodes, "edges": edges, "outputs": outputs}


@pytest.fixture
def assistant(world):
    """Le compte de l'assistant : un jeton de conception (view + edit) lié au concepteur."""
    user = world["designer"]
    user._token = SimpleNamespace(
        scopes=["view", "edit"], workspace_id=world["ws_a"].id, workspace=world["ws_a"], environments=[]
    )
    c = client_for(user)
    yield c
    del user._token


def test_complex_workflow_and_form_built_through_mcp_only_then_activated_published_and_run(world, assistant):
    ws = world["ws_a"]
    designer = type(world["designer"]).objects.get(
        pk=world["designer"].pk
    )  # la PERSONNE : sa propre session, sans le jeton de l'assistant

    # 1. l'assistant s'informe, constate que rien ne convient, et prépare les ressources manquantes (propositions)
    guide = call(assistant, "get_authoring_guide")
    assert "MÉTHODE" in guide["guide"]
    assert call(assistant, "search_actions", {"query": "total"})["count"] == 0
    for tool, args in (("propose_action", ACTION_TOTAL), ("propose_action", ACTION_ARCHIVE), ("propose_skill", SKILL)):
        assert call(assistant, tool, args)["status"] == "proposed"

    # 2. le formulaire (champs, conditions, listes) puis le workflow, rattachés
    form = call(assistant, "create_form", {"slug": "depense", "name": "Demande de dépense", "definition": FORM})
    assert form["status"].startswith("brouillon")
    assert not formengine.check_definition(Form.objects.get(slug="depense").draft_definition)
    created = call(
        assistant, "create_workflow", {"slug": "depense", "name": "Traitement d'une dépense", "graph": graph()}
    )
    codes = {(i["code"], i["level"]) for i in created["issues"]}
    assert codes == {("proposed_action", "error")}, created["issues"]  # seule chose à relire : les propositions
    attached = call(
        assistant, "attach_form", {"workflow": "depense", "revision": created["revision"], "form": "depense"}
    )
    assert attached["issues"] and {i["code"] for i in attached["issues"]} == {"proposed_action"}, attached["issues"]

    # 3. rien d'exécutable ni de publiable à ce stade, quoi que fasse l'assistant
    with pytest.raises(Exception):  # noqa: B017, PT011  (refus du serveur)
        launch(
            ws,
            WorkflowVersion(
                workflow=Workflow.objects.get(slug="depense"), version="0", graph=graph(), input_schema=INPUTS
            ),
            designer,
        )
    h = client_for(designer)
    assert h.post(f"{BASE}/workflows/depense/publish/", {}, format="json").status_code == 400

    # 4. une PERSONNE relit puis active (session humaine) et publie
    for slug in ("calculer-total", "formater-reference"):
        assert h.post(f"{BASE}/actions/{slug}/activate/").status_code == 200
    assert h.post(f"{BASE}/ai/skills/qualifier-depense/activate/").status_code == 200
    assert h.post(f"{BASE}/form-library/depense/publish/", {}, format="json").status_code == 201
    p = h.post(f"{BASE}/workflows/depense/publish/", {"bump": "minor"}, format="json")
    assert p.status_code == 201, p.content
    v = WorkflowVersion.objects.get(workflow__slug="depense")
    assert v.graph["input_form"]["sections"], "le formulaire d'entrée est épinglé dans la version publiée"

    # 5. le workflow s'exécute pour de vrai (scripts, décision, jonction, aiguillage, actions) et produit ce qu'il doit
    run = drain(launch(ws, v, designer, {"employe": "  ada lovelace ", "service": "rh", "montant": 200}))
    assert run.status == "succeeded", run.error
    assert run.outputs == {"total": 240.0, "tva": 40.0, "reference": "RH-00024000", "employe": "Ada Lovelace"}
    steps = {s.node_id: s.status for s in run.steps.all()}
    assert steps["note_rh"] == "succeeded" and steps["total"] == "succeeded"
    assert (
        steps.get("approbation") != "succeeded"
        and steps.get("note_it") != "succeeded"
        and steps.get("note_autre") != "succeeded"
    )  # une seule branche
    assert not Execution.objects.filter(status="failed").exists()
    # l'aiguillage choisit la bonne branche pour chaque service, la décision envoie les gros montants à l'approbation
    for service, branch in (("it", "note_it"), ("finance", "note_autre")):
        other = drain(launch(ws, v, designer, {"employe": "alan", "service": service, "montant": 100}))
        assert other.status == "succeeded" and {s.node_id for s in other.steps.filter(status="succeeded")} >= {
            branch
        }, service
    big = drain(launch(ws, v, designer, {"employe": "grace", "service": "rh", "montant": 5000}), max_rounds=25)
    assert big.status == "running" and big.steps.get(node_id="approbation").status != "succeeded"  # attend une personne
    assert not big.steps.filter(node_id="total").exists() or big.steps.get(node_id="total").status != "succeeded"

    # 6. tout ce que l'assistant a fait est tracé, marqué et borné
    wrote = set(AuditEvent.objects.filter(action="mcp.design.write").values_list("data__tool", flat=True))
    assert wrote == {"propose_action", "propose_skill", "create_form", "create_workflow", "attach_form"}
    assert not AuditEvent.objects.filter(action="mcp.design.write", data__external_assistant=False).exists()
    assert "assistant-externe" in Workflow.objects.get(slug="depense").tags
    assert set(
        Action.objects.filter(slug__in=["calculer-total", "formater-reference"]).values_list("status", flat=True)
    ) == {"active"}
    assert AiSkill.objects.get(slug="qualifier-depense").status == "active"
    assert (
        json.dumps(
            list(AuditEvent.objects.filter(action__startswith="mcp.design").values_list("data", flat=True))
        ).count("ada lovelace")
        == 0
    )
