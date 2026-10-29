"""Schedule firing (called from the orchestrator loop). Idempotent per (schedule, due instant)."""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from audit.models import record
from runs import engine
from runs.models import WorkflowRun
from tenancy.models import Membership
from tenancy.permissions import ROLE_PERMISSIONS, RUN

from . import cron
from .models import Schedule

log = logging.getLogger("fluxonata.scheduler")
ACTIVE = ("running", "compensating")


def _note(sch: Schedule, text: str) -> None:
    sch.last_note = text[:200]
    record("schedule." + text.split(":")[0], actor=sch.created_by, workspace=sch.workspace, target=sch, note=text)


def fire_due(now=None) -> int:
    now = now or timezone.now()
    fired = 0
    ids = list(
        Schedule.objects.filter(enabled=True, next_run_at__lte=now, workspace__archived_at__isnull=True).values_list(
            "id", flat=True
        )
    )
    for sid in ids:
        with transaction.atomic():
            sch = (
                Schedule.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("workflow", "workspace", "environment", "created_by", "last_run")
                .filter(pk=sid, enabled=True, next_run_at__lte=now, workspace__archived_at__isnull=True)
                .first()
            )
            if sch is None:
                continue
            due = sch.next_run_at
            # next occurrence strictly after both `now` and the due instant: missed occurrences are coalesced
            sch.next_run_at = cron.next_fire(sch.cron, max(now, due), sch.timezone)
            missed = (now - due) > timedelta(seconds=sch.grace_seconds)
            if missed and sch.missed_policy == Schedule.Missed.SKIP:
                _note(sch, f"missed_skipped: occurrence du {due:%Y-%m-%d %H:%M} UTC ignorée (service arrêté ?)")
            elif (
                sch.overlap_policy == Schedule.Overlap.SKIP
                and sch.last_run is not None
                and sch.last_run.status in ACTIVE
            ):
                _note(
                    sch,
                    f"overlap_skipped: occurrence du {due:%Y-%m-%d %H:%M} UTC ignorée, l'exécution précédente tourne encore",
                )
            elif not _owner_can_run(sch):
                sch.enabled = False
                _note(sch, "disabled: le propriétaire n'a plus le droit de lancer ce workflow")
            else:
                version = sch.workflow.latest_version()
                if version is None:
                    _note(sch, "skipped_unpublished: workflow non publié")
                else:
                    try:
                        run, _ = engine.start_run(
                            workspace=sch.workspace,
                            environment=sch.environment,
                            workflow_version=version,
                            user=sch.created_by,
                            inputs=sch.inputs,
                            idempotency_key=f"sched-{sch.pk}-{due.isoformat()}",
                            trigger="schedule",
                        )
                    except Exception as exc:  # noqa: BLE001 - e.g. governance refusal: keep the schedule alive
                        _note(sch, f"refused: {getattr(exc, 'detail', exc)}")
                    else:
                        sch.last_run, sch.last_run_at = run, now
                        sch.last_note = ""
                        fired += 1
            sch.save()
    return fired


def _owner_can_run(sch: Schedule) -> bool:
    if sch.created_by is None or not sch.created_by.is_active:
        return False
    m = Membership.objects.filter(user=sch.created_by, workspace=sch.workspace).first()
    return m is not None and RUN in ROLE_PERMISSIONS[m.role]


def set_next(sch: Schedule, after=None) -> None:
    sch.next_run_at = cron.next_fire(sch.cron, after or timezone.now(), sch.timezone)


def active_count(sch: Schedule) -> int:
    return WorkflowRun.objects.filter(pk=sch.last_run_id, status__in=ACTIVE).count()
