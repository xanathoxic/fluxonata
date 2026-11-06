"""ADR 0025 — le guide de conception ne peut pas dériver : ses exemples passent les VRAIS contrôles du serveur."""

import json

import pytest

from ai.skills import SkillCreateSerializer
from catalog.library import ActionCreateSerializer, require_contract
from tests.conftest import client_for
from tests.wf_helpers import STR, make_action, schema
from triggers import formengine
from workflows.mcp_design_guide import (
    ACTION_EXAMPLE,
    BRANCHING_EXAMPLE,
    ERROR_HANDLING_EXAMPLE,
    FORM_EXAMPLE,
    FORM_RULES_EXAMPLE,
    FORM_SOURCE_EXAMPLE,
    OTHER_NODES,
    SKILL_EXAMPLE,
    TRIGGER_INPUTS_EXAMPLES,
    WORKFLOW_EXAMPLE,
    guide,
)
from workflows.validation import NODE_TYPES, validate

URL = "/api/v1/workspaces/team-a/mcp/design/"


def call(c, tool, args=None):
    r = c.post(
        URL,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}},
        format="json",
    )
    return r.json()["result"]


@pytest.fixture
def ws(world):
    # deux actions réelles dont les contrats correspondent à l'exemple du guide (le guide demande de remplacer les <slug>)
    make_action(
        world["ws_a"],
        "echo",
        input_schema=schema(["texte"], texte=STR),
        output_schema=schema(["n"], n={"type": "integer"}),
    )
    make_action(
        world["ws_a"],
        "log",
        input_schema=schema(["value"], value={"type": "integer"}),
        output_schema=schema(["value"], value={"type": "integer"}),
    )
    return world["ws_a"]


def test_the_workflow_example_has_no_error_once_its_placeholders_are_real_actions(ws):
    text = (
        json.dumps(WORKFLOW_EXAMPLE)
        .replace("<slug d'une action existante>", "echo")
        .replace("<slug d'une autre action>", "log")
    )
    issues = validate(json.loads(text), ws, "exemple")
    assert [i for i in issues if i["level"] == "error"] == [], issues


def test_the_form_example_is_valid_and_feeds_the_workflow_example(ws):
    assert not formengine.check_definition(FORM_EXAMPLE)
    assert formengine.check_against_inputs(FORM_EXAMPLE, WORKFLOW_EXAMPLE["inputs"]) == {}


def test_the_action_and_skill_examples_pass_the_real_serializers():
    a = ActionCreateSerializer(data={**ACTION_EXAMPLE, "ai_generated": True, "reviewed": True})
    assert a.is_valid(), a.errors
    require_contract(a.validated_data["description"], a.validated_data["version"])  # contrat complet
    s = SkillCreateSerializer(data={**SKILL_EXAMPLE, "ai_generated": True, "reviewed": True})
    assert s.is_valid(), s.errors


def test_the_guide_lists_every_node_and_field_type_from_the_code():
    text = guide("all")["guide"]
    assert all(t in text for t in NODE_TYPES) and all(t in text for t in formengine.FIELD_TYPES)


def test_topics_and_unknown_topics(ws):
    only = guide("form")
    assert only["topic"] == "form" and only["guide"].startswith("FORMULAIRE") and "WORKFLOW." not in only["guide"]
    assert guide("n'importe quoi")["topic"] == "all"


def test_the_tool_is_readable_by_a_viewer_and_declared_read_only(ws, world):
    res = call(client_for(world["viewer"]), "get_authoring_guide", {"topic": "workflow"})
    assert res["isError"] is False and res["structuredContent"]["examples"]["workflow"]["nodes"]
    tools = (
        client_for(world["viewer"])
        .post(URL, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, format="json")
        .json()["result"]["tools"]
    )
    spec = next(t for t in tools if t["name"] == "get_authoring_guide")
    assert spec["annotations"]["readOnlyHint"] is True and "COMMENCEZ ICI" in spec["description"]


def test_the_branching_example_has_no_error(ws):
    """Script, décision, approbation, jonction, aiguillage : le format enseigné par le guide est celui que le serveur accepte."""
    issues = validate(BRANCHING_EXAMPLE, ws, "exemple")
    assert [i for i in issues if i["level"] == "error"] == [], issues


