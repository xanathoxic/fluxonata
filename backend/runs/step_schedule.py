"""Nœud « Programmer » (ADR 0034) : programme le lancement d'un autre workflow à une date et une heure, puis laisse le workflow en cours continuer.

S'exécute dans l'orchestrateur, comme « Cache » : de la base de données seulement, jamais de réseau ni de code utilisateur. L'étape ne lance
rien : elle crée une ligne `ScheduledLaunch`, que `runs.scheduled_launch.fire_due` démarre à l'heure dite, avec l'identité de la personne qui
avait lancé le run (et en revérifiant son droit à ce moment-là). Rejouer l'étape ne programme jamais deux fois (contrainte d'unicité)."""

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from audit.models import record
from tenancy import governance
from workflows import schedule_spec as spec
from workflows.expressions import ExpressionError, resolve
from workflows.validation import resolve_workflow_version

from .models import ScheduledLaunch


class ScheduleStepError(Exception):
    def __init__(self, code, message, next_step="Corrigez la configuration puis relancez."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def _when(node, ctx, now):
    """L'instant du lancement (UTC), et si on demande « tout de suite » parce que l'heure est déjà passée."""
    tz = node.get("timezone") or spec.DEFAULT_TIMEZONE
    try:
        if node.get("when") == "in":
            when = spec.in_delay(now, node.get("delay_seconds"))
        else:
            when = spec.parse_when(resolve(node.get("at", ""), ctx), tz)
    except (spec.WhenError, ExpressionError) as exc:
        raise ScheduleStepError("SCHEDULE_WHEN", str(exc)) from None
    return _bound(when, now, tz, node.get("on_past", "fail"), "SCHEDULE")


def _bound(when, now, tz, on_past, prefix):
    """Refuse une date passée (sauf « tout de suite ») ou trop lointaine ; partagé avec l'attente jusqu'à une date (`resume_at`)."""
    if when <= now:
        if on_past == "fail":
            raise ScheduleStepError(
                f"{prefix}_PAST",
                f"Cette date est déjà passée ({when.astimezone(spec.zone(tz)):%d/%m/%Y %H:%M}).",
                "Choisissez une date future, ou réglez « Si la date est passée » sur « continuer tout de suite ».",
            )
        return now, True
    if (when - now).total_seconds() > spec.MAX_HORIZON_SECONDS:
        raise ScheduleStepError(f"{prefix}_HORIZON", "On ne peut pas viser une date à plus de 366 jours.")
    return when, False


def resume_at(node, ctx, now):
    """Nœud « Attente » réglé sur une date (`until`) : l'instant de reprise (UTC), et si l'on reprend tout de suite car la date est passée."""
    tz = node.get("timezone") or spec.DEFAULT_TIMEZONE
    try:
        when = spec.parse_when(resolve(node.get("until", ""), ctx), tz)
    except (spec.WhenError, ExpressionError) as exc:
        raise ScheduleStepError("WAIT_UNTIL", str(exc)) from None
    return _bound(when, now, tz, node.get("on_past", "fail"), "WAIT")


def _target(run, node, ctx):
    wf, version = resolve_workflow_version(run.workspace, node.get("workflow"), node.get("version"))
    if wf is None or version is None:
        raise ScheduleStepError(
            "SCHEDULE_TARGET", f"Le workflow « {node.get('workflow')} » est introuvable ou n'a aucune version publiée."
        )
    if wf.pk == run.workflow_version.workflow_id:
        raise ScheduleStepError("SCHEDULE_TARGET", "Un workflow ne peut pas programmer son propre lancement.")
    try:
        from .serializers import first_problem, validate_inputs_against, with_defaults

        inputs = with_defaults(version.input_schema, resolve(node.get("inputs") or {}, ctx))
    except ExpressionError as exc:
        raise ScheduleStepError("SCHEDULE_INPUTS", str(exc)) from None
    if problems := validate_inputs_against(version.input_schema, inputs):
        raise ScheduleStepError(
            "SCHEDULE_INPUTS", f"Entrées du workflow programmé invalides — {first_problem(problems)}"
        )
    return wf, version, inputs


def apply(run, node, ctx) -> dict:
    now = timezone.now()
    existing = ScheduledLaunch.objects.filter(source_run=run, source_node=node["id"]).select_related("workflow").first()
    if existing is not None:  # étape rejouée : on rend la même réponse
        return _output(existing, existing.version, immediate=False)
    when, immediate = _when(node, ctx, now)
    wf, version, inputs = _target(run, node, ctx)
    try:  # refus anticipé : mieux vaut le savoir maintenant que le jour dit (la politique de l'espace est revérifiée au lancement)
        governance.authorize_run(
            workspace=run.workspace,
            environment=run.environment,
            workflow_version=version,
            user=run.requested_by,
            trigger="scheduled",
        )
    except PermissionDenied as exc:
        raise ScheduleStepError("SCHEDULE_REFUSED", str(getattr(exc, "detail", exc))) from None
    try:
        with transaction.atomic():
            pending = ScheduledLaunch.objects.select_for_update().filter(
                workspace=run.workspace, environment=run.environment, status=ScheduledLaunch.Status.PENDING
            )
            if pending.count() >= spec.MAX_PENDING:
                raise ScheduleStepError(
                    "SCHEDULE_QUOTA",
                    f"Trop de lancements programmés en attente ({spec.MAX_PENDING} par espace et environnement).",
                    "Annulez des lancements inutiles dans « Déclencheurs ».",
                )
            launch = ScheduledLaunch.objects.create(
                workspace=run.workspace,
                environment=run.environment,
                workflow=wf,
                version=str(node.get("version") or "latest"),
                inputs=inputs,
                run_at=when,
                timezone=node.get("timezone") or spec.DEFAULT_TIMEZONE,
                requested_by=run.requested_by,
                source_run=run,
                source_node=node["id"],
            )
    except IntegrityError:  # une autre passe de la même étape vient de la programmer
        launch = ScheduledLaunch.objects.get(source_run=run, source_node=node["id"])
        return _output(launch, launch.version, immediate=False)
    record(
        "scheduled_launch.create",
        actor=run.requested_by,
        workspace=run.workspace,
        target=launch,
        workflow=wf.slug,
        run_at=when.isoformat(),
        source_run=str(run.pk),
    )
    return _output(launch, version.version, immediate)


def _output(launch, version, immediate) -> dict:
    local = launch.run_at.astimezone(spec.zone(launch.timezone))
    return {
        "id": str(launch.pk),
        "workflow": launch.workflow.slug,
        "version": str(version),
        "run_at": launch.run_at.isoformat(),
        "run_at_local": local.isoformat(),
        "immediate": immediate,
    }
