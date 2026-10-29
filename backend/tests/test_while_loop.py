import pytest

from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish, schema
from workflows.validation import validate_graph


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    publish(
        world["ws_a"],
        "incremente",
        [node("a", action="add", inputs={"a": "{{ inputs.n }}", "b": 1})],
        [],
        inputs=schema(["n"], n={"type": "integer"}),
        outputs={"n": "{{ steps.a.output.sum }}"},
        user=world["designer"],
    )
    return world["ws_a"]


def _loop(ws, world, **kw):
    fields = {
        "workflow": "incremente",
        "inputs": {"n": "{{ default(last.n, 0) }}"},
        "condition": "last.n < 3",
        "max_iterations": 10,
        **kw,
    }
    return publish(
        ws,
        "boucle",
        [node("w", "while", **fields)],
        [],
        outputs={"fois": "{{ steps.w.output.iterations }}", "n": "{{ steps.w.output.last.n }}"},
        user=world["designer"],
    )


def test_repeats_while_the_condition_holds_and_exposes_last_and_iteration(ws, world):
    run = drain(launch(ws, _loop(ws, world), world["designer"]), max_rounds=300)
    out = run.steps.get(node_id="w").output
    assert run.status == "succeeded" and out["iterations"] == 3 and out["stopped"] == "condition"
    assert [r["n"] for r in out["results"]] == [1, 2, 3] and out["last"] == {"n": 3}
    assert run.outputs == {"fois": 3, "n": 3}


def test_the_bound_stops_a_loop_whose_condition_never_ends(ws, world):
    run = drain(launch(ws, _loop(ws, world, condition="true", max_iterations=2), world["designer"]), max_rounds=300)
    out = run.steps.get(node_id="w").output
    assert run.status == "succeeded" and out["iterations"] == 2 and out["stopped"] == "max_iterations"


def test_delay_between_repetitions_and_failed_iteration(ws, world):
    from datetime import timedelta

    from django.utils import timezone

    from runs.models import StepRun

    run = launch(ws, _loop(ws, world, delay_seconds=3600), world["designer"])
    run = drain(
        run,
        max_rounds=60,
        until=lambda r: StepRun.objects.filter(run=r, node_id="w", available_at__isnull=False).exists(),
    )
    sr = run.steps.get(node_id="w")
    assert sr.available_at > timezone.now() + timedelta(minutes=50) and sr.output["iterations"] == 1
    StepRun.objects.filter(pk=sr.pk).update(available_at=timezone.now() - timedelta(seconds=1))
    run = drain(run, max_rounds=40, until=lambda r: r.steps.get(node_id="w").output["iterations"] >= 2)
    assert run.steps.get(node_id="w").output["iterations"] >= 2


def test_validation(ws, world):
    def codes(**kw):
        g = {"inputs": schema(), "nodes": [node("w", "while", workflow="incremente", **kw)], "edges": []}
        return {i["code"] for i in validate_graph(g, ws, "t") if i["level"] == "error"}

    assert "missing_expr" in codes(inputs={"n": 1}, max_iterations=5)
    assert "unbounded_loop" in codes(inputs={"n": 1}, condition="true")
    assert "bad_wait" in codes(inputs={"n": 1}, condition="true", max_iterations=5, delay_seconds=-1)
    assert "bad_expression" in codes(inputs={"n": 1}, condition="last.n <", max_iterations=5)
    assert codes(inputs={"n": "{{ default(last.n, 0) }}"}, condition="iteration < 3", max_iterations=5) == set()


def test_foreach_interval_starts_at_most_one_iteration_per_interval(ws, world):
    from datetime import timedelta

    from django.utils import timezone

    from runs.models import StepRun, WorkflowRun

    v = publish(
        ws,
        "lot",
        [
            node(
                "f",
                "foreach",
                workflow="incremente",
                items="inputs.valeurs",
                item_input="n",
                max_iterations=5,
                concurrency=3,
                interval_seconds=3600,
                inputs={},
            )
        ],
        [],
        inputs=schema(["valeurs"], valeurs={"type": "array"}),
        user=world["designer"],
    )
    run = drain(
        launch(ws, v, world["designer"], inputs={"valeurs": [1, 2, 3]}),
        max_rounds=80,
        until=lambda r: WorkflowRun.objects.filter(parent_step__run=r, status="succeeded").exists(),
    )
    sr = StepRun.objects.get(run=run, node_id="f")
    drain(run, max_rounds=30)
    assert sr.child_runs.count() == 1  # concurrency 3, but the interval lets one start per hour
    sr.refresh_from_db()
    sr.output = {**sr.output, "last_start": (timezone.now() - timedelta(hours=2)).timestamp()}
    sr.save()
    drain(run, max_rounds=30)
    assert StepRun.objects.get(pk=sr.pk).child_runs.count() == 2
