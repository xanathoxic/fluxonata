"""AR4.2 — l'orchestrateur n'a besoin ni de la clé du coffre ni d'un réseau sortant : les rappels sortants partent du rôle « aux » du worker.

On fait tourner le tour de l'orchestrateur sur des runs réels (réussi, en échec avec alerte, avec rappel sortant) pendant que le chiffrement lève une erreur
dès qu'on le touche, et que toute tentative de connexion sortante échoue. L'exécution des actions (côté worker) et la livraison (rôle aux) ont lieu HORS de
cette garde, comme dans le vrai déploiement.
"""

import contextlib
import threading

import pytest

from core import netsec
from runs import alerts, callbacks, orchestrator
from runs.models import AlertRule, CallbackDelivery, WorkflowRun
from runs.runners import SubprocessRunner
from runs.worker import process_one
from tenancy.models import Environment
from tests.test_ops import RECEIVED, sink  # noqa: F401 (fixture)
from tests.wf_helpers import ACTIONS, launch, make_action, node, publish


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


VIOLATIONS: list = []  # chaque tentative est ENREGISTRÉE : le code appelé peut avaler l'exception (deliver_due le fait), pas la trace


@contextlib.contextmanager
def no_key_no_network(monkeypatch):
    from vault import crypto

    def forbidden(*a, **k):
        VIOLATIONS.append("clé du coffre ou réseau sortant")
        raise AssertionError("l'orchestrateur a touché à la clé du coffre ou au réseau sortant")

    with monkeypatch.context() as m:
        for name in ("encrypt", "decrypt", "_fernet", "current_key_id", "new_key"):
            m.setattr(crypto, name, forbidden)
        for name in ("safe_request", "open_connection"):
            m.setattr(netsec, name, forbidden)
        yield


def drive(monkeypatch, runs, rounds=60):
    VIOLATIONS.clear()
    for _ in range(rounds):
        with no_key_no_network(monkeypatch):
            orchestrator.tick()  # le tour de l'orchestrateur, sous garde
        process_one("w", SubprocessRunner())  # le worker exécute (hors garde)
        if all(WorkflowRun.objects.get(pk=r.pk).status in ("succeeded", "failed", "cancelled") for r in runs):
            for _ in range(
                2
            ):  # l'orchestrateur continue de tourner après la fin des runs : c'est là qu'il aurait livré les rappels
                with no_key_no_network(monkeypatch):
                    orchestrator.tick()
            assert VIOLATIONS == [], VIOLATIONS  # le tour de l'orchestrateur n'a rien touché d'interdit
            return [WorkflowRun.objects.get(pk=r.pk) for r in runs]
    raise AssertionError("les runs ne se terminent pas")


def test_the_orchestrator_tick_never_touches_the_key_nor_the_network(ws, world, sink, monkeypatch):  # noqa: F811
    from tenancy.models import Role
    from tests.conftest import client_for, make_user

    admin = client_for(make_user("ada-admin", ws, Role.ADMIN))
    assert (
        admin.post(
            "/api/v1/workspaces/team-a/ops/alerts/",
            {"name": "KO", "event": "run_failed", "environment": "test", "url": sink},
            format="json",
        ).status_code
        == 201
    )
    ok = publish(
        ws, "ok", [node("a", action="echo", inputs={"value": "x"}), node("j", "join")], [], user=world["designer"]
    )
    ko = publish(ws, "ko", [node("s", action="boom")], [], user=world["designer"])
    from runs import engine

    env = Environment.objects.get(workspace=ws, kind="test")
    runs = [
        launch(ws, ok, world["designer"]),
        launch(ws, ko, world["designer"]),
        engine.start_run(
            workspace=ws, environment=env, workflow_version=ok, user=world["designer"], inputs={}, callback_url=sink
        )[0],
    ]
    done = drive(monkeypatch, runs)
    assert [r.status for r in done] == ["succeeded", "failed", "succeeded"]
    # l'orchestrateur a PRÉPARÉ le rappel et l'alerte (lignes en base) sans rien envoyer
    assert CallbackDelivery.objects.filter(status="pending").count() == 1 and RECEIVED == []
    assert AlertRule.objects.count() == 1 and alerts.AlertDelivery.objects.count() == 1


def test_the_aux_role_delivers_what_the_orchestrator_prepared(ws, world, sink, monkeypatch):  # noqa: F811
    from runs import engine

    ok = publish(ws, "ok", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    env = Environment.objects.get(workspace=ws, kind="test")
    run = engine.start_run(
        workspace=ws, environment=env, workflow_version=ok, user=world["designer"], inputs={}, callback_url=sink
    )[0]
    drive(monkeypatch, [run])
    assert RECEIVED == [] and CallbackDelivery.objects.get().status == "pending"
    assert callbacks.deliver_due() == 1  # ce que fait le fil « callbacks » du worker (rôle aux)
    assert len(RECEIVED) == 1 and CallbackDelivery.objects.get().status == "delivered"


def test_the_callbacks_loop_stops_on_request_and_keeps_going_after_an_error(db, monkeypatch):
    calls = []
    stop = threading.Event()

    def deliver():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("point d'arrivée en panne")
        stop.set()
        return 0

    monkeypatch.setattr(callbacks, "deliver_due", deliver)
    monkeypatch.setattr(callbacks, "close_old_connections", lambda: None)
    callbacks.run_loop("w", stop, poll_seconds=0.01)
    assert len(calls) == 2  # l'erreur n'a pas arrêté la boucle


def test_the_tick_does_not_import_the_callbacks_module_anymore():
    import inspect

    assert "callbacks" not in inspect.getsource(orchestrator.tick)
