"""Worker loop: separate OS process (never the web process). Claims, heartbeats, runs, completes."""

import logging
import socket
import threading
import uuid

from django.conf import settings
from django.db import close_old_connections

from core.logging import bind
from vault.injection import InjectionError, resolve_env

from . import registry, services
from .finalize import finalize_attempt
from .models import ServiceInstance
from .runners import get_runner

log = logging.getLogger("fluxonata.worker")


def _heartbeat_loop(execution_id, attempt, stop: threading.Event, cancel: threading.Event):
    interval = max(1.0, settings.ORCH_LEASE_SECONDS / 3)
    cancel_poll = min(1.0, interval)
    last_beat = 0.0
    while not stop.wait(cancel_poll):
        try:
            last_beat += cancel_poll
            if last_beat >= interval:
                last_beat = 0.0
                if not services.heartbeat(execution_id, attempt):
                    return
            if services.cancel_requested(execution_id):
                cancel.set()
        except Exception:  # noqa: BLE001
            log.exception("heartbeat failed")
        finally:
            close_old_connections()


def process_one(worker_id: str, runner=None) -> bool:
    """Claim and process one execution. Returns False when the queue is empty."""
    claimed = services.claim_next(worker_id)
    if claimed is None:
        return False
    execution, attempt = claimed
    with bind(execution_id=str(execution.id), attempt=attempt, workspace=execution.workspace.slug):
        return _process_claimed(execution, attempt, runner)


