"""AR4.4 — l'orchestrateur ne repasse que sur les runs qui ont une raison d'être repris (`next_check_at`).

Mesuré avant le correctif : un passage sur un run qui n'a rien à faire coûte un verrou de ligne, donc un `fsync` au COMMIT (20-38 ms ici) ; 150 runs en attente
=> 5,5 s par tour. Ces tests protègent les DEUX côtés : (1) un run au repos n'est plus touché ; (2) aucun événement externe n'est retardé — chaque chemin qui
doit réveiller un run le réveille, et un événement oublié est de toute façon rattrapé par le filet de sécurité.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from runs import engine, orchestrator
from runs.models import Approval, StepRun, WorkflowRun
from runs.runners import SubprocessRunner
from runs.worker import process_one
from tests.wf_helpers import ACTIONS, edge, launch, make_action, node, publish


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


@pytest.fixture
def advanced(monkeypatch):
    """Les runs sur lesquels `tick()` a réellement appelé `advance`."""
    seen = []
    real = engine.advance
    monkeypatch.setattr(
        engine, "advance", lambda run_id, **kw: seen.append(str(run_id)) or real(run_id, **kw)
    )  # le tour passe `only_if_due=True` (AR4.5)
    return seen


def tick_and_get(run):
    orchestrator.tick()
    return WorkflowRun.objects.get(pk=run.pk)


def make_run(ws, world, nodes, edges=(), **kw):
    v = publish(ws, "wf", nodes, list(edges), user=world["designer"])
    return launch(ws, v, world["designer"], **kw)


# --- (1) un run au repos n'est plus touché ------------------------------------------------------------------------------------------
def test_a_run_waiting_for_a_timer_is_left_alone_until_it_is_due(ws, world, advanced):
    run = make_run(ws, world, [node("p", "wait", seconds=3600)])
    r = tick_and_get(run)
    assert advanced == [str(run.pk)]  # le premier passage démarre l'attente
    assert r.next_check_at is not None and timezone.now() + engine.IDLE_RECHECK - timedelta(
        seconds=2
    ) <= r.next_check_at <= timezone.now() + engine.IDLE_RECHECK + timedelta(seconds=2)
    advanced.clear()
    for _ in range(5):
        orchestrator.tick()
    assert advanced == []  # cinq tours : aucun verrou, aucun COMMIT


def test_the_timer_wakes_the_run_at_its_due_time_not_after_the_fallback(ws, world, advanced):
    run = make_run(ws, world, [node("p", "wait", seconds=3600)])
    tick_and_get(run)
    due = timezone.now() + timedelta(seconds=3)
    StepRun.objects.filter(run=run, node_id="p").update(available_at=due)
    engine.advance(run.pk)  # recalcule l'échéance à partir de l'étape (comme le ferait le prochain passage)
    assert abs((WorkflowRun.objects.get(pk=run.pk).next_check_at - due).total_seconds()) < 1


def test_a_run_whose_timer_elapsed_is_advanced_to_completion(ws, world, advanced):
    run = make_run(
        ws, world, [node("p", "wait", seconds=3600), node("e", action="echo", inputs={"value": "x"})], [edge("p", "e")]
    )
    tick_and_get(run)
    StepRun.objects.filter(run=run, node_id="p").update(available_at=timezone.now() - timedelta(seconds=1))
    WorkflowRun.objects.filter(pk=run.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
    for _ in range(40):
        orchestrator.tick()
        process_one("w", SubprocessRunner())
        WorkflowRun.objects.filter(pk=run.pk, status="running").update(
            next_check_at=None
        )  # ce test n'attend pas les 2 s de re-vérification d'une action
        if WorkflowRun.objects.get(pk=run.pk).status == "succeeded":
            return
    raise AssertionError("le run ne se termine pas")


# --- (2) aucun événement externe n'est retardé -------------------------------------------------------------------------------------
def test_an_approval_decision_wakes_the_run_immediately(ws, world, advanced):
    from tenancy.models import Role
    from tests.conftest import make_user

    run = make_run(
        ws,
        world,
        [node("ap", "approval", title="ok"), node("e", action="echo", inputs={"value": "x"})],
        [edge("ap", "e")],
    )
    tick_and_get(run)
    assert Approval.objects.count() == 1
    advanced.clear()
    orchestrator.tick()
    assert advanced == []  # en attente d'une décision humaine : on n'y touche pas
    engine.decide_approval(Approval.objects.get().pk, make_user("anna", ws, Role.APPROVER), True)
    assert WorkflowRun.objects.get(pk=run.pk).next_check_at <= timezone.now()  # réveillé
    orchestrator.tick()
    assert advanced == [str(run.pk)]


def test_a_cancel_request_wakes_the_run_immediately(ws, world, advanced):
    run = make_run(ws, world, [node("p", "wait", seconds=3600)])
    tick_and_get(run)
    advanced.clear()
    engine.request_run_cancel(run.pk, world["designer"])
    r = tick_and_get(run)
    assert advanced == [str(run.pk)] and r.status == "cancelled" and r.next_check_at is None


def test_an_execution_result_wakes_the_run_that_waits_for_it(ws, world, advanced):
    run = make_run(ws, world, [node("e", action="echo", inputs={"value": "x"})])
    tick_and_get(run)  # l'action est en file, l'étape attend un résultat
    r = WorkflowRun.objects.get(pk=run.pk)
    assert r.next_check_at >= timezone.now() + engine.EXEC_RECHECK - timedelta(seconds=1)  # re-vérification espacée...
    assert process_one("w", SubprocessRunner())  # ...mais le résultat réveille le run tout de suite
    assert WorkflowRun.objects.get(pk=run.pk).next_check_at <= timezone.now()
    advanced.clear()
    r = tick_and_get(run)
    assert advanced == [str(run.pk)] and r.status == "succeeded"


def test_a_lost_worker_wakes_the_run_too(ws, world, advanced, settings):
    from runs import services
    from runs.models import Execution

    run = make_run(ws, world, [node("e", action="boom")])
    tick_and_get(run)
    ex = Execution.objects.get()
    services.claim_next("w")
    Execution.objects.filter(pk=ex.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=5), attempt_count=settings.ORCH_MAX_ATTEMPTS
    )
    WorkflowRun.objects.filter(pk=run.pk).update(next_check_at=timezone.now() + timedelta(hours=1))
    services.reconcile_expired()
    assert WorkflowRun.objects.get(pk=run.pk).next_check_at <= timezone.now()


def test_a_webhook_resume_wakes_the_run(ws, world, advanced):
    from rest_framework.test import APIClient

    from runs import resume

    run = make_run(ws, world, [node("w", "wait", seconds=3600, resume="webhook")])
    tick_and_get(run)
    sr = StepRun.objects.get(run=run, node_id="w")
    advanced.clear()
    orchestrator.tick()
    assert advanced == []
    r = APIClient().post(f"/api/v1/hooks/resume/{resume.token_for(sr.pk)}/", data="{}", content_type="application/json")
    assert r.status_code == 202, r.content
    assert WorkflowRun.objects.get(pk=run.pk).next_check_at <= timezone.now()


# --- le filet de sécurité -------------------------------------------------------------------------------------------------------------
def test_an_event_nobody_announced_is_still_picked_up_after_the_fallback_delay(ws, world, advanced):
    """Si un chemin oubliait `wake`, le run n'est jamais perdu : il est repris au plus tard à `next_check_at`."""
    run = make_run(ws, world, [node("ap", "approval", title="ok")])
    tick_and_get(run)
    ap = Approval.objects.get()
    Approval.objects.filter(pk=ap.pk).update(status="approved")  # décision enregistrée SANS réveil (chemin « oublié »)
    advanced.clear()
    orchestrator.tick()
    assert advanced == []  # pas encore : le run dort
    WorkflowRun.objects.filter(pk=run.pk).update(
        next_check_at=timezone.now() - timedelta(seconds=1)
    )  # le filet arrive à échéance
    r = tick_and_get(run)
    assert advanced == [str(run.pk)] and r.status == "succeeded"


def test_new_and_terminal_runs(ws, world, advanced):
    run = make_run(ws, world, [node("j", "join")])
    assert run.next_check_at is None  # un run neuf est dû tout de suite
    r = tick_and_get(run)
    assert r.status == "succeeded" and r.next_check_at is None  # un run terminé n'est plus planifié


def test_a_busy_run_is_still_polled_every_tour(ws, world, advanced):
    """Les états qu'on ne sait pas dater (sous-workflow, boucle, HTTP, nouvelle tentative…) gardent le comportement d'avant : repassage à chaque tour."""
    v = publish(ws, "enfant", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    assert v
    run = make_run(ws, world, [node("s", "subworkflow", workflow="enfant")])
    r = tick_and_get(run)
    assert r.next_check_at is None or r.next_check_at <= timezone.now() + timedelta(milliseconds=500)
