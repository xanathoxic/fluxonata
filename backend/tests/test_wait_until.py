"""Nœud « Attente » réglé sur une date : le workflow est en pause jusqu'à la date et l'heure, puis reprend (minuterie durable)."""

from datetime import UTC, timedelta

import pytest
from django.utils import timezone

from runs import engine
from runs.models import WorkflowRun
from tests.wf_helpers import STR, drain, edge, launch, node, publish, schema
from workflows.validation import validate


def when(days=0, hours=0) -> str:
    return (timezone.now() + timedelta(days=days, hours=hours)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M")


def flow(ws, user, **fields):
    cfg = {"until": when(days=3), "timezone": "UTC"} | fields
    return publish(
        ws,
        "pause",
        [node("w", "wait", **cfg), node("a", action="echo", inputs={"value": "{{ inputs.text }}"})],
        [edge("w", "a")],
        inputs=schema(["text"], text=STR),
        user=user,
    )


def run_of(ws, user, **fields):
    return launch(ws, flow(ws, user, **fields), user, {"text": "x"})


def test_the_workflow_pauses_until_the_date_then_resumes(ws, world):
    run = run_of(ws, world["designer"])
    run = drain(run, max_rounds=5)
    w = run.steps.get(node_id="w")
    assert run.status == "running" and w.status == "waiting"
    expected = timezone.now() + timedelta(days=3)
    assert abs((w.available_at - expected).total_seconds()) < 120
    assert not run.steps.filter(node_id="a").exists()  # rien ne s'exécute pendant la pause
    assert run.events.filter(type="wait_until").exists()
    w.available_at = timezone.now() - timedelta(seconds=1)  # la date est arrivée
    w.save()
    run = drain(run)
    assert run.status == "succeeded" and run.steps.get(node_id="a").status == "succeeded"


def test_the_pause_is_not_over_before_the_date(ws, world):
    run = drain(run_of(ws, world["designer"], until=when(hours=2)), max_rounds=8)
    engine.advance(run.pk)
    assert WorkflowRun.objects.get(pk=run.pk).steps.get(node_id="w").status == "waiting"


def test_the_date_is_read_in_the_chosen_timezone(ws, world):
    run = drain(run_of(ws, world["designer"], until="2099-01-15T09:00", timezone="Europe/Paris"), max_rounds=3)
    assert run.status == "failed" or run.steps.get(node_id="w").error  # hors horizon : refus
    run = drain(run_of(ws, world["designer"], until=when(days=2)[:11] + "09:00", timezone="Europe/Paris"), max_rounds=3)
    w = run.steps.get(node_id="w")
    assert w.status == "waiting" and w.available_at.hour in (7, 8)  # 09:00 à Paris = 07:00 ou 08:00 UTC


def test_the_date_can_come_from_an_input(ws, world):
    wf = publish(
        ws,
        "pause-input",
        [node("w", "wait", until="{{ inputs.quand }}", timezone="UTC")],
        [],
        inputs=schema(["quand"], quand=STR),
        user=world["designer"],
    )
    run = drain(launch(ws, wf, world["designer"], {"quand": when(days=1)}), max_rounds=3)
    assert run.steps.get(node_id="w").status == "waiting"


def test_a_past_date_fails_or_continues_right_away(ws, world):
    run = drain(run_of(ws, world["designer"], until=when(hours=-2)))
    assert run.steps.get(node_id="w").error["code"] == "WAIT_PAST"
    run = drain(run_of(ws, world["designer"], until=when(hours=-2), on_past="now"))
    assert run.status == "succeeded"


def test_the_horizon_is_bounded_to_a_year(ws, world):
    run = drain(run_of(ws, world["designer"], until=when(days=400)))
    assert run.steps.get(node_id="w").error["code"] == "WAIT_HORIZON"


def test_a_bad_expression_value_fails_clearly(ws, world):
    wf = publish(
        ws,
        "pause-bad",
        [node("w", "wait", until="{{ inputs.quand }}", timezone="UTC")],
        [],
        inputs=schema(["quand"], quand=STR),
        user=world["designer"],
    )
    run = drain(launch(ws, wf, world["designer"], {"quand": "demain"}))
    assert run.steps.get(node_id="w").error["code"] == "WAIT_UNTIL"


def test_the_pause_survives_a_cancellation_request(ws, world):
    run = drain(run_of(ws, world["designer"]), max_rounds=3)
    WorkflowRun.objects.filter(pk=run.pk).update(cancel_requested=True)
    assert drain(run).status == "cancelled"


def issues(ws, **fields):
    g = {"inputs": schema(), "nodes": [node("w", "wait", **fields)], "edges": [], "outputs": {}}
    return [i["message"] for i in validate(g, ws, "x") if i["level"] == "error"]


@pytest.mark.parametrize(
    ("fields", "needle"),
    [
        ({"until": ""}, "date et l'heure"),
        ({"until": "2026-12-01"}, "heure"),
        ({"until": "pas une vraie date"}, "illisible"),
        ({"until": "2026-12-01T08:30", "timezone": "Mars/Base"}, "Fuseau"),
        ({"until": "2026-12-01T08:30", "on_past": "peut-être"}, "date est passée"),
    ],
)
def test_validation_rejects_bad_configuration(ws, fields, needle):
    assert any(needle in m for m in issues(ws, **fields))


def test_validation_accepts_a_date_without_a_delay_and_checks_expressions(ws):
    assert issues(ws, until="2026-12-01T08:30") == []
    assert issues(ws, until="{{ inputs.inconnue }}") != []
    assert issues(ws, seconds=60) == []  # le réglage par durée est inchangé
