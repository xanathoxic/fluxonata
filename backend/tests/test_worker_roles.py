"""AR2.2 — rôles d'un process worker (ORCH_ROLES / --roles). Sans réglage, le comportement historique est strictement conservé."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from runs import worker as w
from runs.models import ServiceInstance

HISTORICAL_THREADS = {
    "ai-jobs",
    "http-steps",
    "poll-triggers",
    "form-lookups",
    "alerts",
    "callbacks",
    "ai-evals",
    "git-sync",
}


class Stop(Exception):
    pass


@pytest.fixture
def probe(monkeypatch, settings, db):
    """Remplace les fils et la prise de tâches : on observe ce que `run_forever` démarre et appelle, sans rien exécuter."""
    settings.ORCH_ROLES = ""
    seen = {"threads": [], "process_one": 0, "reconcile": 0}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            seen["threads"].append(name)

        def start(self):
            pass

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(w.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        w, "close_old_connections", lambda: None
    )  # en production elle ferme la connexion ; ici ce serait celle de la transaction de test
    monkeypatch.setattr(
        w, "process_one", lambda worker_id: seen.__setitem__("process_one", seen["process_one"] + 1) or False
    )
    monkeypatch.setattr(w.services, "reconcile_expired", lambda: seen.__setitem__("reconcile", seen["reconcile"] + 1))
    monkeypatch.setattr(w, "_pause", lambda s: (_ for _ in ()).throw(Stop()))  # une seule itération, puis on sort
    w.STOP.clear()
    return seen


def run(**kw):
    with pytest.raises(Stop):
        w.run_forever(**kw)


# --- analyse de la valeur -----------------------------------------------------------------------------------------------
def test_no_value_means_every_role():
    assert w.parse_roles("") == w.parse_roles(None) == w.WORKER_ROLES == ("exec", "aux", "ai")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("exec", ("exec",)),
        (" aux , ai ", ("aux", "ai")),
        ("worker-exec,worker-ai", ("exec", "ai")),
        ("AI,ai,Ai", ("ai",)),
        ("exec,,", ("exec",)),
        (["aux"], ("aux",)),
    ],
)
def test_aliases_spaces_case_and_duplicates(value, expected):
    assert w.parse_roles(value) == expected


@pytest.mark.parametrize("bad", ["execution", "api-ui", "runner-gw", "exec,orchestrator", "worker-"])
def test_an_unknown_role_is_an_error_that_says_what_to_write(bad):
    with pytest.raises(w.RoleError, match="Valeurs possibles : exec, aux, ai"):
        w.parse_roles(bad)


# --- ce que le process démarre -------------------------------------------------------------------------------------------
def test_default_is_exactly_the_historical_behaviour(probe):
    run()
    assert set(probe["threads"]) == HISTORICAL_THREADS and len(probe["threads"]) == 8
    assert probe["process_one"] == 1 and probe["reconcile"] == 1
    inst = ServiceInstance.objects.get()
    assert inst.kind == "worker" and inst.info["roles"] == ["exec", "aux", "ai"]


def test_exec_only_starts_no_background_thread(probe):
    run(roles="exec")
    assert probe["threads"] == [] and probe["process_one"] == 1 and probe["reconcile"] == 1


@pytest.mark.parametrize(
    "roles, threads",
    [
        ("aux", {"http-steps", "poll-triggers", "form-lookups", "alerts", "callbacks", "git-sync"}),
        ("ai", {"ai-jobs", "ai-evals"}),
        ("aux,ai", HISTORICAL_THREADS),
    ],
)
def test_a_process_without_exec_never_executes_actions_and_is_not_counted_as_a_worker(probe, roles, threads):
    run(roles=roles)
    assert set(probe["threads"]) == threads
    assert probe["process_one"] == 0 and probe["reconcile"] == 0  # ni prise de tâche, ni reprise de baux
    inst = ServiceInstance.objects.get()
    assert inst.kind == "worker-aux"  # core/health compte `kind="worker"` : un process aux/ai seul ne doit pas rassurer


def test_exec_plus_one_family(probe):
    run(roles="exec,ai")
    assert set(probe["threads"]) == {"ai-jobs", "ai-evals"} and probe["process_one"] == 1


def test_the_setting_is_used_when_no_argument_is_given(probe, settings):
    settings.ORCH_ROLES = "worker-ai"
    run()
    assert set(probe["threads"]) == {"ai-jobs", "ai-evals"} and probe["process_one"] == 0


def test_the_argument_overrides_the_setting(probe, settings):
    settings.ORCH_ROLES = "ai"
    run(roles="exec")
    assert probe["threads"] == [] and probe["process_one"] == 1


def test_once_without_exec_returns_immediately_and_touches_nothing(probe):
    w.run_forever(once=True, roles="aux")
    assert probe == {"threads": [], "process_one": 0, "reconcile": 0}


def test_once_with_exec_still_drains_and_exits_without_threads(probe):
    w.run_forever(once=True, roles="exec")
    assert probe["threads"] == [] and probe["process_one"] == 1


# --- la commande ---------------------------------------------------------------------------------------------------------
def test_command_refuses_a_bad_option_and_a_bad_setting_before_starting(settings, probe):
    with pytest.raises(CommandError, match="Rôle de worker inconnu"):
        call_command("run_worker", roles="nimporte")
    settings.ORCH_ROLES = "api-ui"  # un rôle d'API sur un worker
    with pytest.raises(CommandError, match="Rôle de worker inconnu"):
        call_command("run_worker")
    assert probe["threads"] == [] and probe["process_one"] == 0


# --- AR4.6 : plusieurs actions à la fois dans un même process ---------------------------------------------------------------------------
def test_concurrency_one_is_the_historical_behaviour(probe, settings):
    settings.ORCH_WORKER_CONCURRENCY = 1
    run(roles="exec")
    assert probe["threads"] == [] and probe["process_one"] == 1


def test_extra_execution_slots_are_started_and_stopped_with_the_worker(probe, settings, monkeypatch):
    settings.ORCH_WORKER_CONCURRENCY = 3
    run(roles="exec")
    assert probe["threads"] == ["exec-1", "exec-2"]  # 2 créneaux en plus de la boucle principale = 3 actions à la fois


def test_slots_only_exist_for_the_exec_role(probe, settings):
    settings.ORCH_WORKER_CONCURRENCY = 4
    run(roles="aux")
    assert not [t for t in probe["threads"] if t.startswith("exec-")]


def test_concurrency_is_clamped(probe, settings):
    settings.ORCH_WORKER_CONCURRENCY = 500
    run(roles="exec")
    assert len([t for t in probe["threads"] if t.startswith("exec-")]) == 15  # 16 au plus
    probe["threads"].clear()
    settings.ORCH_WORKER_CONCURRENCY = 0
    run(roles="exec")
    assert probe["threads"] == []


def test_slots_really_run_in_parallel_and_stop_after_their_action(db, monkeypatch, settings):
    """Trois créneaux, chacun bloqué dans `process_one` : ils s'exécutent bien EN MÊME TEMPS, puis s'arrêtent sans en reprendre une autre."""
    import threading

    monkeypatch.setattr(w, "close_old_connections", lambda: None)
    settings.ORCH_WORKER_CONCURRENCY = 3
    inside, calls, lock = threading.Barrier(2), [], threading.Lock()
    release = threading.Event()

    def process_one(worker_id):
        with lock:
            calls.append(threading.current_thread().name)
        if threading.current_thread().name.startswith("exec-"):
            inside.wait(timeout=5)  # les deux créneaux supplémentaires sont dans l'action en même temps
            release.wait(timeout=5)
        return True if not w.STOP.is_set() else False

    monkeypatch.setattr(w, "process_one", process_one)
    monkeypatch.setattr(w.services, "reconcile_expired", lambda: None)
    t = threading.Thread(target=lambda: w.run_forever(roles="exec", poll_seconds=0.01))
    t.start()
    for _ in range(100):
        if len([c for c in calls if c.startswith("exec-")]) >= 2:
            break
        threading.Event().wait(0.05)
    w.STOP.set()
    release.set()
    t.join(timeout=20)
    assert not t.is_alive()
    assert {c for c in calls if c.startswith("exec-")} == {"exec-1", "exec-2"}