def _process_claimed(execution, attempt, runner):
    version = execution.action_version
    if execution.limits:  # per-execution override (script tasks); in memory only, versions stay immutable
        version.limits = {**version.limits, **execution.limits}
    log.info("claimed execution=%s attempt=%s", execution.id, attempt)
    try:
        env_secrets, to_mask = resolve_env(execution, version)
    except InjectionError as exc:
        services.complete_attempt(
            execution.id,
            attempt,
            ok=False,
            error={"code": exc.code, "message": exc.message, "next_step": "Vérifiez la connexion dans « Connexions »."},
        )
        return True
    stop, cancel = threading.Event(), threading.Event()
    hb = threading.Thread(target=_heartbeat_loop, args=(execution.id, attempt, stop, cancel), daemon=True)
    hb.start()
    if version.runtime == "ai":  # built-in: never a container, whatever runner was injected
        from ai.runner import AiRunner

        runner = AiRunner(execution)
    try:
        outcome = (runner or get_runner()).run(
            version, execution.inputs, int(version.limits.get("timeout_seconds", 30)), cancel, env_secrets
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("runner crashed")
        from .runners import RunOutcome

        outcome = RunOutcome(False, error={"code": "RUNNER_ERROR", "message": f"Erreur du lanceur : {exc}"[:300]})
    finally:
        stop.set()
    finalize_attempt(execution, attempt, outcome, to_mask)
    return True


# AR2.2 (ADR 0022) — un process worker peut ne porter qu'une partie de ses responsabilités. Sans réglage : TOUT, comme avant (rétrocompatible).
#   exec : exécution des actions en conteneur (boucle principale : prise de tâches, baux, annulation)
#   aux  : étapes HTTP/SQL/SFTP/SMTP/LDAP, interrogation de déclencheurs, listes dynamiques, alertes, rappels sortants, synchronisation Git
#   ai   : tâches de l'assistant (plan, génération, édition) et évaluations
WORKER_ROLES = ("exec", "aux", "ai")
ROLE_THREADS = {  # rôle -> (nom du fil, module qui expose `run_loop(worker_id)`)
    "ai": (("ai-jobs", "ai.jobs"), ("ai-evals", "ai.evals.runner")),
    "aux": (
        ("http-steps", "runs.http_node"),
        ("poll-triggers", "triggers.poller"),
        ("form-lookups", "triggers.lookups"),
        ("alerts", "runs.alerts"),
        ("callbacks", "runs.callbacks"),
        ("git-sync", "workflows.gitsync"),
    ),
}


class RoleError(ValueError):
    """Valeur de ORCH_ROLES / --roles invalide pour un worker (message en français, dit quoi mettre)."""


def parse_roles(value) -> tuple[str, ...]:
    """`""`/None → tous les rôles. Accepte `exec,aux`, `worker-exec,worker-ai`, des espaces. Un rôle inconnu est une erreur franche (jamais ignoré)."""
    items = [
        x.strip().lower()
        for x in (value if isinstance(value, (list, tuple)) else str(value or "").split(","))
        if str(x).strip()
    ]
    if not items:
        return WORKER_ROLES
    out = []
    for item in items:
        role = item.removeprefix("worker-")
        if role not in WORKER_ROLES:
            raise RoleError(
                f"Rôle de worker inconnu : « {item} ». Valeurs possibles : {', '.join(WORKER_ROLES)} "
                "(ou worker-exec, worker-aux, worker-ai), séparées par des virgules."
            )
        if role not in out:
            out.append(role)
    return tuple(out)


STOP = threading.Event()  # posé par SIGTERM/SIGINT (runs/lifecycle.py) : on finit l'action en cours, on n'en prend pas d'autre, puis on sort (AR2.6)
STOP_JOIN_ACTIONS_SECONDS = (
    100  # attente maximale d'une action en cours à l'arrêt (le `stop_grace_period` du compose est de 120 s)
)
STOP_JOIN_SECONDS = 10  # temps laissé aux fils aux/ai pour finir leur tour avant de sortir (ils sont démons : la reprise sur bail rattrape le reste)


def _pause(seconds: float) -> None:
    STOP.wait(seconds)  # se réveille tout de suite à la demande d'arrêt


def _start_threads(roles, worker_id) -> list[threading.Thread]:
    import importlib

    started = []
    for role in roles:
        for name, module in ROLE_THREADS.get(role, ()):
            loop = importlib.import_module(module).run_loop
            t = threading.Thread(target=loop, args=(worker_id, STOP), daemon=True, name=name)
            t.start()
            started.append(t)
    return started


def _exec_slot(worker_id: str, poll_seconds: float) -> None:
    """Un créneau d'exécution supplémentaire (AR4.6) : prend et exécute des actions comme la boucle principale, sans refaire le battement de cœur ni la
    reprise des baux (la boucle principale s'en charge). Les créneaux sont indépendants : bail, battement et annulation sont par exécution."""
    while not STOP.is_set():
        close_old_connections()
        try:
            did = process_one(worker_id)
        except Exception:  # noqa: BLE001
            log.exception("worker slot iteration failed")
            did = False
        if not did:
            _pause(poll_seconds)


def run_forever(poll_seconds: float = 1.0, once: bool = False, roles=None) -> None:
    roles = parse_roles(settings.ORCH_ROLES if roles is None else roles)
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    log.info("worker %s started (runner=%s, roles=%s)", worker_id, settings.ORCH_RUNNER, ",".join(roles))
    if once and "exec" not in roles:
        return  # --once = « vider la file d'actions puis sortir » : rien à faire sans le rôle exec
    threads = (
        [] if once else _start_threads(roles, worker_id)
    )  # these run in the worker, never in the web process; the AI ones need the same sandbox
    # Seul un process qui exécute des actions est un « worker » pour la santé de la plateforme (core/health) : un process aux/ai seul ne doit pas la rassurer.
    kind = "worker" if "exec" in roles else "worker-aux"
    slots = []
    if "exec" in roles and not once:  # ORCH_WORKER_CONCURRENCY actions à la fois (défaut 1 = comportement historique)
        for i in range(1, max(1, min(16, int(settings.ORCH_WORKER_CONCURRENCY)))):
            t = threading.Thread(target=_exec_slot, args=(worker_id, poll_seconds), daemon=True, name=f"exec-{i}")
            t.start()
            slots.append(t)
    try:
        while not STOP.is_set():
            close_old_connections()
            try:
                registry.beat(kind, worker_id, runner=settings.ORCH_RUNNER, roles=list(roles))
                did = False
                if "exec" in roles:
                    services.reconcile_expired()
                    did = process_one(
                        worker_id
                    )  # une action en cours va jusqu'au bout : l'arrêt est pris en compte juste après
            except Exception:  # noqa: BLE001
                log.exception("worker iteration failed")
                did = False
            if once and not did:
                return
            if not did:
                _pause(poll_seconds)
    finally:
        if STOP.is_set():
            for t in (
                slots
            ):  # chacun finit son action en cours (le délai d'arrêt de l'orchestrateur de conteneurs borne l'attente)
                t.join(timeout=STOP_JOIN_ACTIONS_SECONDS)
            for t in threads:
                t.join(timeout=STOP_JOIN_SECONDS / max(1, len(threads)))
            ServiceInstance.objects.filter(
                name=worker_id
            ).delete()  # ne plus apparaître « actif » pendant les 30 s qui suivent un arrêt voulu
            log.info("worker %s stopped cleanly", worker_id)
