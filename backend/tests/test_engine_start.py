"""Caractérisation du démarrage d'une étape (`runs.engine._start`) : ce que l'étape devient selon son type, avant tout refactoring.

Les versions sont créées SANS la validation du publieur (`raw_version`) quand le test doit atteindre une erreur que la validation
refuserait en amont : le moteur doit rester sûr même devant un graphe que la validation n'aurait pas dû laisser passer."""

import pytest

from runs.models import Approval, StepRun
from tenancy.models import Environment
from tests.wf_helpers import STR, drain, edge, launch, node, publish, schema, step
from workflows.models import Workflow, WorkflowVersion
from workflows.validation import pin_graph


def raw_version(ws, slug, nodes, edges, user, inputs=None, pins=None):
    graph = {"inputs": inputs or schema(), "nodes": nodes, "edges": edges, "outputs": {}}
    if pins:
        graph["pins"] = pins
    wf, _ = Workflow.objects.get_or_create(workspace=ws, slug=slug, defaults={"name": slug})
    pinned = pin_graph(graph, ws)
    if pins:
        pinned["pins"] = pins
    return WorkflowVersion.objects.create(
        workflow=wf, version="1.0.0", graph=pinned, input_schema=graph["inputs"], created_by=user
    )


def events(run):
    return list(run.events.order_by("id").values_list("type", "message"))


def test_throw_fails_the_step_with_its_code_and_a_resolved_message(ws, world):
    user = world["designer"]
    v = raw_version(
        ws,
        "thr",
        [node("t", "throw", code="MONTANT", message="Montant {{ inputs.m }} refusé")],
        [],
        user,
        inputs=schema(["m"], m=STR),
    )
    run = drain(launch(ws, v, user, {"m": "42"}))
    sr = step(run, "t")
    assert run.status == "failed" and sr.status == "failed"
    assert sr.error["code"] == "MONTANT" and sr.error["message"] == "Montant 42 refusé"
    assert sr.error["next_step"]
    assert ("step_failed", "Étape « T » échouée : Montant 42 refusé") in events(run)


def test_throw_without_code_uses_the_default_one(ws, world):
    user = world["designer"]
    v = raw_version(ws, "thr2", [node("t", "throw", message="Stop")], [], user)
    assert step(drain(launch(ws, v, user)), "t").error["code"] == "THROWN"


def test_switch_takes_the_first_true_case_else_default(ws, world):
    user = world["designer"]
    nodes = [
        node("s", "switch", cases=[{"id": "petit", "expr": "inputs.n < 5"}, {"id": "moyen", "expr": "inputs.n < 50"}]),
        node("a", "join"),
    ]
    v = raw_version(ws, "sw", nodes, [edge("s", "a", "petit")], user, inputs=schema(["n"], n={"type": "integer"}))
    for n, expected in ((3, "petit"), (30, "moyen"), (300, "default")):
        run = drain(launch(ws, v, user, {"n": n}))
        assert step(run, "s").output == {"result": expected}


def test_condition_reports_oui_or_non(ws, world):
    user = world["designer"]
    v = raw_version(
        ws, "cond", [node("c", "condition", expr="inputs.n > 1")], [], user, inputs=schema(["n"], n={"type": "integer"})
    )
    run = drain(launch(ws, v, user, {"n": 0}))
    assert step(run, "c").output == {"result": False}
    assert ("step_succeeded", "Condition « C » : non.") in events(run)


def test_a_failing_expression_fails_the_step_with_expression_error(ws, world):
    user = world["designer"]
    v = raw_version(
        ws,
        "exprerr",
        [node("c", "condition", expr="inputs.n / 0 > 1")],
        [],
        user,
        inputs=schema(["n"], n={"type": "integer"}),
    )
    run = drain(launch(ws, v, user, {"n": 1}))
    sr = step(run, "c")
    assert sr.status == "failed" and sr.error["code"] == "EXPRESSION_ERROR"
    assert sr.error["next_step"] == "Corrigez la configuration de l'étape."


