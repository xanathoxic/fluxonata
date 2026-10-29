"""Long assistant tasks (draft generation), run by the WORKER process, never by the web process.

The editor creates a job (`enqueue_draft`), then polls it: a progress percentage and a live log of the phases the
generation goes through (plan, schema, checks, dry run, review, save). A thread of `run_worker` claims queued jobs one at
a time (`claim_next`, safe with several workers) and runs the very same `service.draft_workflow` as the synchronous path,
with a progress sink installed. The request text is dropped once the job is over (the assistant keeps no content by
default); a job whose worker disappeared is marked failed instead of spinning forever."""

import logging
import threading
import time
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

from . import progress, service
from .models import AiJob

log = logging.getLogger("fluxonata.ai.jobs")
STALE_AFTER = timedelta(minutes=10)
KEEP_FINISHED = timedelta(hours=24)
MAX_EVENTS = 80


class JobCancelled(Exception):
    """The person cancelled the job: raised from the progress sink, so the pipeline stops at its next phase."""


class _Finished(Exception):
    """Ends `process` early with a ready outcome (the plan job has no draft to save)."""

    def __init__(self, outcome):
        super().__init__("finished")
        self.outcome = outcome


def enqueue_draft(workspace, user, request_text: str, slug: str, name: str, provider_name: str | None) -> AiJob:
    return AiJob.objects.create(
        workspace=workspace,
        user=user,
        kind="draft",
        payload={"request": request_text, "slug": slug, "name": name, "provider": provider_name},
        events=[
            {
                "t": round(time.time(), 1),
                "phase": "queued",
                "label": "En attente d'un travailleur",
                "state": "running",
                "detail": "",
                "pct": 0,
            }
        ],
    )


def enqueue_outline(workspace, user, request_text: str, previous, feedback, provider_name: str | None) -> AiJob:
    return AiJob.objects.create(
        workspace=workspace,
        user=user,
        kind="outline",
        payload={"request": request_text, "previous": previous, "feedback": feedback, "provider": provider_name},
        events=[
            {
                "t": round(time.time(), 1),
                "phase": "queued",
                "label": "En attente d'un travailleur",
                "state": "running",
                "detail": "",
                "pct": 0,
            }
        ],
    )