def test_the_form_rules_example_is_valid_and_its_conditional_fields_may_feed_optional_inputs(ws):
    assert not formengine.check_definition(FORM_RULES_EXAMPLE)
    props = {k: {"type": "string"} for k in ("service", "motif", "mail")} | {
        "montant": {"type": "number"},
        "urgent": {"type": "boolean"},
    }
    assert (
        formengine.check_against_inputs(
            FORM_RULES_EXAMPLE, {"type": "object", "required": ["montant"], "properties": props}
        )
        == {}
    )


def test_the_other_node_examples_pass_the_validator(ws):
    from ai.models import AiProvider, AiSkill, AiSkillVersion

    skill = AiSkill.objects.create(workspace=ws, slug="qualifier", name="Qualifier")
    AiSkillVersion.objects.create(
        skill=skill,
        version="1.0.0",
        instructions="x {{ inputs.motif }}",
        input_schema=schema(["motif"], motif=STR),
        output_schema=schema(["c"], c=STR),
    )
    AiProvider.objects.create(workspace=ws, name="local", kind="fake")
    ai = {**OTHER_NODES["ai (skill actif)"], "skill": "qualifier", "provider": "local"}
    without = {**OTHER_NODES["ai (skill actif)"], "skill": "qualifier", "provider": "absent"}
    g0 = {"inputs": schema(motif=STR), "nodes": [without], "edges": [], "outputs": {}}
    assert "unknown_provider" in {
        i["code"] for i in validate(g0, ws, "x")
    }  # d'où `workspace.ai_providers` dans le guide
    for node_ in (OTHER_NODES["transform"], ai):
        g = {"inputs": schema(lignes={"type": "array"}, motif=STR), "nodes": [node_], "edges": [], "outputs": {}}
        assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == [], (node_["type"], validate(g, ws, "x"))


def test_the_guide_warns_about_nodes_that_need_a_published_workflow():
    text = guide("workflow")["guide"]
    assert "foreach" in text and "PUBLIÉ" in text and "script" in text and "approval" in text and "default" in text


def test_the_guide_tells_which_ai_providers_the_workspace_has_without_exposing_their_settings(ws, world):
    from ai.models import AiProvider

    AiProvider.objects.create(
        workspace=ws, name="local", kind="local_openai", base_url="http://llm.interne:8080", model="modele-secret-xyz"
    )
    out = call(client_for(world["viewer"]), "get_authoring_guide", {"topic": "workflow"})["structuredContent"]
    assert out["workspace"] == {"ai_providers": ["local"], "connections_by_kind": {}}
    assert "llm.interne" not in json.dumps(out) and "modele-secret-xyz" not in json.dumps(out)


def test_a_refused_approval_is_a_failed_step_that_an_error_edge_can_handle(ws, world):
    """Constaté sur un workflow de plus de 100 étapes : l'assistant ne savait pas router un refus d'approbation. Le moteur le traite comme un échec
    de l'étape (code APPROVAL_REJECTED) : une arête `error` le prend en charge, et sans elle le workflow échoue. Ce que le guide enseigne est exécuté ici."""
    from runs.engine import decide_approval
    from runs.models import Approval
    from tenancy.models import Role
    from tests.conftest import make_user
    from tests.wf_helpers import drain, launch, publish

    approver = make_user("appro", ws, Role.APPROVER)

    def build(slug, with_handler):
        nodes = [
            {"id": "ok", "type": "approval", "name": "Validation", "title": "Valider ?"},
            {
                "id": "suite",
                "type": "script",
                "name": "Suite",
                "language": "python",
                "script": "r = 1\n",
                "bindings": {},
                "outputs": [{"name": "r", "type": "integer"}],
            },
        ]
        edges = [{"from": "ok", "to": "suite"}]
        if with_handler:
            nodes.append(
                {
                    "id": "refus",
                    "type": "script",
                    "name": "Traiter le refus",
                    "language": "python",
                    "script": "traite = 1\n",
                    "bindings": {},
                    "outputs": [{"name": "traite", "type": "integer"}],
                }
            )
            edges.append({"from": "ok", "to": "refus", "label": "error"})
        return publish(ws, slug, nodes, edges, user=world["designer"])

    handled = launch(ws, build("avec-refus", True), world["designer"])
    handled = drain(
        handled, max_rounds=40, until=lambda r: Approval.objects.filter(step_run__run=r, status="pending").exists()
    )
    decide_approval(Approval.objects.get(step_run__run=handled).pk, approver, False, "non")
    handled = drain(handled, max_rounds=60)
    steps = {s.node_id: s.status for s in handled.steps.all()}
    assert steps["refus"] == "succeeded" and steps.get("suite") != "succeeded" and handled.status == "succeeded"

    bare = launch(ws, build("sans-refus", False), world["designer"])
    bare = drain(
        bare, max_rounds=40, until=lambda r: Approval.objects.filter(step_run__run=r, status="pending").exists()
    )
    decide_approval(Approval.objects.get(step_run__run=bare).pk, approver, False, "non")
    bare = drain(bare, max_rounds=60)
    assert bare.status == "failed"  # sans arête `error`, un refus fait échouer le workflow


