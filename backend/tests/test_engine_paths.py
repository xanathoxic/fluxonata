"""Chemins du moteur que les scénarios nominaux ne parcourent pas : boucle dont une itération échoue, sous-workflow en échec
ou mal alimenté, sortie du workflow illisible à la fin."""

import pytest

from tests.wf_helpers import STR, drain, launch, node, publish, schema


def test_a_failing_iteration_fails_the_loop_and_names_the_iteration(ws, world):
    user = world["designer"]
    publish(
        ws,
        "bad",
        [node("b", action="boom")],
        [],
        inputs=schema(["item"], item=STR),
        user=user,
    )
    parent = publish(
        ws,
        "p",
        [
            node(
                "loop",
                "foreach",
                workflow="bad",
                items="inputs.items",
                item_input="item",
                max_iterations=5,
                concurrency=1,
            )
        ],
        [],
        inputs=schema(["items"], items={"type": "array"}),
        user=user,
    )
    run = drain(launch(ws, parent, user, {"items": ["a", "b"]}), max_rounds=300)
    assert run.status == "failed" and run.error["code"] == "ITERATION_FAILED"
    assert "itération" in run.error["message"]


def test_a_failing_sub_workflow_fails_its_parent_with_the_child_error(ws, world):
    user = world["designer"]
    publish(ws, "bad2", [node("b", action="boom")], [], user=user)
    outer = publish(ws, "o", [node("sub", "subworkflow", workflow="bad2")], [], user=user)
    run = drain(launch(ws, outer, user), max_rounds=200)
    assert run.status == "failed" and run.error


def test_an_unreadable_workflow_output_ends_the_run_as_failed(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "o2",
        [node("e", action="echo", inputs={"value": "x"})],
        [],
        inputs=schema(["zero"], zero={"type": "integer"}),
        outputs={"x": "{{ 1 / inputs.zero }}"},
        user=user,
    )
    run = drain(launch(ws, v, user, {"zero": 0}), max_rounds=100)
    assert run.status == "failed" and run.error["code"] == "OUTPUT_ERROR"
    assert run.error["message"].startswith("Sorties du workflow")


def test_a_sub_workflow_started_with_inputs_that_break_its_schema_fails_with_the_reason(ws, world):
    user = world["designer"]
    publish(
        ws,
        "needs-text",
        [node("e", action="echo", inputs={"value": "{{ inputs.item }}"})],
        [],
        inputs=schema(["item"], item=STR),
        user=user,
    )
    outer = publish(
        ws,
        "wrong-input",
        [node("sub", "subworkflow", workflow="needs-text", inputs={"item": "{{ inputs.x }}"})],
        [],
        inputs=schema(["x"], x={}),  # untyped: the mismatch only shows when the value arrives
        user=user,
    )
    run = drain(launch(ws, outer, user, {"x": 5}), max_rounds=100)
    assert run.status == "failed" and run.error["code"] == "INPUT_INVALID"
    assert "Entrées du sous-workflow invalides" in run.error["message"]


def test_declared_script_outputs_are_checked_without_echoing_values():
    from runs.engine import _script_output_problem as check

    node_ = {"outputs": [{"name": "n", "type": "integer"}, {"name": "t", "type": "string"}]}
    assert check(node_, {"n": 1, "t": "x"}) is None
    assert check(node_, {"n": 1}) == "La sortie « t » n'a pas été produite."
    assert check(node_, None) == "La sortie « n » n'a pas été produite."
    assert (
        check(node_, {"n": True, "t": "x"}) == "La sortie « n » doit être de type integer."
    )  # a boolean is not a number
    assert check(node_, {"n": "secret-12", "t": "x"}) == "La sortie « n » doit être de type integer."
    assert check({"outputs": [{"name": "r", "type": "number"}]}, {"r": 1.5}) is None


def test_the_runtime_image_digest_is_the_pin_or_what_docker_says_or_nothing(monkeypatch):
    from runs import engine

    engine._digest_cache.clear()
    assert engine._image_digest("repo/img@sha256:abc") == "sha256:abc"
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    assert engine._image_digest("repo/img:latest") is None
    engine._digest_cache.clear()


def test_an_operator_decides_about_an_uncertain_step(ws, world):
    from rest_framework.exceptions import ValidationError

    from runs import engine
    from runs.models import StepRun

    user = world["designer"]
    v = publish(ws, "unsure", [node("w", "wait", seconds=3600)], [], user=user)
    run = launch(ws, v, user)
    engine.advance(run.pk)
    sr = run.steps.get(node_id="w")
    with pytest.raises(ValidationError):  # not uncertain: nothing to decide
        engine.decide_uncertain(sr.pk, user, "retry")
    for decision, expected in (("retry", "retry_wait"), ("succeed", "succeeded"), ("fail", "failed")):
        StepRun.objects.filter(pk=sr.pk).update(status="uncertain", error={"code": "X"})
        engine.decide_uncertain(sr.pk, user, decision, output={"ok": True})
        assert StepRun.objects.get(pk=sr.pk).status == expected
    StepRun.objects.filter(pk=sr.pk).update(status="uncertain")
    with pytest.raises(ValidationError):
        engine.decide_uncertain(sr.pk, user, "peut-être")


def test_cancelling_a_run_rejects_the_approval_it_was_waiting_for(ws, world):
    from runs import engine
    from runs.models import Approval

    user = world["designer"]
    v = publish(ws, "annule", [node("ap", "approval", title="Valider")], [], user=user)
    run = drain(launch(ws, v, user), until=lambda r: Approval.objects.exists())
    engine.request_run_cancel(run.pk, user)
    run = drain(run, max_rounds=100)
    ap = Approval.objects.get()
    assert run.status == "cancelled" and ap.status == "rejected" and ap.comment == "Annulée avec le workflow"


def test_a_failing_iteration_cancels_the_ones_still_running(ws, world):
    user = world["designer"]
    publish(
        ws,
        "mixed",
        [
            node("c", "condition", expr="inputs.item == 'fail'"),
            node("b", action="boom"),
            node("s", action="sleepy", inputs={"seconds": 2}),
        ],
        [
            {"from": "c", "to": "b", "label": "true"},
            {"from": "c", "to": "s", "label": "false"},
        ],
        inputs=schema(["item"], item=STR),
        user=user,
    )
    parent = publish(
        ws,
        "fan",
        [
            node(
                "loop",
                "foreach",
                workflow="mixed",
                items="inputs.items",
                item_input="item",
                max_iterations=5,
                concurrency=2,
            )
        ],
        [],
        inputs=schema(["items"], items={"type": "array"}),
        user=user,
    )
    run = drain(launch(ws, parent, user, {"items": ["slow", "fail"]}), max_rounds=400)
    assert run.status == "failed" and run.error["code"] == "ITERATION_FAILED"
    assert run.error["message"].endswith("L'itération 2 a échoué.")  # not the first one, only cancelled because of it