def claim_next(worker_id: str) -> AiJob | None:
    with transaction.atomic():
        job = (
            AiJob.objects.select_for_update(skip_locked=True)
            .filter(status=AiJob.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        job.status, job.claimed_by, job.heartbeat_at = AiJob.Status.RUNNING, worker_id, timezone.now()
        job.save(update_fields=["status", "claimed_by", "heartbeat_at"])
        return job


def _sink(job: AiJob):
    """Appends each phase event to the job row (the editor reads it): a phase that goes on is updated, not repeated."""
    events: list = list(job.events)

    def sink(event: dict):
        if AiJob.objects.filter(pk=job.pk, status=AiJob.Status.CANCELLED).exists():
            raise JobCancelled()  # nothing more is done, and in particular nothing is saved
        if (
            events
            and events[-1]["phase"] == event["phase"]
            and events[-1]["state"] == "running"
            and event["state"] == "running"
        ):
            events[-1] = event  # same phase, new detail
        else:
            if events and events[-1]["state"] == "running" and events[-1]["phase"] != event["phase"]:
                events[-1] = {**events[-1], "state": "done"}  # the previous phase is over
            events.append(event)
        del events[:-MAX_EVENTS]
        AiJob.objects.filter(pk=job.pk).update(
            events=events, progress=min(99, event["pct"]), heartbeat_at=timezone.now()
        )

    return sink


def process(job: AiJob) -> None:
    token = progress.install(_sink(job))
    try:
        payload = job.payload
        progress.report("context")
        if job.kind == "outline":
            before = service._mark(job.workspace)
            outline = service.make_outline(
                job.workspace,
                job.user,
                payload["request"],
                previous=payload.get("previous"),
                feedback=payload.get("feedback"),
                provider_name=payload.get("provider"),
            )
            result = {"outline": outline, "usage": service.usage_since(job.workspace, before)}
            outcome = {"status": AiJob.Status.SUCCEEDED, "result": result, "error": None}
            progress.report("done", "Le plan est prêt : relisez-le.", state="done", pct=100)
            raise _Finished(outcome)
        result = service.draft_workflow(
            job.workspace,
            job.user,
            payload["request"],
            payload["slug"],
            payload["name"],
            provider_name=payload.get("provider"),
        )
        outcome = {"status": AiJob.Status.SUCCEEDED, "result": result, "error": None}
        progress.report("done", "Le brouillon est prêt : relisez-le avant de le publier.", state="done", pct=100)
    except JobCancelled:
        last = AiJob.objects.get(pk=job.pk).events
        stop = {
            "t": round(time.time(), 1),
            "phase": "cancelled",
            "label": "Génération annulée",
            "state": "warning",
            "detail": "Rien n'a été enregistré.",
            "pct": job.progress,
        }
        AiJob.objects.filter(pk=job.pk).update(
            events=[*last, stop][-MAX_EVENTS:],
            error={"code": "CANCELLED", "message": "Génération annulée : aucun brouillon n'a été enregistré."},
            payload={"slug": job.payload.get("slug"), "name": job.payload.get("name")},
            finished_at=timezone.now(),
        )
        return
    except _Finished as done:
        outcome = done.outcome
    except service.AiRefused as exc:
        outcome = {"status": AiJob.Status.FAILED, "result": None, "error": {"code": exc.code, "message": exc.message}}
        progress.report("error", exc.message, state="error", label="La génération a échoué", pct=job.progress)
    except Exception:  # noqa: BLE001  (an unexpected bug must end the job, not leave it spinning)
        log.exception("ai job %s crashed", job.pk)
        outcome = {
            "status": AiJob.Status.FAILED,
            "result": None,
            "error": {"code": "INTERNAL", "message": "Erreur interne pendant la génération."},
        }
        progress.report("error", "Erreur interne pendant la génération.", state="error", label="La génération a échoué")
    finally:
        progress.uninstall(token)
    job.refresh_from_db(fields=["events"])
    final = 100 if outcome["status"] == AiJob.Status.SUCCEEDED else job.progress
    AiJob.objects.filter(pk=job.pk).exclude(status=AiJob.Status.CANCELLED).update(
        status=outcome["status"],
        result=outcome["result"],
        error=outcome["error"],
        progress=final,
        payload={"slug": job.payload.get("slug"), "name": job.payload.get("name")},  # the request text is not kept
        finished_at=timezone.now(),
    )


def cancel(job: AiJob) -> AiJob:
    """Cancel a queued or running job (a running one stops at its next phase; nothing is saved). No effect once it is over."""
    if job.status in (AiJob.Status.QUEUED, AiJob.Status.RUNNING):
        extra = {}
        if job.status == AiJob.Status.QUEUED:  # nobody runs it: it is over now
            extra = {
                "finished_at": timezone.now(),
                "payload": {"slug": job.payload.get("slug"), "name": job.payload.get("name")},
            }
        AiJob.objects.filter(pk=job.pk, status__in=[AiJob.Status.QUEUED, AiJob.Status.RUNNING]).update(
            status=AiJob.Status.CANCELLED,
            error={"code": "CANCELLED", "message": "Génération annulée : aucun brouillon n'a été enregistré."},
            **extra,
        )
        job.refresh_from_db()
    return job


def recent_for(user, workspace, hours: int = 6) -> list[dict]:
    """What the tray shows: the person's recent generations, with where each one is."""
    since = timezone.now() - timedelta(hours=hours)
    rows = AiJob.objects.filter(workspace=workspace, user=user, kind="draft", created_at__gte=since)
    out = []
    recent_end = timezone.now() - timedelta(
        minutes=30
    )  # a finished one is shown for a while, then it is only in the list
    for j in rows.order_by("-created_at")[:8]:
        if j.finished_at and j.finished_at < recent_end:
            continue
        last = j.events[-1] if j.events else {}
        out.append(
            {
                "id": str(j.id),
                "status": j.status,
                "progress": j.progress,
                "name": (j.payload or {}).get("name") or "Brouillon",
                "slug": (j.payload or {}).get("slug"),
                "phase": last.get("label", ""),
                "detail": last.get("detail", ""),
                "error": (j.error or {}).get("message"),
                "workflow": (j.result or {}).get("workflow"),
                "created_at": j.created_at,
            }
        )
    return out


def housekeeping() -> None:
    """Jobs whose worker vanished fail visibly; old finished jobs are removed."""
    now = timezone.now()
    for job in AiJob.objects.filter(status=AiJob.Status.RUNNING, heartbeat_at__lt=now - STALE_AFTER):
        AiJob.objects.filter(pk=job.pk).update(
            status=AiJob.Status.FAILED,
            error={
                "code": "INTERRUPTED",
                "message": "La génération a été interrompue (redémarrage du travailleur). Relancez-la.",
            },
            payload={"slug": job.payload.get("slug"), "name": job.payload.get("name")},
            finished_at=now,
        )
    AiJob.objects.filter(finished_at__lt=now - KEEP_FINISHED).delete()


def run_loop(worker_id: str, stop: threading.Event | None = None, poll_seconds: float = 1.0) -> None:
    """Thread body inside `run_worker`: one generation at a time per worker."""
    stop = stop or threading.Event()
    last_housekeeping = 0.0
    while not stop.is_set():
        close_old_connections()
        try:
            if time.monotonic() - last_housekeeping > 60:
                housekeeping()
                last_housekeeping = time.monotonic()
            job = claim_next(worker_id)
            if job is not None:
                process(job)
                continue
        except Exception:  # noqa: BLE001
            log.exception("ai job loop iteration failed")
        stop.wait(poll_seconds)