def test_the_guide_teaches_what_an_assistant_got_wrong_or_could_not_know():
    """Constatés sur une session réelle de plus de 100 étapes : refus d'approbation, codes d'options, impossibilité d'exécuter."""
    text = guide("all")["guide"]
    assert "REFUS fait ÉCHOUER" in text and "arête `error`" in text
    assert "`value` des options" in text and "pas leurs libellés" in text
    assert (
        "ESSAYEZ avant de rendre la main" in text and "ce que l'essai ne couvre pas" in text
    )  # il peut désormais essayer, mais doit dire ce qui reste non couvert


def test_the_guide_makes_the_assistant_try_before_handing_over():
    full = guide("all")["guide"]
    assert "start_dry_run" in full and "get_dry_run" in full and "zéro problème" in full and "SIMULÉS" in full
    essai = guide("essai")["guide"]
    assert (
        essai.startswith("ESSAI À BLANC")
        and "mocks" in essai
        and "expected" in essai
        and "MÊMES codes que le formulaire" in essai
    )


def test_the_guide_makes_the_assistant_test_the_code_of_the_actions_it_proposes():
    essai = guide("essai")["guide"]
    assert (
        "start_action_test" in essai
        and "reste « proposée »" in essai
        and "cas nominal, un cas vide ou limite et un cas d'erreur" in essai
    )
    assert "start_action_test" in guide("method")["guide"]


def test_the_guide_teaches_native_steps_and_that_actions_have_no_network(ws):
    text = guide("all")["guide"]
    assert "ÉLÉMENT NATIF D'ABORD" in text and "AUCUN réseau" in text and "étape `http`" in text
    assert all(f"`{t}`" in text for t in ("http", "sql", "ldap", "sftp", "smtp"))


def test_the_native_step_examples_pass_the_validator(ws):
    from vault.models import Connection

    for name, kind in (("svc", "http_bearer"), ("db", "postgres"), ("mail", "smtp")):
        Connection.objects.create(workspace=ws, name=name, label=name, kind=kind)
    for key, conn in (
        ("http (appel d'un service web par une connexion)", "svc"),
        ("sql (lecture seule)", "db"),
        ("smtp (envoi d'e-mail)", "mail"),
    ):
        node_ = {**OTHER_NODES[key], "connection": conn}
        g = {"inputs": schema(motif=STR, mail=STR), "nodes": [node_], "edges": [], "outputs": {}}
        issues = validate(g, ws, "x")
        assert [i for i in issues if i["level"] == "error"] == [], (key, issues)


def test_the_workspace_facts_list_connections_by_kind_without_secrets(ws):
    from vault.models import Connection

    Connection.objects.create(workspace=ws, name="svc", label="Service", kind="oauth2_client", config={"x": "y"})
    facts = guide("all", ws)["workspace"]
    assert facts["connections_by_kind"] == {"oauth2_client": ["svc"]}
    assert "config" not in json.dumps(facts)


def test_a_script_or_action_that_calls_the_network_gets_a_hint():
    from workflows.mcp_design_hints import design_hints, uses_network

    assert uses_network("import requests\nrequests.get('https://x')") and uses_network("await fetch(u)")
    assert not uses_network("total = sum(lignes)")
    g = {"nodes": [{"id": "a", "type": "script", "script": "requests.post(u)"}]}
    assert "network_in_script" in {h["code"] for h in design_hints(g)}