def test_wait_parks_the_step_until_its_date(ws, world):
    user = world["designer"]
    v = raw_version(ws, "wt", [node("w", "wait", seconds=3600)], [], user)
    run = drain(launch(ws, v, user), max_rounds=3)
    sr = step(run, "w")
    assert sr.status == "waiting" and sr.available_at is not None and run.status == "running"


def test_approval_creates_one_request_and_waits(ws, world):
    user = world["designer"]
    v = raw_version(
        ws,
        "appr",
        [node("a", "approval", title="Valider {{ inputs.m }}", instructions="Relire", allow_self_approval=True)],
        [],
        user,
        inputs=schema(["m"], m=STR),
    )
    run = drain(launch(ws, v, user, {"m": "X"}), max_rounds=3)
    sr = step(run, "a")
    assert sr.status == "waiting"
    ap = Approval.objects.get(step_run=sr)
    assert (ap.title, ap.instructions, ap.allow_self_approval, ap.workspace_id) == ("Valider X", "Relire", True, ws.pk)
    assert ap.requested_by_id == user.pk
    assert any(k == "approval_requested" for k, _ in events(run))


def test_foreach_over_something_that_is_not_a_list_fails(ws, world):
    user = world["designer"]
    publish(ws, "fe-child", [node("e", "join")], [], inputs=schema(["item"], item=STR), user=user)
    v = raw_version(
        ws,
        "fe",
        [
            node(
                "f",
                "foreach",
                items="inputs.m",
                workflow="fe-child",
                item_input="item",
                max_iterations=3,
                concurrency=1,
            )
        ],
        [],
        user,
        inputs=schema(["m"], m=STR),
    )
    sr = step(drain(launch(ws, v, user, {"m": "pas une liste"})), "f")
    assert sr.status == "failed" and sr.error["code"] == "NOT_A_LIST"


@pytest.mark.parametrize("env, replaced", [("dev", True), ("test", False)])
def test_a_pinned_output_replaces_the_step_in_dev_only(ws, world, env, replaced):
    user = world["designer"]
    Environment.objects.get_or_create(workspace=ws, kind="dev", defaults={"name": "dev"})
    v = raw_version(
        ws, "pinned", [node("e", action="echo", inputs={"value": "reel"})], [], user, pins={"e": {"value": "epingle"}}
    )
    run = drain(launch(ws, v, user, env=env))
    sr = step(run, "e")
    assert sr.status == "succeeded"
    assert (sr.output == {"value": "epingle"}) is replaced
    assert any(k == "step_pinned" for k, _ in events(run)) is replaced


def test_a_pin_never_replaces_a_decision(ws, world):
    user = world["designer"]
    Environment.objects.get_or_create(workspace=ws, kind="dev", defaults={"name": "dev"})
    v = raw_version(
        ws,
        "pinc",
        [node("c", "condition", expr="inputs.n > 1")],
        [],
        user,
        inputs=schema(["n"], n={"type": "integer"}),
        pins={"c": {"result": True}},
    )
    assert step(drain(launch(ws, v, user, {"n": 0}, env="dev")), "c").output == {"result": False}


@pytest.mark.parametrize("ntype", ["sql", "sftp", "http", "smtp", "ldap", "ssh"])
def test_a_network_step_with_an_unusable_configuration_fails_with_its_own_error(ws, world, ntype):
    user = world["designer"]
    v = raw_version(ws, f"bad-{ntype}", [node("n", ntype, connection="inexistante")], [], user)
    run = drain(launch(ws, v, user))
    sr = step(run, "n")
    assert sr.status == "failed" and run.status == "failed"
    assert sr.error["code"] and sr.error["message"] and sr.error["next_step"]
    assert isinstance(sr.output, (dict, type(None)))
    assert sr.node_type == ntype and not StepRun.objects.filter(run=run, status="waiting").exists()
