"""Finding and comparing runs: filters shared by the list and the export, and a side-by-side comparison of two runs."""

import csv
import io
import json
from datetime import timedelta

from django.db.models import DurationField, ExpressionWrapper, F, Q
from django.utils.dateparse import parse_date

from .models import StepRun

MAX_EXPORT = 5000
MAX_VALUE = 2000


def filter_runs(qs, params):
    """Filters: status, workflow (slug), q (name or slug), environment, trigger, from / to (dates), min_duration /
    max_duration (seconds), step (node id of the failed step), code (error code), user (username)."""
    if v := params.get("status"):
        qs = qs.filter(status=v)
    if v := params.get("workflow"):
        qs = qs.filter(workflow_version__workflow__slug=v)
    if v := (params.get("q") or "").strip():
        qs = qs.filter(
            Q(workflow_version__workflow__name__icontains=v) | Q(workflow_version__workflow__slug__icontains=v)
        )
    if v := params.get("environment"):
        qs = qs.filter(environment__kind=v)
    if v := params.get("user"):
        qs = qs.filter(requested_by__username=v)
    if v := params.get("step"):
        qs = qs.filter(error__failed_step=v)
    if v := params.get("code"):
        qs = qs.filter(error__code=v)
    if d := parse_date(params.get("from") or ""):
        qs = qs.filter(created_at__date__gte=d)
    if d := parse_date(params.get("to") or ""):
        qs = qs.filter(created_at__date__lte=d)
    lo, hi = params.get("min_duration"), params.get("max_duration")
    if (lo or "").isdigit() or (hi or "").isdigit():
        qs = qs.filter(finished_at__isnull=False).annotate(
            _dur=ExpressionWrapper(F("finished_at") - F("created_at"), output_field=DurationField())
        )
        if (lo or "").isdigit():
            qs = qs.filter(_dur__gte=timedelta(seconds=int(lo)))
        if (hi or "").isdigit():
            qs = qs.filter(_dur__lte=timedelta(seconds=int(hi)))
    return qs


def _seconds(run):
    return round((run.finished_at - run.created_at).total_seconds(), 2) if run.finished_at else ""


def export_csv(qs) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "id",
            "workflow",
            "version",
            "environnement",
            "statut",
            "créée",
            "terminée",
            "durée_s",
            "lancée_par",
            "étape_en_échec",
            "code_erreur",
        ]
    )
    for r in qs.order_by("-created_at")[:MAX_EXPORT]:
        err = r.error or {}
        w.writerow(
            [
                r.id,
                r.workflow_version.workflow.slug,
                r.workflow_version.version,
                r.environment.kind,
                r.status,
                r.created_at.isoformat(),
                r.finished_at.isoformat() if r.finished_at else "",
                _seconds(r),
                r.requested_by.username if r.requested_by else "",
                err.get("failed_step", ""),
                err.get("code", ""),
            ]
        )
    return out.getvalue()


def _short(v):
    text = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return v if len(text) <= MAX_VALUE else {"tronqué": text[:MAX_VALUE]}


def _diff(a: dict, b: dict) -> list:
    keys = sorted(set(a or {}) | set(b or {}))
    return [
        {
            "key": k,
            "a": _short((a or {}).get(k)),
            "b": _short((b or {}).get(k)),
            "same": (a or {}).get(k) == (b or {}).get(k),
        }
        for k in keys
    ]


def compare(a, b) -> dict:
    """Two runs side by side: inputs, then every step (status, duration, output differences), then the run's own outputs."""
    sa = {s.node_id: s for s in StepRun.objects.filter(run=a)}
    sb = {s.node_id: s for s in StepRun.objects.filter(run=b)}
    names = {n["id"]: n.get("name", n["id"]) for r in (a, b) for n in r.definition["graph"]["nodes"]}
    order = [n["id"] for n in a.definition["graph"]["nodes"]]
    order += [n["id"] for n in b.definition["graph"]["nodes"] if n["id"] not in order]
    steps = []
    for nid in order:
        x, y = sa.get(nid), sb.get(nid)
        out_a, out_b = (x.output if x else None), (y.output if y else None)
        steps.append(
            {
                "node_id": nid,
                "name": names.get(nid, nid),
                "status_a": x.status if x else None,
                "status_b": y.status if y else None,
                "same_status": (x.status if x else None) == (y.status if y else None),
                "same_output": out_a == out_b,
                "output_diff": [
                    d
                    for d in _diff(out_a if isinstance(out_a, dict) else {}, out_b if isinstance(out_b, dict) else {})
                    if not d["same"]
                ],
                "error_a": (x.error or {}).get("message") if x and x.error else None,
                "error_b": (y.error or {}).get("message") if y and y.error else None,
            }
        )
    meta = lambda r: {  # noqa: E731
        "id": str(r.id),
        "workflow": r.workflow_version.workflow.slug,
        "version": r.workflow_version.version,
        "environment": r.environment.kind,
        "status": r.status,
        "created_at": r.created_at,
        "duration_s": _seconds(r) or None,
    }
    return {
        "a": meta(a),
        "b": meta(b),
        "same_version": a.workflow_version_id == b.workflow_version_id,
        "inputs": _diff(a.inputs, b.inputs),
        "steps": steps,
        "outputs": _diff(a.outputs or {}, b.outputs or {}),
        "different_steps": sum(1 for s in steps if not (s["same_status"] and s["same_output"])),
    }
