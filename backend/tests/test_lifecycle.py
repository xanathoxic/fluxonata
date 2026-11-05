"""AR2.6 — arrêt propre, sonde de santé par process, couverture du rôle « aux » dans la santé de la plateforme."""

import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from core import health
from runs import orchestrator as orch
from runs import registry
from runs import worker as w
from runs.models import ServiceInstance

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_stop():
    w.STOP.clear()
    orch.STOP.clear()
    yield
    w.STOP.clear()
    orch.STOP.clear()


# --- worker : l'arrêt est pris en compte après l'action en cours, jamais pendant -----------------------------------------
@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(w, "close_old_connections", lambda: None)
    monkeypatch.setattr(w.services, "reconcile_expired", lambda: None)


def test_worker_finishes_the_running_action_then_stops_without_taking_another(db, quiet, monkeypatch):
    calls = []

    def process_one(worker_id):
        calls.append("début")
        w.STOP.set()  # le signal arrive PENDANT l'action
        calls.append("fin de l'action")
        return True

    monkeypatch.setattr(w, "process_one", process_one)
    w.run_forever(roles="exec")
    assert calls == ["début", "fin de l'action"]  # une seule prise de tâche, menée jusqu'au bout
    assert ServiceInstance.objects.count() == 0  # et le worker s'est retiré du registre de santé


def test_a_stop_requested_before_the_start_does_nothing_at_all(db, quiet, monkeypatch):
    monkeypatch.setattr(w, "process_one", lambda worker_id: pytest.fail("aucune tâche ne doit être prise"))
    w.STOP.set()
    w.run_forever(roles="exec")


def test_the_pause_wakes_up_immediately_on_stop():
    w.STOP.set()
    t0 = time.monotonic()
    w._pause(30)
    assert time.monotonic() - t0 < 1


def test_background_loops_receive_the_stop_event_and_are_joined(db, quiet, monkeypatch):
    seen = []

    class T:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            seen.append((name, args[1] is w.STOP))

        def start(self):
            pass

        def join(self, timeout=None):
            seen.append(("join", timeout))

    monkeypatch.setattr(w.threading, "Thread", T)
    monkeypatch.setattr(w, "process_one", lambda worker_id: w.STOP.set() or False)
    w.run_forever(roles="exec,aux,ai")
    started = [x for x in seen if x[0] != "join"]
    assert len(started) == 8 and all(is_stop for _, is_stop in started)  # chaque fil de fond reçoit l'événement d'arrêt
    assert len([x for x in seen if x[0] == "join"]) == 8


# --- orchestrateur ---------------------------------------------------------------------------------------------------------
def test_orchestrator_finishes_its_tick_then_stops_and_leaves_the_registry(db, monkeypatch):
    monkeypatch.setattr(orch, "close_old_connections", lambda: None)
    ticks = []
    monkeypatch.setattr(orch, "tick", lambda: ticks.append(1) or orch.STOP.set() or 0)
    registry.beat("orchestrator", orch.INSTANCE)
    orch.run_forever()
    assert ticks == [1] and not ServiceInstance.objects.filter(name=orch.INSTANCE).exists()


def test_two_orchestrators_do_not_overwrite_each_other_in_the_registry(db, monkeypatch):
    registry.beat("orchestrator", "orchestrator-hote-a")
    registry.beat("orchestrator", "orchestrator-hote-b")
    assert ServiceInstance.objects.filter(kind="orchestrator").count() == 2
    assert orch.INSTANCE.startswith("orchestrator-")


# --- sonde de santé d'un process -------------------------------------------------------------------------------------------
def run_check(*args):
    with pytest.raises(SystemExit) as e:
        call_command("healthcheck", *args)
    return e.value.code


def test_healthcheck_passes_for_a_fresh_heartbeat_of_this_container(db, capsys):
    ServiceInstance.objects.create(
        kind="orchestrator", name="o1", hostname=os.uname().nodename, last_seen_at=timezone.now()
    )
    call_command("healthcheck", "--kind", "orchestrator")  # ne lève pas SystemExit : succès
    assert "ok : orchestrator o1" in capsys.readouterr().out


def test_healthcheck_fails_when_the_loop_stopped_beating_even_if_the_process_exists(db, capsys):
    ServiceInstance.objects.create(
        kind="worker", name="w1", hostname=os.uname().nodename, last_seen_at=timezone.now() - timedelta(seconds=120)
    )
    assert run_check("--kind", "worker") == 1
    assert "boucle est bloquée ou arrêtée" in capsys.readouterr().err


