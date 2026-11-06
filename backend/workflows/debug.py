"""Editor-side debugging helpers: the data a step would see, built from the LAST run of the workflow and from pinned outputs.

Nothing here runs a step that has an effect: expressions and pure steps (decision, choice, transformation) are evaluated
with the real evaluator on real data; other kinds answer with their pin, their last output, or nothing."""

import json
import re

from . import transforms
from .expressions import ExpressionError, evaluate, resolve

MAX_PINS = 20
MAX_PIN_BYTES = 64 * 1024
MAX_PREVIEW_BYTES = 20 * 1024
PURE = ("condition", "switch", "transform")

# Display only: the editor never shows values stored under such names (the evaluation itself still uses the real data).
SENSITIVE = re.compile(
    r"pass(word|wd|phrase)?|pwd|mot[_-]?de[_-]?passe|secret|token|api[_-]?key|authorization|credential|private[_-]?key|bearer|cookie",
    re.IGNORECASE,
)
MASK = "••••••"


def redact(value):
    """Copy of `value` where whatever sits under a sensitive key name is replaced by a mask (preview display only)."""
    if isinstance(value, dict):
        return {k: MASK if SENSITIVE.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def redact_for_expr(expr, value):
    """Display value of an expression result: masked when the expression reads a sensitive field (booleans and numbers,
    e.g. the result of a comparison, are kept)."""
    if SENSITIVE.search(str(expr)) and not isinstance(value, (bool, int, float, type(None))):
        return MASK
    return redact(value)


def validate_pins(pins, node_ids: set) -> list[str]:
    """Problems (French) in a graph's `pins` (node id -> output object)."""
    if pins in (None, {}):
        return []
    if not isinstance(pins, dict):
        return ["Les données épinglées doivent former un objet (étape → sortie)."]
    out = []
    if len(pins) > MAX_PINS:
        out.append(f"{MAX_PINS} données épinglées au plus.")
    for nid, value in pins.items():
        if nid not in node_ids:
            out.append(f"Donnée épinglée sur une étape absente : {nid}.")
        elif not isinstance(value, dict):
            out.append(f"La donnée épinglée de « {nid} » doit être un objet (sa sortie).")
        elif len(json.dumps(value)) > MAX_PIN_BYTES:
            out.append(f"La donnée épinglée de « {nid} » dépasse {MAX_PIN_BYTES // 1024} Ko.")
    return out


def last_run(workspace, workflow, run_id=None):
    from runs.models import WorkflowRun

    qs = WorkflowRun.objects.filter(workspace=workspace, workflow_version__workflow=workflow, parent_step__isnull=True)
    if run_id:
        return qs.filter(pk=run_id).first()
    return qs.order_by("-created_at").first()


def build_context(graph: dict, run=None) -> tuple[dict, dict]:
    """(ctx, where): the data an expression sees. Outputs come from `run` (default: none), pins win over them.
    `where` tells the editor which steps have data and from where: {"step": "pin" | "run"}."""
    steps, where = {}, {}
    if run is not None:
        for sr in run.steps.all():
            if sr.status == "succeeded" and isinstance(sr.output, dict):
                steps[sr.node_id], where[sr.node_id] = {"output": sr.output}, "run"
    for nid, value in (graph.get("pins") or {}).items():
        if isinstance(value, dict):
            steps[nid], where[nid] = {"output": value}, "pin"
    ctx = {
        "inputs": run.inputs if run is not None else {},
        "vars": {
            v["name"]: (v.get("env_values") or {}).get(run.environment.kind if run is not None else "", v.get("value"))
            for v in graph.get("variables") or []
            if isinstance(v, dict) and "name" in v
        },
        "run": {
            "id": str(run.id) if run is not None else "essai",
            "started_at": run.created_at.isoformat() if run is not None else "2026-01-01T00:00:00+00:00",
            "environment": run.environment.kind if run is not None else "dev",
        },
        "steps": steps,
    }
    return ctx, where


def _bounded(value):
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_PREVIEW_BYTES:
        return {"truncated": True, "preview": text[:MAX_PREVIEW_BYTES]}
    return value


def evaluate_preview(expr: str, ctx: dict, item=None) -> dict:
    """Value of an expression or template (`{{ … }}` inside text) on the given data: {"ok": True, "value": …} or
    {"ok": False, "error": "…"}. `item` is bound as `item` (per-item expressions of a transformation)."""
    scope = {**ctx, "item": item} if item is not None else ctx
    try:
        src = str(expr)
        value = resolve(src, scope) if "{{" in src else evaluate(src, scope)
    except ExpressionError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "value": _bounded(value)}


def test_step(node: dict, ctx: dict, where: dict) -> dict:
    """Runs one PURE step on the given data. Others answer from their pin / last output."""
    nid, ntype = node["id"], node.get("type")
    if ntype == "condition":
        r = evaluate_preview(node.get("expr", ""), ctx)
        return {
            "ok": r["ok"],
            "output": {"result": bool(r["value"])} if r["ok"] else None,
            "error": r.get("error"),
            "source": "calculé",
        }
    if ntype == "switch":
        for case in node.get("cases") or []:
            r = evaluate_preview(case.get("expr", ""), ctx)
            if not r["ok"]:
                return {
                    "ok": False,
                    "output": None,
                    "error": f"Cas « {case.get('id')} » : {r['error']}",
                    "source": "calculé",
                }
            if r["value"]:
                return {"ok": True, "output": {"result": case["id"]}, "error": None, "source": "calculé"}
        return {"ok": True, "output": {"result": "default"}, "error": None, "source": "calculé"}
    if ntype == "transform":
        try:
            return {"ok": True, "output": _bounded(transforms.apply(node, ctx)), "error": None, "source": "calculé"}
        except (transforms.TransformError, ExpressionError) as exc:
            return {"ok": False, "output": None, "error": str(exc), "source": "calculé"}
    if nid in ctx["steps"]:
        src = "épinglée" if where.get(nid) == "pin" else "dernière exécution"
        return {"ok": True, "output": _bounded(ctx["steps"][nid]["output"]), "error": None, "source": src}
    return {
        "ok": False,
        "output": None,
        "error": "Cette étape a un effet : elle n'est pas rejouée ici. Lancez le workflow en recette ou épinglez une sortie.",
        "source": "aucune",
    }
