"""Operations figures for the dashboard (computed on demand from runs; bounded)."""

from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from workflows import access

from .models import Execution, StepRun, WorkflowRun

MAX_ROWS = 20000


def _scoped(qs, workspace, user):
    return qs if user is None else access.visible_runs(qs, user, workspace)


def _pct(values: list[float], p: float):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(p * len(values)))], 2)


def dashboard(workspace, days: int = 7, environment: str = "", user=None) -> dict:
    since = timezone.now() - timedelta(days=days)
    qs = WorkflowRun.objects.filter(workspace=workspace, parent_step__isnull=True, created_at__gte=since)
    if user is not None:  # figures never include what lives in a private folder the person has no access to
        qs = access.visible_runs(qs, user, workspace)
    if environment:
        qs = qs.filter(environment__kind=environment)
    counts = Counter(qs.values_list("status", flat=True))
    finished = counts["succeeded"] + counts["failed"]
    per_day: dict = defaultdict(lambda: {"succeeded": 0, "failed": 0})
    for day, status in (
        qs.filter(status__in=["succeeded", "failed"]).annotate(d=TruncDate("created_at")).values_list("d", "status")
    ):
        per_day[day.isoformat()][status] += 1
    rows = list(
        qs.filter(finished_at__isnull=False).values_list(
            "workflow_version__workflow__slug",
            "workflow_version__workflow__name",
            "status",
            "created_at",
            "finished_at",
            "error",
        )[:MAX_ROWS]
    )
    durations, by_wf, failing_steps = [], defaultdict(lambda: {"name": "", "runs": 0, "failed": 0, "d": []}), Counter()
    for slug, name, status, a, b, error in rows:
        sec = (b - a).total_seconds()
        durations.append(sec)
        w = by_wf[slug]
        w["name"], w["runs"] = name, w["runs"] + 1
        w["d"].append(sec)
        if status == "failed":
            w["failed"] += 1
            if isinstance(error, dict) and error.get("failed_step"):
                failing_steps[(slug, error["failed_step"])] += 1
    top_failing = sorted(
        (
            {"workflow": s, "name": w["name"], "runs": w["runs"], "failed": w["failed"]}
            for s, w in by_wf.items()
            if w["failed"]
        ),
        key=lambda r: (-r["failed"], r["workflow"]),
    )[:8]
    slowest = sorted(
        ({"workflow": s, "name": w["name"], "runs": w["runs"], "p95_s": _pct(w["d"], 0.95)} for s, w in by_wf.items()),
        key=lambda r: (-(r["p95_s"] or 0), r["workflow"]),
    )[:8]
    ai = {"tokens": 0, "calls": 0}
    try:
        from ai.models import AiCall

        agg = AiCall.objects.filter(workspace=workspace, created_at__gte=since).aggregate(
            i=Sum("input_tokens"), o=Sum("output_tokens")
        )
        ai = {
            "tokens": (agg["i"] or 0) + (agg["o"] or 0),
            "calls": AiCall.objects.filter(workspace=workspace, created_at__gte=since).count(),
        }
    except ImportError:  # the assistant is optional
        ai = {"tokens": 0, "calls": 0}
    return {
        "period_days": days,
        "totals": {
            "runs": sum(counts.values()),
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "cancelled": counts["cancelled"],
            "running": counts["running"] + counts["compensating"],
            "failure_rate": round(100 * counts["failed"] / finished, 1) if finished else None,
        },
        "per_day": [{"day": d, **v} for d, v in sorted(per_day.items())],
        "durations": {"p50_s": _pct(durations, 0.5), "p95_s": _pct(durations, 0.95)},
        "top_failing": top_failing,
        "slowest": slowest,
        "failing_steps": [{"workflow": w, "step": s, "count": c} for (w, s), c in failing_steps.most_common(8)],
        "queue": {
            "running_runs": _scoped(
                WorkflowRun.objects.filter(workspace=workspace, status__in=["running", "compensating"]), workspace, user
            ).count(),
            "queued_executions": Execution.objects.filter(workspace=workspace, status="queued").count(),
            "waiting_steps": StepRun.objects.filter(run__workspace=workspace, status="waiting").count(),
        },
        "ai": ai,
    }
