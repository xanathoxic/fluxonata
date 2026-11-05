"""Variable inspector of a run: what each step received and produced, for the run page. Read-only and recomputed from what is
already stored (frozen definition, run inputs, outputs of finished steps): nothing is persisted and nothing is executed.
Secrets are masked with the same rule as the catalog view (`format: password`, `writeOnly` or a secret-looking name)."""

from catalog.models import ActionVersion
from workflows.expressions import coerce_inputs, resolve

from .catalog_views import mask_inputs
from .engine import _ctx, _variables


def _step_inputs(run, node, ctx):
    """The resolved inputs of a step (None when the step type takes none or they cannot be resolved)."""
    schema = None
    t = node["type"]
    if t == "action":
        pin = (run.definition.get("actions") or {}).get(node["id"])
        av = ActionVersion.objects.filter(pk=pin["action_version_id"]).first() if pin else None
        schema = av.input_schema if av else None
        raw = coerce_inputs(node.get("inputs", {}), schema) if schema else node.get("inputs") or {}
    elif t == "script":
        raw = node.get("bindings") or {}
    elif t == "ai":
        raw = dict(node.get("inputs") or {})
        if node.get("prompt"):
            raw["prompt"] = node["prompt"]
    else:
        raw = node.get("inputs") or {}
    if not raw:
        return None
    try:
        values = resolve(raw, ctx)
    except Exception:  # an expression that cannot be evaluated (step not reached): nothing to show
        return None
    return mask_inputs(schema, values)[0]


def build(run, steps: dict) -> dict:
    """steps: {node_id: StepRun}. Returns the start values, the end values and the inputs of every started step."""
    ctx = _ctx(run, steps)
    schema = getattr(run.workflow_version, "input_schema", None)
    graph = run.definition["graph"]
    nodes = {}
    for node in graph["nodes"]:
        if node["id"] in steps:
            nodes[node["id"]] = {"inputs": _step_inputs(run, node, ctx)}
    return {
        "start": {
            "inputs": mask_inputs(schema, run.inputs or {})[0],
            "constants": mask_inputs(None, _variables(run))[0],
        },
        "nodes": nodes,
        "end": {"outputs": mask_inputs(None, run.outputs)[0] if run.outputs is not None else None},
    }
