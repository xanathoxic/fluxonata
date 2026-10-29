"""Execution state machine and durable claim/lease/fencing logic (PostgreSQL = source of truth).

Transitions: queued -> running -> {succeeded, failed, cancelled}; queued -> cancelled;
running -> queued (lease lost, retry safe). Delivery is at-least-once: a lost attempt of an action with external
side effects is NOT retried blindly (OUTCOME_UNKNOWN).

Claim policy: per-workspace concurrency cap (ORCH_MAX_CONCURRENT_PER_WORKSPACE), then fairness (workspace with
the fewest running executions first), then priority, then age.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.models import record

from .models import Attempt, Execution, ExecutionEvent

S = Execution.Status
ALLOWED = {
    S.QUEUED: {S.RUNNING, S.CANCELLED},
    S.RUNNING: {S.QUEUED, S.SUCCEEDED, S.FAILED, S.CANCELLED},
    S.SUCCEEDED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
}


def _transition(execution: Execution, new: str) -> None:
    if new not in ALLOWED[execution.status]:
        raise ValueError(f"Transition interdite {execution.status} -> {new}")
    execution.status = new


def add_event(execution: Execution, type_: str, message: str, **data) -> ExecutionEvent:
    last = execution.events.order_by("-seq").values_list("seq", flat=True).first() or 0
    return ExecutionEvent.objects.create(
        execution=execution, seq=last + 1, type=type_, message=message[:300], data=data
    )


def enqueue(
    *,
    workspace,
    environment,
    action_version,
    user,
    inputs,
    step_run=None,
    priority=0,
    idempotency_key=None,
    connections=None,
    limits=None,
):
    execution = Execution.objects.create(
        workspace=workspace,
        environment=environment,
        action_version=action_version,
        requested_by=user,
        inputs=inputs,
        step_run=step_run,
        priority=priority,
        connections=connections or {},
        limits=limits,
        idempotency_key=idempotency_key or None,
    )
    add_event(execution, "queued", "Demande enregistrée, en attente d'un worker.")
    return execution


@transaction.atomic
def create_execution(*, workspace, environment, action_version, user, inputs, idempotency_key=None, connections=None):
    """Returns (execution, created). Same idempotency key in the workspace returns the existing run."""
    if idempotency_key:
        existing = Execution.objects.filter(workspace=workspace, idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    try:
        with transaction.atomic():
            execution = enqueue(
                workspace=workspace,
                environment=environment,
                action_version=action_version,
                user=user,
                inputs=inputs,
                idempotency_key=idempotency_key,
                connections=connections,
            )
    except IntegrityError:
        return Execution.objects.get(workspace=workspace, idempotency_key=idempotency_key), False
    record(
        "execution.create",
        actor=user,
        workspace=workspace,
        target=execution,
        action_slug=action_version.action.slug,
        version=action_version.version,
        environment=environment.kind,
    )
    return execution, True


@transaction.atomic
def claim_next(worker_id: str, runner=None):
    """Claim one queued execution (SKIP LOCKED). Returns (execution, attempt_number) or None.

    Who may run what:
    - the built-in workers (`runner=None`) serve the *shared* pool: spaces that allow shared runners, jobs whose required
      tags they carry (ORCH_WORKER_TAGS);
    - a runner agent serves only its scope (the spaces it is dedicated to, or the shared pool), only runtimes it is
      allowed to run, jobs whose required tags it carries, never AI steps, and never more than its own concurrency."""
    now = timezone.now()
    qs = (
        Execution.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("action_version__action")
        .filter(status=S.QUEUED)
    )
    if runner is None:
        qs, tags = qs.filter(workspace__use_shared_runners=True), set(settings.ORCH_WORKER_TAGS)
    else:
        if runner.paused or Execution.objects.filter(runner=runner, status=S.RUNNING).count() >= runner.max_concurrent:
            return None
        scope = (
            Q(workspace__in=runner.workspaces.all())
            if runner.scope == "dedicated"
            else Q(workspace__use_shared_runners=True)
        )
        qs = qs.filter(scope, action_version__runtime__in=runner.runtimes).exclude(action_version__runtime="ai")
        tags = set(runner.tags)
    candidates = [
        c for c in qs.order_by("-priority", "created_at")[:200] if set(c.action_version.required_tags or []) <= tags
    ]
    if not candidates:
        return None
    running = dict(
        Execution.objects.filter(status=S.RUNNING)
        .values_list("workspace_id")
        .annotate(n=Count("id"))
        .values_list("workspace_id", "n")
    )
    cap = settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE
    eligible = [c for c in candidates if running.get(c.workspace_id, 0) < cap]
    if not eligible:
        return None
    # fairness: least-loaded workspace first; stable w.r.t. priority/age thanks to min() keeping first minimum
    execution = min(eligible, key=lambda c: (-c.priority, running.get(c.workspace_id, 0)))
    _transition(execution, S.RUNNING)
    execution.attempt_count += 1
    execution.worker_id = worker_id
    execution.runner = runner
    execution.lease_expires_at = now + timedelta(seconds=settings.ORCH_LEASE_SECONDS)
    execution.started_at = execution.started_at or now
    execution.save()
    Attempt.objects.create(execution=execution, number=execution.attempt_count, worker_id=worker_id)
    add_event(
        execution, "attempt_started", f"Tentative {execution.attempt_count} démarrée.", attempt=execution.attempt_count
    )
    return execution, execution.attempt_count


def heartbeat(execution_id, attempt: int) -> bool:
    """Extend the lease; False if the attempt is no longer the live one (fenced out)."""
    updated = Execution.objects.filter(pk=execution_id, status=S.RUNNING, attempt_count=attempt).update(
        lease_expires_at=timezone.now() + timedelta(seconds=settings.ORCH_LEASE_SECONDS)
    )
    return bool(updated)


def cancel_requested(execution_id) -> bool:
    return Execution.objects.filter(pk=execution_id, cancel_requested=True).exists()


@transaction.atomic
def request_cancel(execution: Execution) -> None:
    """Cooperative first: queued runs cancel immediately; running ones get a flag that the worker honours
    (container stopped with a grace period, then killed). Effects already produced are not undone."""
    ex = Execution.objects.select_for_update().get(pk=execution.pk)
    if ex.status == S.QUEUED:
        _transition(ex, S.CANCELLED)
        ex.finished_at = timezone.now()
        ex.error = {"code": "CANCELLED", "message": "Annulée avant démarrage."}
        ex.save()
        add_event(ex, "cancelled", "Annulée avant démarrage.")
    elif ex.status == S.RUNNING and not ex.cancel_requested:
        ex.cancel_requested = True
        ex.save()
        add_event(ex, "cancel_requested", "Annulation demandée, arrêt en cours.")


@transaction.atomic
def complete_attempt(
    execution_id, attempt: int, *, ok: bool, result=None, error=None, logs="", exit_code=None, cancelled=False
) -> bool:
    """Record an attempt outcome. A late answer from an old attempt is rejected (fencing token)."""
    execution = Execution.objects.select_for_update().get(pk=execution_id)
    if execution.status != S.RUNNING or execution.attempt_count != attempt:
        Attempt.objects.filter(execution=execution, number=attempt, finished_at__isnull=True).update(
            status=Attempt.Status.LOST, finished_at=timezone.now(), logs=logs
        )
        add_event(
            execution, "stale_result_ignored", f"Résultat tardif de la tentative {attempt} ignoré.", attempt=attempt
        )
        return False
    now = timezone.now()
    Attempt.objects.filter(execution=execution, number=attempt).update(
        status=Attempt.Status.SUCCEEDED if ok else Attempt.Status.FAILED,
        finished_at=now,
        logs=logs,
        exit_code=exit_code,
    )
    if cancelled:
        _transition(execution, S.CANCELLED)
        execution.error = {
            "code": "CANCELLED",
            "message": "Annulée pendant l'exécution (effets déjà produits non annulés).",
        }
    else:
        _transition(execution, S.SUCCEEDED if ok else S.FAILED)
        execution.result, execution.error = (result, None) if ok else (None, error)
    execution.finished_at, execution.lease_expires_at = now, None
    execution.save()
    if cancelled:
        add_event(execution, "cancelled", "Exécution annulée.")
    elif ok:
        add_event(execution, "succeeded", "Exécution terminée avec succès.")
    else:
        add_event(execution, "failed", error["message"] if error else "Échec.", code=(error or {}).get("code"))
    return True


@transaction.atomic
def reconcile_expired() -> int:
    """Handle orphaned runs whose lease expired (worker crash). Returns number of runs handled."""
    now = timezone.now()
    expired = list(
        Execution.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("action_version")
        .filter(status=S.RUNNING)
        .filter(Q(lease_expires_at__lt=now))
    )
    for ex in expired:
        Attempt.objects.filter(execution=ex, number=ex.attempt_count, finished_at__isnull=True).update(
            status=Attempt.Status.LOST, finished_at=now
        )
        av = ex.action_version
        unsafe = av.side_effects != av.SideEffects.NONE and not av.idempotent
        if unsafe:
            _transition(ex, S.FAILED)
            ex.error = {
                "code": "OUTCOME_UNKNOWN",
                "message": "Le worker a disparu pendant une action à effet externe : l'issue est inconnue.",
                "next_step": "Vérifiez dans le système cible si l'effet a eu lieu avant de relancer.",
            }
            ex.finished_at, ex.lease_expires_at = now, None
            add_event(ex, "outcome_unknown", "Issue inconnue : relance automatique refusée.")
        elif ex.attempt_count >= settings.ORCH_MAX_ATTEMPTS:
            _transition(ex, S.FAILED)
            ex.error = {
                "code": "ATTEMPTS_EXHAUSTED",
                "message": "Le nombre maximal de tentatives est atteint.",
                "next_step": "Relancez l'exécution ou contactez le propriétaire de l'action.",
            }
            ex.finished_at, ex.lease_expires_at = now, None
            add_event(ex, "failed", "Tentatives épuisées après perte de worker.")
        elif ex.cancel_requested:
            _transition(ex, S.CANCELLED)
            ex.finished_at, ex.lease_expires_at = now, None
            add_event(ex, "cancelled", "Annulée (worker perdu).")
        else:
            _transition(ex, S.QUEUED)
            ex.lease_expires_at = None
            add_event(ex, "requeued", f"Worker perdu : tentative {ex.attempt_count} abandonnée, nouvelle tentative.")
        ex.save()
    return len(expired)
