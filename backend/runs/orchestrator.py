"""Orchestrator loop: separate OS process. Advances running workflow runs and reconciles orphaned leases.

Stateless by design: everything is recomputed from PostgreSQL, so a crash/restart loses nothing.
"""

import logging
import socket
import threading

from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from core.logging import bind

from . import engine, registry, services
from .models import ServiceInstance, WorkflowRun

log = logging.getLogger("fluxonata.orchestrator")
STOP = (
    threading.Event()
)  # posé par SIGTERM/SIGINT (runs/lifecycle.py) : on finit le tour en cours, puis on sort (AR2.6)
INSTANCE = f"orchestrator-{socket.gethostname()}"  # un nom par process : plusieurs orchestrateurs ne s'écrasent plus dans le registre (AR4.1)


def tick() -> int:
    """One pass over active runs. Returns the number of runs processed."""
    registry.beat("orchestrator", INSTANCE)
    services.reconcile_expired()
    from triggers import scheduler

    scheduler.fire_due()
    now = timezone.now()
    ids = list(  # seulement les runs « à repasser » (AR4.4) : un run qui attend une minuterie ou une décision n'est pas touché à chaque tour
        WorkflowRun.objects.filter(status__in=["running", "compensating"])
        .filter(Q(next_check_at__isnull=True) | Q(next_check_at__lte=now))
        .order_by("-priority", "created_at")
        .values_list("id", flat=True)[:500]
    )
    done = 0
    for run_id in ids:
        try:
            with bind(run_id=str(run_id)):
                done += bool(engine.advance(run_id, only_if_due=True))
        except Exception:  # noqa: BLE001
            log.exception("advance failed for run %s", run_id)
    return done


def run_forever(poll_seconds: float = 0.5, once: bool = False) -> None:
    log.info("orchestrator started")
    try:
        while not STOP.is_set():
            close_old_connections()
            try:
                n = tick()
            except Exception:  # noqa: BLE001
                log.exception("orchestrator tick failed")
                n = 0
            if once:
                return
            STOP.wait(poll_seconds if not n else 0.1)
    finally:
        if STOP.is_set():
            ServiceInstance.objects.filter(
                name=INSTANCE
            ).delete()  # un orchestrateur arrêté proprement n'est pas « vivant » pendant 30 s de plus
            log.info("orchestrator stopped cleanly")
