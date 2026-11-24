"""Lancement des workflows programmés par le nœud « Programmer » (ADR 0034). Appelé par la boucle de l'orchestrateur, à chaque tour.

Idempotent : une ligne n'est lancée qu'une fois (verrou de ligne, clé d'idempotence du run). Un lancement en retard (service arrêté à l'heure
dite) est **quand même fait**, avec une note ; contrairement à un planning récurrent, une date unique manquée ne se rattrape pas toute seule
plus tard. Le droit de la personne est revérifié à l'heure dite : si elle l'a perdu, le lancement est refusé et dit pourquoi."""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from audit.models import record
from tenancy.models import Membership
from tenancy.permissions import ROLE_PERMISSIONS, RUN
from workflows.validation import resolve_workflow_version

from . import engine
from .models import ScheduledLaunch

log = logging.getLogger("fluxonata.scheduled_launch")
LATE = timedelta(minutes=5)


def _can_run(launch) -> bool:
    user = launch.requested_by
    if user is None:  # lancé par un déclencheur sans personne (webhook, formulaire public) : comme les sous-workflows
        return True
    if not user.is_active:
        return False
    m = Membership.objects.filter(user=user, workspace=launch.workspace).first()
    return m is not None and RUN in ROLE_PERMISSIONS[m.role]


def _refuse(launch, why: str) -> None:
    launch.status = ScheduledLaunch.Status.REFUSED
    launch.note = why[:300]
    record(
        "scheduled_launch.refused",
        actor=launch.requested_by,
        workspace=launch.workspace,
        target=launch,
        note=launch.note,
    )


def _fire(launch, now) -> bool:
    if not _can_run(launch):
        _refuse(launch, "La personne qui avait programmé ce lancement n'a plus le droit de lancer ce workflow.")
        return False
    _, version = resolve_workflow_version(launch.workspace, launch.workflow.slug, launch.version)
    if version is None:
        _refuse(launch, "Le workflow n'a plus de version publiée à lancer.")
        return False
    try:
        run, _ = engine.start_run(
            workspace=launch.workspace,
            environment=launch.environment,
            workflow_version=version,
            user=launch.requested_by,
            inputs=launch.inputs,
            idempotency_key=f"launch-{launch.pk}",
            trigger="scheduled",
        )
    except Exception as exc:  # noqa: BLE001 - refus de gouvernance, entrées devenues invalides… : le dire, ne pas boucler
        _refuse(launch, f"Lancement refusé : {getattr(exc, 'detail', exc)}")
        return False
    late = now - launch.run_at
    launch.status, launch.fired_run, launch.fired_at = ScheduledLaunch.Status.FIRED, run, now
    if late > LATE:
        launch.note = f"Lancé avec {int(late.total_seconds() // 60)} min de retard (service arrêté à l'heure prévue ?)."
    record(
        "scheduled_launch.fired", actor=launch.requested_by, workspace=launch.workspace, target=launch, run=str(run.pk)
    )
    return True


def fire_due(now=None) -> int:
    now = now or timezone.now()
    ids = list(
        ScheduledLaunch.objects.filter(
            status=ScheduledLaunch.Status.PENDING, run_at__lte=now, workspace__archived_at__isnull=True
        ).values_list("id", flat=True)[:100]
    )
    fired = 0
    for pk in ids:
        with transaction.atomic():
            launch = (
                ScheduledLaunch.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("workflow", "workspace", "environment", "requested_by")
                .filter(pk=pk, status=ScheduledLaunch.Status.PENDING, run_at__lte=now)
                .first()
            )
            if launch is None:
                continue
            fired += _fire(launch, now)
            launch.save()
    return fired
