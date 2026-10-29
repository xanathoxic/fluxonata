"""Runs evaluations queued from the interface, in the WORKER process (they call the model: never in the web process).
The harness is the `ai_eval` command; its output is kept as the log and its totals are read from the last lines."""

import io
import logging
import re
import threading

from django.core.management import call_command
from django.db import close_old_connections, transaction
from django.utils import timezone

from ai import service
from ai.models import AiCall, AiEvalRun

log = logging.getLogger("fluxonata.ai.evals")
FLAGS = {"search": [], "workflows": ["--workflows"], "edits": ["--edits"], "vague": ["--vague"]}
TOTAL_RE = re.compile(r"Total : (\d+)/(\d+) crit")
CASES_RE = re.compile(r"(\d+)/(\d+) cas réussis")
MODE_RE = re.compile(r"^\s+(direct|plan) : (\d+)/(\d+) crit", re.M)


def claim_next(worker_id: str):
    with transaction.atomic():
        run = (
            AiEvalRun.objects.select_for_update(skip_locked=True)
            .filter(status=AiEvalRun.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if run is None:
            return None
        run.status, run.claimed_by = AiEvalRun.Status.RUNNING, worker_id
        run.save(update_fields=["status", "claimed_by"])
        return run


def parse_totals(log_text: str) -> tuple[int, int]:
    m = TOTAL_RE.search(log_text) or CASES_RE.search(log_text)
    if m:
        return int(m.group(1)), int(m.group(2))
    modes = MODE_RE.findall(log_text)  # « vague » suite: direct and plan are both counted
    return (sum(int(a) for _, a, _ in modes), sum(int(b) for _, _, b in modes)) if modes else (0, 0)


def process(run: AiEvalRun) -> None:
    before = AiCall.objects.filter(workspace=run.workspace).order_by("-pk").values_list("pk", flat=True).first() or 0
    out = io.StringIO()
    try:
        args = [run.workspace.slug, "--provider", run.provider, *FLAGS[run.suite]]
        if run.options.get("set") in ("tuned", "heldout") and run.suite == "workflows":
            args += ["--set", run.options["set"]]
        call_command("ai_eval", *args, "--repeat", str(run.options.get("repeat", 1)), stdout=out)
        passed, total = parse_totals(out.getvalue())
        from django.db.models import Sum

        agg = AiCall.objects.filter(workspace=run.workspace, pk__gt=before).aggregate(
            i=Sum("input_tokens"), o=Sum("output_tokens")
        )
        AiEvalRun.objects.filter(pk=run.pk).update(
            status=AiEvalRun.Status.DONE,
            passed=passed,
            total=total,
            tokens=(agg["i"] or 0) + (agg["o"] or 0),
            log=out.getvalue()[-20000:],
            finished_at=timezone.now(),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("evaluation %s failed", run.pk)
        AiEvalRun.objects.filter(pk=run.pk).update(
            status=AiEvalRun.Status.FAILED,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            log=out.getvalue()[-20000:],
            finished_at=timezone.now(),
        )


def prompt_versions() -> dict:
    return {k: v.get("version", "") for k, v in service.PROMPTS.items()}


def run_loop(worker_id: str, stop=None, poll_seconds: float = 3.0) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        close_old_connections()
        try:
            run = claim_next(worker_id)
            if run is not None:
                process(run)
                continue
        except Exception:  # noqa: BLE001
            log.exception("eval loop iteration failed")
        stop.wait(poll_seconds)