def test_the_expression_help_lists_exactly_the_engine_functions():
    from workflows.expressions import FUNCS

    text = guide("expressions")["guide"]
    assert text.startswith("EXPRESSIONS") and all(f"{name}" in text for name in FUNCS) and "default" in text
    assert "inputs." in text and "steps.<id>.output" in text and "PAS de filtre" in text


def test_the_integration_example_passes_the_validator(ws):
    from vault.models import Connection

    Connection.objects.create(workspace=ws, name="slack-bot", label="Slack", kind="http_bearer")
    node_ = {**OTHER_NODES["http via une intégration (Slack, Jira, GitHub…)"], "connection": "slack-bot"}
    g = {"inputs": schema(motif=STR), "nodes": [node_], "edges": [], "outputs": {}}
    assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == []
    bad = {**node_, "operation": "n_existe_pas"}
    assert "bad_http" in {i["code"] for i in validate({**g, "nodes": [bad]}, ws, "x")}


def test_the_error_handling_example_passes_the_validator(ws):
    from vault.models import Connection

    Connection.objects.create(workspace=ws, name="tickets", label="Tickets", kind="http_bearer")
    g = json.loads(
        json.dumps(ERROR_HANDLING_EXAMPLE, ensure_ascii=False).replace(
            "<connexion « Service HTTP » de list_connections>", "tickets"
        )
    )
    assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == [], validate(g, ws, "x")
    # la branche d'erreur qui lirait la sortie de l'étape en échec est refusée : c'est ce que la rubrique enseigne
    bad = json.loads(json.dumps(g))
    bad["nodes"][1]["bindings"] = {"motif": "{{ steps.creer_ticket.output.status }}"}
    assert "reads_failed_output" in {i["code"] for i in validate(bad, ws, "x")}
    # idem quand c'est le message d'un `throw` qui lit la sortie de l'étape en échec
    bad_throw = json.loads(json.dumps(g))
    bad_throw["nodes"][2]["message"] = "{{ steps.creer_ticket.output.status }}"
    assert "reads_failed_output" in {i["code"] for i in validate(bad_throw, ws, "x")}


def test_list_integrations_gives_the_catalog_then_the_operations_without_secrets(ws, world):
    c = client_for(world["viewer"])
    res = call(c, "list_integrations", {"query": "slack"})
    assert res["isError"] is False and res["structuredContent"]["results"][0]["slug"] == "slack"
    detail = call(c, "list_integrations", {"slug": "slack"})["structuredContent"]
    post = next(o for o in detail["operations"] if o["id"] == "post_message")
    assert {p["name"] for p in post["params"] if p["required"]} == {"channel", "text"}
    assert detail["connection_kind"] == "http_bearer"
    assert call(c, "list_integrations", {"slug": "inconnue"})["isError"] is True


def test_the_declencheurs_topic_names_every_trigger_kind_and_its_input_rule():
    text = guide("declencheurs")["guide"]
    assert text.startswith("DÉCLENCHEURS") and all(
        w in text for w in ("PLANIFICATION", "WEBHOOK", "INTERROGATION", "FORMULAIRE PUBLIC")
    )
    assert "`payload`" in text and "`item`" in text and "`items`" in text and "list_triggers" in text
    assert "trigger_inputs" in guide("all")["examples"]


def test_the_trigger_input_examples_satisfy_the_real_trigger_rules():
    """Les schémas du guide sont acceptés par les mêmes contrôles que la création d'un déclencheur."""
    from types import SimpleNamespace

    from rest_framework import serializers

    from triggers import polls
    from triggers.models import PollTrigger

    ex = TRIGGER_INPUTS_EXAMPLES
    one = SimpleNamespace(input_schema=ex["interrogation, un élément par exécution"])
    batch = SimpleNamespace(input_schema=ex["interrogation, un lot"])
    polls._check_inputs(one, {"mode": PollTrigger.Mode.PER_ITEM, "id_path": "id"})
    polls._check_inputs(batch, {"mode": PollTrigger.Mode.BATCH, "id_path": "id"})
    # le piège enseigné : une autre entrée obligatoire fait refuser le déclencheur
    extra = SimpleNamespace(input_schema={**one.input_schema, "required": ["item", "autre"]})
    with pytest.raises(serializers.ValidationError):
        polls._check_inputs(extra, {"mode": PollTrigger.Mode.PER_ITEM, "id_path": "id"})
    hook = ex["webhook (fournisseur github, gitlab, jira ou jeton)"]
    assert "payload" in hook["properties"] and set(hook["properties"]) == {"payload", "event", "delivery"}


