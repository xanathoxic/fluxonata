"""Worker loop: separate OS process (never the web process). Claims, heartbeats, runs, completes."""

import logging
import socket
import threading
import time
import uuid

from django.conf import settings
from django.db import close_old_connections

from core.logging import bind
from vault.injection import InjectionError, resolve_env

from . import registry, services
from .finalize import finalize_attempt
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


def run_forever(poll_seconds: float = 1.0, once: bool = False) -> None:
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    log.info("worker %s started (runner=%s)", worker_id, settings.ORCH_RUNNER)
    if not once:  # draft generation runs here too (a thread), never in the web process; it needs the same sandbox
        from ai import jobs

        threading.Thread(target=jobs.run_loop, args=(worker_id,), daemon=True, name="ai-jobs").start()
        from . import http_node

        threading.Thread(target=http_node.run_loop, args=(worker_id,), daemon=True, name="http-steps").start()
        from triggers import poller

        threading.Thread(target=poller.run_loop, args=(worker_id,), daemon=True, name="poll-triggers").start()
        from . import alerts

        threading.Thread(target=alerts.run_loop, args=(worker_id,), daemon=True, name="alerts").start()
        from ai.evals import runner as eval_runner

        threading.Thread(target=eval_runner.run_loop, args=(worker_id,), daemon=True, name="ai-evals").start()
        from workflows import gitsync

        threading.Thread(target=gitsync.run_loop, args=(worker_id,), daemon=True, name="git-sync").start()
    while True:
        close_old_connections()
        try:
            registry.beat("worker", worker_id, runner=settings.ORCH_RUNNER)
            services.reconcile_expired()
            did = process_one(worker_id)
        except Exception:  # noqa: BLE001
            log.exception("worker iteration failed")
            did = False
        if once and not did:
            return
        if not did:
            time.sleep(poll_seconds)
