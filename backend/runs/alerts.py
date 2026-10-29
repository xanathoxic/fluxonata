"""Alerts and housekeeping, run by a thread of the WORKER process.

- `enqueue_run_failed` is called when a run ends in failure (same transaction: nothing is lost if the process dies);
- `evaluate_rates` looks at recent runs for « taux d'échec » rules;
- `deliver_due` sends the queue (SSRF-checked, signed by the platform key if present is NOT used: the destination is a chat
  or monitoring address, authenticated by its own secret URL), with retries;
- `apply_retention` deletes finished runs older than the workspace's retention."""

import json
import logging
import threading
import time
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

from core import netsec

from .models import AlertDelivery, AlertRule, WorkflowRun

log = logging.getLogger("fluxonata.alerts")
MAX_ATTEMPTS = 5


def _link(run) -> str:
    from django.conf import settings

    return f"{getattr(settings, 'ORCH_PUBLIC_URL', '').rstrip('/')}/runs/{run.id}"


def _payload(rule: AlertRule, text: str, facts: dict) -> dict:
    return {"text": text} if rule.format == "slack" else {"event": rule.event, "text": text, **facts}


def _enqueue(rule: AlertRule, text: str, facts: dict) -> None:
    AlertDelivery.objects.create(rule=rule, payload=_payload(rule, text, facts), next_attempt_at=timezone.now())


def enqueue_run_failed(run: WorkflowRun) -> None:
    """Queue an alert for every enabled « une exécution échoue » rule matching this top-level run."""
    if run.parent_step_id is not None:
        return
    wf = run.workflow_version.workflow
    rules = AlertRule.objects.filter(workspace=run.workspace, enabled=True, event=AlertRule.Event.RUN_FAILED)
    err = run.error or {}
    for rule in rules:
        if rule.environment and rule.environment != run.environment.kind:
            continue
        if rule.workflow_id and rule.workflow_id != wf.pk:
            continue
        text = f"❌ Exécution échouée : {wf.name} ({run.environment.kind}) — {str(err.get('message', ''))[:200]} {_link(run)}".strip()
        facts = {
            "workflow": wf.slug,
            "run_id": str(run.id),
            "environment": run.environment.kind,
            "code": err.get("code", ""),
            "step": err.get("failed_step", ""),
        }
        _enqueue(rule, text, facts)


def evaluate_rates(now=None) -> int:
    now, fired = now or timezone.now(), 0
    for rule in AlertRule.objects.filter(enabled=True, event=AlertRule.Event.FAILURE_RATE).select_related(
        "workspace", "workflow"
    ):
        if rule.last_fired_at and now - rule.last_fired_at < timedelta(minutes=rule.cooldown_minutes):
            continue
        runs = WorkflowRun.objects.filter(
            workspace=rule.workspace,
            parent_step__isnull=True,
            finished_at__gte=now - timedelta(minutes=rule.window_minutes),
            status__in=["succeeded", "failed"],
        )
        if rule.environment:
            runs = runs.filter(environment__kind=rule.environment)
        if rule.workflow_id:
            runs = runs.filter(workflow_version__workflow_id=rule.workflow_id)
        total = runs.count()
        if total < rule.min_runs:
            continue
        failed = runs.filter(status="failed").count()
        pct = round(100 * failed / total)
        if pct >= rule.threshold_percent:
            text = f"⚠️ Taux d'échec {pct} % ({failed}/{total}) sur {rule.window_minutes} min — {rule.name}"
            _enqueue(
                rule, text, {"failed": failed, "total": total, "percent": pct, "window_minutes": rule.window_minutes}
            )
            AlertRule.objects.filter(pk=rule.pk).update(last_fired_at=now)
            fired += 1
    return fired


def deliver_due(now=None) -> int:
    now, done = now or timezone.now(), 0
    for did in list(
        AlertDelivery.objects.filter(status="pending", next_attempt_at__lte=now).values_list("id", flat=True)[:50]
    ):
        with transaction.atomic():
            d = (
                AlertDelivery.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("rule")
                .filter(pk=did, status="pending")
                .first()
            )
            if d is None:
                continue
            d.attempts += 1
            try:
                status, _, _ = netsec.safe_request(
                    "POST",
                    d.rule.reveal_url(),
                    headers={"Content-Type": "application/json"},
                    body=json.dumps(d.payload).encode(),
                    timeout=5,
                )
                ok, err, permanent = 200 <= status < 300, f"HTTP {status}", False
            except netsec.UnsafeURL as exc:
                ok, err, permanent = False, f"Adresse refusée : {exc}", True
            except OSError as exc:
                ok, err, permanent = False, f"Réseau : {type(exc).__name__}", False
            if ok:
                d.status, d.last_error = "delivered", ""
                done += 1
            else:
                d.last_error = err[:300]
                if permanent or d.attempts >= MAX_ATTEMPTS:
                    d.status = "failed"
                else:
                    d.next_attempt_at = timezone.now() + timedelta(seconds=min(1800, 30 * 2 ** (d.attempts - 1)))
            d.save()
    return done


def apply_retention(now=None) -> int:
    """Delete finished top-level runs older than each workspace's retention (artifacts included)."""
    from tenancy.models import Workspace

    from .artifacts import get_store
    from .models import Artifact, Execution

    now, removed = now or timezone.now(), 0
    for ws in Workspace.objects.filter(retention_days__isnull=False, archived_at__isnull=True):
        cutoff = now - timedelta(days=ws.retention_days)
        runs = WorkflowRun.objects.filter(
            workspace=ws,
            status__in=["succeeded", "failed", "cancelled"],
            finished_at__lt=cutoff,
            parent_step__isnull=True,
        )
        ids = list(runs.values_list("pk", flat=True)[:500])
        if not ids:
            continue
        step_execs = Execution.objects.filter(step_run__run__in=ids)
        store = get_store()
        for a in Artifact.objects.filter(execution__in=step_execs):
            store.delete(a.storage_key)
        removed += WorkflowRun.objects.filter(pk__in=ids).delete()[1].get("runs.WorkflowRun", 0)
    return removed


def run_loop(worker_id: str, stop=None, poll_seconds: float = 5.0) -> None:
    stop = stop or threading.Event()
    last_rates = last_retention = last_forms = 0.0
    while not stop.is_set():
        close_old_connections()
        try:
            deliver_due()
            if time.monotonic() - last_rates > 60:
                evaluate_rates()
                last_rates = time.monotonic()
            if time.monotonic() - last_forms > 60:
                from triggers.forms import expire_forms

                expire_forms()
                last_forms = time.monotonic()
            if time.monotonic() - last_retention > 3600:
                apply_retention()
                last_retention = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("alerts loop iteration failed")
        stop.wait(poll_seconds)