def test_list_triggers_shows_what_exists_without_any_secret_address_or_value(ws, world):
    from tenancy.models import Environment
    from triggers.models import PollTrigger, PublicForm, Schedule, Webhook
    from workflows.models import Workflow

    wf, _ = Workflow.objects.get_or_create(workspace=ws, slug="salue", defaults={"name": "salue"})
    env = Environment.objects.get(workspace=ws, kind="test")
    common = {"workspace": ws, "workflow": wf, "environment": env}
    Schedule.objects.create(**common, name="matin", cron="0 7 * * *", inputs={"cle": "valeur-confidentielle"})
    hook = Webhook.objects.create(**common, name="gh", provider="github")
    PollTrigger.objects.create(
        **common, name="tickets", request={"connection": "svc", "path": "/secret-chemin"}, id_path="id"
    )
    form = PublicForm.objects.create(**common, name="pub", title="Demande")
    c = client_for(world["viewer"])
    res = call(c, "list_triggers")
    assert res["isError"] is False
    rows = {r["kind"]: r for r in res["structuredContent"]["results"]}
    assert set(rows) == {"schedule", "webhook", "poll", "public_form"}
    assert rows["schedule"]["cron"] == "0 7 * * *" and rows["schedule"]["fixed_inputs"] == ["cle"]
    dump = json.dumps(res["structuredContent"])
    assert all(x not in dump for x in (str(hook.pk), form.token, "valeur-confidentielle", "secret-chemin"))
    only = call(c, "list_triggers", {"kind": "webhook"})["structuredContent"]["results"]
    assert [r["kind"] for r in only] == ["webhook"]


def test_list_triggers_hides_triggers_of_private_folders(ws, world):
    from tenancy.models import Environment
    from triggers.models import Schedule
    from workflows.models import FolderAccess, Workflow

    wf, _ = Workflow.objects.get_or_create(workspace=ws, slug="salue", defaults={"name": "salue", "folder": "RH/Prive"})
    FolderAccess.objects.create(workspace=ws, folder="RH")  # liste d'accès sans le lecteur : dossier privé
    Schedule.objects.create(
        workspace=ws,
        workflow=wf,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        name="m",
        cron="0 7 * * *",
    )
    assert call(client_for(world["viewer"]), "list_triggers")["structuredContent"]["results"] == []


def test_the_dynamic_list_example_is_a_valid_form_and_reads_a_real_shaped_reply():
    """L'exemple du guide passe la validation des formulaires, et sa lecture donne bien groupes puis membres sur une réponse type."""
    from triggers import lookups

    assert formengine.check_definition(FORM_SOURCE_EXAMPLE) == {}
    groupe, membre = (f["source"] for f in FORM_SOURCE_EXAMPLE["sections"][0]["fields"])
    reply = {"groups": {"group:ops": ["bob@ex.fr", "alice@ex.fr"], "group:vide": [], "group:dev": ["carl@ex.fr"]}}
    assert [o["label"] for o in lookups.to_options(reply, groupe)] == [
        "group:dev (1)",
        "group:ops (2)",
    ]  # « vide » écarté, trié
    assert [o["value"] for o in lookups.to_options(reply, membre, params={"groupe": "group:ops"})] == [
        "alice@ex.fr",
        "bob@ex.fr",
    ]


def test_the_listes_topic_teaches_the_shape_driven_choice_and_the_cascade():
    text = guide("listes")["guide"]
    assert text.startswith("LISTES DYNAMIQUES") and all(
        w in text for w in ("items_as", "entries", "where", "label_expr", "{champ}")
    )
    assert "form_source" in guide("all")["examples"] and "topic `listes`" in guide("form")["guide"]
