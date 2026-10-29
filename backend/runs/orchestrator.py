"""Orchestrator loop: separate OS process. Advances running workflow runs and reconciles orphaned leases.

Stateless by design: everything is recomputed from PostgreSQL, so a crash/restart loses nothing.
"""

import logging
import time

from django.db import close_old_connections

from core.logging import bind

from . import engine, registry, services
from .models import WorkflowRun

log = logging.getLogger("fluxonata.orchestrator")


def tick() -> int:
    """One pass over active runs. Returns the number of runs processed."""
    registry.beat("orchestrator", "orchestrator")
    services.reconcile_expired()
    from triggers import scheduler

    scheduler.fire_due()
    from . import callbacks

    callbacks.deliver_due()
    ids = list(
        WorkflowRun.objects.filter(status__in=["running", "compensating"])
        .order_by("-priority", "created_at")
        .values_list("id", flat=True)[:500]
    )
    done = 0
    for run_id in ids:
        try:
            with bind(run_id=str(run_id)):
                done += bool(engine.advance(run_id))
        except Exception:  # noqa: BLE001
            log.exception("advance failed for run %s", run_id)
    return done


def run_forever(poll_seconds: float = 0.5, once: bool = False) -> None:
    log.info("orchestrator started")
    while True:
        close_old_connections()
        try:
            n = tick()
        except Exception:  # noqa: BLE001
            log.exception("orchestrator tick failed")
            n = 0
        if once:
            return
        if not n:
            time.sleep(poll_seconds)
        else:
            time.sleep(0.1)
