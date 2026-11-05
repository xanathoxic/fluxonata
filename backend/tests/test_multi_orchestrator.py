"""AR4.1 / AR4.5 — plusieurs orchestrateurs sur la même base : chaque étape démarre UNE fois, chaque run se termine, et les orchestrateurs ne refont pas le travail des autres.

Vrais fils, vraies connexions (transaction=True), `SELECT … FOR UPDATE SKIP LOCKED` réel : pas de simulation du verrou.
"""

import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from runs import engine
from runs.models import Execution, RunEvent, StepRun, WorkflowRun
from runs.runners import SubprocessRunner
from runs.worker import process_one
from tests.wf_helpers import ACTIONS, edge, launch, make_action, node, publish

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def test_only_if_due_skips_a_run_another_orchestrator_just_handled(ws, world):
    v = publish(ws, "w", [node("p", "wait", seconds=3600)], [], user=world["designer"])
    run = launch(ws, v, world["designer"])
    assert engine.advance(run.pk, only_if_due=True) is True  # neuf : dû
    assert (
        engine.advance(run.pk, only_if_due=True) is False
    )  # traité à l'instant par « un autre » : rien à faire, rien d'écrit
    assert engine.advance(run.pk) is True  # un appel direct (tests, reprises) reste inconditionnel
    WorkflowRun.objects.filter(pk=run.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
    assert engine.advance(run.pk, only_if_due=True) is True  # redevenu dû


def spin(stop, counters, name):
    """Un orchestrateur : tour après tour, avec sa propre connexion à la base."""
    try:
        while not stop.is_set():
            ids = list(WorkflowRun.objects.filter(status="running").values_list("id", flat=True))
            for rid in ids:
                counters[name]["calls"] += 1
                try:
                    if engine.advance(rid, only_if_due=True):
                        counters[name]["done"] += 1
                except Exception as exc:  # noqa: BLE001 - le vrai tour (orchestrator.tick) journalise et continue ; ici on COMPTE : aucune erreur ne doit survenir
                    counters[name].setdefault("errors", []).append(f"{type(exc).__name__}: {str(exc)[:100]}")
            stop.wait(0.02)
    finally:
        connection.close()


@pytest.mark.parametrize("n_orchestrators", [2, 4])
def test_concurrent_orchestrators_start_every_step_exactly_once(ws, world, n_orchestrators):
    designer = world["designer"]
    simple = publish(
        ws,
        "simple",
        [node("a", "join"), node("e", action="echo", inputs={"value": "x"}), node("z", "join")],
        [edge("a", "e"), edge("e", "z")],
        user=designer,
    )
    waits = publish(
        ws,
        "waits",
        [node("a", "join"), node("w", "wait", seconds=1), node("z", "join")],
        [edge("a", "w"), edge("w", "z")],
        user=designer,
    )
    runs = [launch(ws, simple if i % 2 else waits, designer) for i in range(16)]
    stop = threading.Event()
    counters = {f"o{i}": {"calls": 0, "done": 0} for i in range(n_orchestrators)}
    threads = [threading.Thread(target=spin, args=(stop, counters, name), daemon=True) for name in counters]
    [t.start() for t in threads]
    try:
        deadline = timezone.now() + timedelta(seconds=90)
        while timezone.now() < deadline:
            process_one("w", SubprocessRunner())  # le worker, dans le fil principal
            if not WorkflowRun.objects.filter(status="running").exists():
                break
    finally:
        stop.set()
        [t.join(timeout=15) for t in threads]
    errors = [e for c in counters.values() for e in c.get("errors", [])]
    assert errors == [], (
        f"{len(errors)} erreur(s) de concurrence entre orchestrateurs : {errors[:3]}"
    )  # ni conflit d'unicité ni interblocage
    assert not WorkflowRun.objects.filter(status="running").exists(), "des runs ne se terminent pas"
    assert set(WorkflowRun.objects.values_list("status", flat=True)) == {"succeeded"}
    # chaque étape démarre EXACTEMENT une fois (jamais deux orchestrateurs sur la même transition) ...
    for run in runs:
        started = list(RunEvent.objects.filter(run=run, type="step_started").values_list("node_id", flat=True))
        assert sorted(started) == sorted(set(started)), f"étape démarrée deux fois dans {run.pk} : {started}"
        assert set(started) == {s["id"] for s in run.definition["graph"]["nodes"]}
    assert not StepRun.objects.filter(attempt__gt=1).exists()
    # ... et chaque action a UNE exécution, tentée UNE fois
    assert Execution.objects.count() == 8 and set(Execution.objects.values_list("attempt_count", flat=True)) == {1}