def test_healthcheck_fails_when_nothing_is_registered_for_this_container_and_ignores_other_hosts(db, capsys):
    ServiceInstance.objects.create(
        kind="worker", name="ailleurs", hostname="un-autre-conteneur", last_seen_at=timezone.now()
    )
    assert run_check("--kind", "worker") == 1
    assert "aucun process" in capsys.readouterr().err


def test_healthcheck_refuses_an_unknown_kind(db):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("healthcheck", "--kind", "api")


# --- santé de la plateforme : couverture du rôle « aux » ---------------------------------------------------------------------
def items(db_ws):
    return (
        {i["key"]: i for i in health.summary(None)} if db_ws is None else {i["key"]: i for i in health.summary(db_ws)}
    )


def beat(kind, name, roles=None):
    info = {"roles": roles} if roles is not None else {}
    ServiceInstance.objects.update_or_create(
        name=name, defaults={"kind": kind, "hostname": name, "last_seen_at": timezone.now(), "info": info}
    )


def test_a_split_deployment_without_any_aux_process_is_reported(db):
    beat("worker", "exec-seul", ["exec"])
    got = items(None)
    assert (
        got["workers"]["status"] == "ok"
        and got["aux_role"]["status"] == "error"
        and "rôle « aux »" in got["aux_role"]["message"]
    )


def test_an_aux_process_clears_it_and_a_legacy_worker_counts_as_every_role(db):
    beat("worker", "exec-seul", ["exec"])
    beat("worker-aux", "aux", ["aux", "ai"])
    assert "aux_role" not in items(None)
    ServiceInstance.objects.all().delete()
    beat("worker", "ancien")  # pas d'information de rôles : porte tout, comme avant
    assert "aux_role" not in items(None)


def test_a_stale_aux_process_does_not_count(db):
    beat("worker", "exec-seul", ["exec"])
    ServiceInstance.objects.create(
        kind="worker-aux",
        name="vieux",
        hostname="v",
        last_seen_at=timezone.now() - timedelta(minutes=5),
        info={"roles": ["aux"]},
    )
    assert items(None)["aux_role"]["status"] == "error"


# --- vrais process : SIGTERM ------------------------------------------------------------------------------------------------
def spawn(*cmd):
    env = {**os.environ, "ORCH_DB_NAME": connection.settings_dict["NAME"], "ORCH_DEBUG": "1"}
    return subprocess.Popen(
        [sys.executable, "manage.py", *cmd],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_registered(kind, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if ServiceInstance.objects.filter(kind=kind).exists():
            return True
        time.sleep(0.3)
    return False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "cmd, kind", [(("run_orchestrator",), "orchestrator"), (("run_worker", "--roles", "aux"), "worker-aux")]
)
def test_a_real_process_stops_cleanly_on_sigterm(cmd, kind):
    p = spawn(*cmd)
    try:
        assert wait_registered(kind), "le process ne s'est pas enregistré"
        t0 = time.time()
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=30)
        assert p.returncode == 0, out[-400:]  # sortie normale, pas « tué par un signal » (-15)
        assert time.time() - t0 < 25 and "arrêt demandé" in out and "stopped cleanly" in out
        assert not ServiceInstance.objects.filter(
            kind=kind
        ).exists()  # retiré du registre : plus « actif » pendant 30 s
    finally:
        if p.poll() is None:
            p.kill()


# --- pile de production : sondes et délais déclarés -------------------------------------------------------------------------
def test_the_production_stack_declares_a_health_probe_and_a_stop_delay_for_each_backend_role():
    import yaml

    stack = yaml.safe_load((BACKEND.parent / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    for name, kind in (("orchestrator", "orchestrator"), ("worker", "worker")):
        assert stack[name]["healthcheck"]["test"][-3:] == ["healthcheck", "--kind", kind]
        assert stack[name]["stop_grace_period"].endswith("s")
    assert "health/ready" in stack["api"]["healthcheck"]["test"][-1] and stack["api"]["stop_grace_period"]
    assert int(stack["worker"]["stop_grace_period"][:-1]) > int(
        stack["orchestrator"]["stop_grace_period"][:-1]
    )  # une action en cours demande plus qu'un tour
