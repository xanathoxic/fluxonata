"""Durable workflow orchestration. PostgreSQL rows are the only source of truth.

`advance(run_id)` is idempotent and transactional: it recomputes what to do from the persisted StepRuns, so the
orchestrator can crash/restart at any point without repeating confirmed steps.

Branch activation:
- an edge is ACTIVE when its source succeeded and the label matches (next / condition true|false), or when the
  source failed and the label is "error"; DEAD when the source was skipped/cancelled or the label doesn't match.
- a non-join node starts when its single incoming edge is active, is skipped when dead.
- a join waits until every incoming branch is resolved, runs if >= 1 branch is active, is skipped otherwise.
Failure policy (fail-fast): a failed step without an "error" edge stops new work, cancels running work, runs the
declared compensations in reverse order (each may itself fail) and ends the run as failed.
"""

import random
from collections import defaultdict
from datetime import timedelta

import jsonschema
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import record
from catalog.models import ActionVersion
from tenancy import governance
from workflows.expressions import ExpressionError, evaluate, resolve
from workflows.validation import explain_validation_error, topo_order

from . import services
from .models import Approval, CallbackDelivery, Execution, RunEvent, StepRun, WorkflowRun

R, S = WorkflowRun.Status, StepRun.Status
TERMINAL = set(StepRun.TERMINAL)
MAX_DEPTH = 5
RETRYABLE = {"TIMEOUT", "ACTION_ERROR", "RUNNER_ERROR", "ATTEMPTS_EXHAUSTED", "OOM_OR_KILLED", "PROTOCOL_ERROR"}


# ----------------------------------------------------------------------------- helpers
def add_event(run, type_, message, node_id=""):
    last = run.events.order_by("-seq").values_list("seq", flat=True).first() or 0
    return RunEvent.objects.create(run=run, seq=last + 1, type=type_, node_id=node_id, message=message[:300])


def _graph_views(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for e in graph["edges"]:
        incoming[e["to"]].append(e)
        outgoing[e["from"]].append(e)
    return nodes, incoming, outgoing, topo_order(graph["nodes"], graph["edges"])


def _variables(run) -> dict:
    return {v["name"]: v.get("value") for v in (run.definition["graph"].get("variables") or [])}


def _ctx(run, steps):
    return {
        "inputs": run.inputs,
        "vars": _variables(run),
        "steps": {
            nid: {"output": sr.output if sr.output is not None else {}, "status": sr.status}
            for nid, sr in steps.items()
            if sr.status == S.SUCCEEDED
        },
    }


def _finish(run, sr, status, *, output=None, error=None):
    sr.status = status
    sr.output = output if output is not None else sr.output
    sr.error = error
    sr.finished_at = timezone.now()
    sr.seq = (max([s.seq for s in run.steps.all()] + [0])) + 1
    sr.save()


def _runtime_info():
    return {"image": settings.ORCH_PYTHON_IMAGE, "digest": _image_digest(settings.ORCH_PYTHON_IMAGE)}


_digest_cache: dict = {}


def _image_digest(image):
    if image not in _digest_cache:
        import subprocess

        try:
            out = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True, timeout=10
            )
            _digest_cache[image] = out.stdout.strip() or None
        except Exception:  # noqa: BLE001
            _digest_cache[image] = None
    return _digest_cache[image]


# ----------------------------------------------------------------------------- start
@transaction.atomic
def start_run(
    *,
    workspace,
    environment,
    workflow_version,
    user,
    inputs,
    idempotency_key=None,
    parent_step=None,
    child_index=None,
    priority=0,
    depth=0,
    trigger="manual",
    callback_url="",
):
    """Create a run with a frozen definition. Returns (run, created)."""
    from .serializers import with_defaults

    inputs = with_defaults(workflow_version.input_schema, inputs)  # omitted inputs take their declared default
    if idempotency_key:
        existing = WorkflowRun.objects.filter(workspace=workspace, idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    governance_decision = None
    if parent_step is None:  # sub-runs inherit their parent's authorization
        governance_decision = governance.authorize_run(
            workspace=workspace, environment=environment, workflow_version=workflow_version, user=user, trigger=trigger
        )
    graph = workflow_version.graph
    pins = {}
    for n in graph["nodes"]:
        if n["type"] == "action":
            av = ActionVersion.objects.get(action__workspace=workspace, action__slug=n["action"], version=n["version"])
            pins[n["id"]] = {"action_version_id": av.id, "source_digest": av.source_digest}
            if n.get("compensation"):
                cv = ActionVersion.objects.get(
                    action__workspace=workspace,
                    action__slug=n["compensation"]["action"],
                    version=n["compensation"]["version"],
                )
                pins[f"comp_{n['id']}"] = {"action_version_id": cv.id, "source_digest": cv.source_digest}
    definition = {
        "graph": graph,
        "workflow": {
            "slug": workflow_version.workflow.slug,
            "version": workflow_version.version,
            "digest": workflow_version.digest,
        },
        "actions": pins,
        "runtime": _runtime_info(),
        "governance": governance_decision,
        "depth": depth,
    }
    run = WorkflowRun.objects.create(
        workspace=workspace,
        environment=environment,
        workflow_version=workflow_version,
        requested_by=user,
        inputs=inputs,
        definition=definition,
        parent_step=parent_step,
        child_index=child_index,
        priority=priority,
        idempotency_key=idempotency_key or None,
        callback_url=callback_url,
    )
    add_event(run, "started", f"Workflow {workflow_version.workflow.name} v{workflow_version.version} démarré.")
    if parent_step is None:
        record(
            "workflow_run.create",
            actor=user,
            workspace=workspace,
            target=run,
            workflow=workflow_version.workflow.slug,
            version=workflow_version.version,
            environment=environment.kind,
        )
    return run, True


# ----------------------------------------------------------------------------- advance
def advance(run_id) -> bool:
    """One orchestration step for a run. Returns True when the run was processed."""
    with transaction.atomic():
        run = (
            WorkflowRun.objects.select_for_update(skip_locked=True)
            .filter(pk=run_id, status__in=[R.RUNNING, R.COMPENSATING])
            .first()
        )
        if run is None:
            return False
        graph = run.definition["graph"]
        nodes, incoming, outgoing, order = _graph_views(graph)
        steps = {s.node_id: s for s in run.steps.all()}
        if run.cancel_requested and run.status == R.RUNNING:
            return _cancel_run(run, steps)
        if run.status == R.COMPENSATING:
            _advance_compensation(run, nodes, steps)
            return True
        for _ in range(len(nodes) + 3):  # fixpoint over instantly-resolvable nodes
            changed = False
            for nid in order:
                sr = steps.get(nid)
                if sr is not None and sr.status not in TERMINAL:
                    changed |= _poll(run, nodes[nid], sr, steps)
            fatal = _fatal_failure(nodes, outgoing, steps)
            if fatal:
                _begin_failure(run, nodes, steps, fatal)
                return True
            for nid in order:
                if nid in steps:
                    continue
                state = _activation(nodes[nid], incoming[nid], steps, nodes)
                if state == "wait":
                    continue
                changed = True
                if state == "skipped":
                    steps[nid] = StepRun.objects.create(
                        run=run, node_id=nid, node_type=nodes[nid]["type"], status=S.SKIPPED, finished_at=timezone.now()
                    )
                    add_event(
                        run,
                        "step_skipped",
                        f"Étape « {nodes[nid].get('name', nid)} » ignorée (branche non prise).",
                        nid,
                    )
                else:
                    steps[nid] = _start(run, nodes[nid], steps)
            if not changed:
                break
        if len(steps) == len(nodes) and all(s.status in TERMINAL for s in steps.values()):
            _complete(run, steps, graph)
        return True


def _activation(node, inc, steps, nodes):
    if not inc:
        return "ready"
    states = []
    for e in inc:
        src = steps.get(e["from"])
        if src is None or src.status not in TERMINAL:
            states.append("pending")
            continue
        label = e.get("label") or "next"
        if src.status == S.SUCCEEDED:
            if nodes[e["from"]]["type"] == "condition":
                active = label == str(bool((src.output or {}).get("result"))).lower()
            else:
                active = label == "next"
        elif src.status == S.FAILED:
            active = label == "error"
        else:
            active = False
        states.append("active" if active else "dead")
    if "pending" in states:
        return "wait"
    if node["type"] == "join":
        return "ready" if "active" in states else "skipped"
    return "ready" if states[0] == "active" else "skipped"


def _fatal_failure(nodes, outgoing, steps):
    for nid, sr in steps.items():
        if sr.status == S.FAILED and not any((e.get("label") == "error") for e in outgoing[nid]):
            return sr
    return None


def _queue_callback(run):
    """Transactional outbox: the delivery row is written in the same transaction as the final state."""
    if run.callback_url and run.parent_step_id is None:
        CallbackDelivery.objects.create(
            run=run,
            url=run.callback_url,
            next_attempt_at=timezone.now(),
            payload={
                "event": "workflow_run.finished",
                "run_id": str(run.id),
                "status": run.status,
                "workflow": run.definition["workflow"]["slug"],
                "version": run.definition["workflow"]["version"],
                "workspace": run.workspace.slug,
                "url": f"/api/v1/workspaces/{run.workspace.slug}/workflow-runs/{run.id}/",
            },
        )


def _complete(run, steps, graph):
    ctx = _ctx(run, steps)
    try:
        outputs = resolve(graph.get("outputs") or {}, ctx)
    except ExpressionError as exc:
        run.status, run.error = R.FAILED, {"code": "OUTPUT_ERROR", "message": f"Sorties du workflow : {exc}"}
        run.finished_at = timezone.now()
        run.save()
        add_event(run, "failed", run.error["message"])
        _queue_callback(run)
        return
    run.status, run.outputs, run.finished_at = R.SUCCEEDED, outputs, timezone.now()
    run.save()
    add_event(run, "succeeded", "Workflow terminé avec succès.")
    _queue_callback(run)


# ----------------------------------------------------------------------------- start a step
def _start(run, node, steps) -> StepRun:
    ntype = node["type"]
    sr = StepRun.objects.create(run=run, node_id=node["id"], node_type=ntype, status=S.RUNNING)
    label = node.get("name", node["id"])
    add_event(run, "step_started", f"Étape « {label} » démarrée.", node["id"])
    ctx = _ctx(run, steps)
    try:
        if ntype == "action":
            _dispatch_action(run, node, sr, ctx)
        elif ntype == "condition":
            result = bool(evaluate(node["expr"], ctx))
            _finish(run, sr, S.SUCCEEDED, output={"result": result})
            add_event(run, "step_succeeded", f"Condition « {label} » : {'oui' if result else 'non'}.", node["id"])
        elif ntype == "ai":
            _dispatch_ai(run, node, sr, ctx)
        elif ntype == "script":
            _dispatch_script(run, node, sr, ctx)
        elif ntype == "throw":
            raise _StepError(
                str(node.get("code") or "THROWN")[:40],
                str(resolve(node.get("message", "Exception levée par le workflow."), ctx))[:300],
                "Corrigez la cause indiquée ou relancez avec d'autres entrées.",
            )
        elif ntype == "join":
            _finish(run, sr, S.SUCCEEDED, output={})
        elif ntype == "wait":
            sr.status, sr.available_at = S.WAITING, timezone.now() + timedelta(seconds=node["seconds"])
            sr.save()
        elif ntype == "approval":
            Approval.objects.create(
                step_run=sr,
                workspace=run.workspace,
                title=str(resolve(node["title"], ctx))[:200],
                instructions=str(resolve(node.get("instructions", ""), ctx)),
                requested_by=run.requested_by,
                allow_self_approval=bool(node.get("allow_self_approval", False)),
            )
            sr.status = S.WAITING
            sr.save()
            add_event(run, "approval_requested", f"Approbation demandée : {node['title']}", node["id"])
        elif ntype == "subworkflow":
            _start_child(run, node, sr, ctx, resolve(node.get("inputs", {}), ctx), None)
            sr.status = S.WAITING
            sr.save()
        elif ntype == "foreach":
            items = evaluate(node["items"], ctx)
            if not isinstance(items, list) or len(items) > node["max_iterations"]:
                raise _StepError(
                    "TOO_MANY_ITEMS" if isinstance(items, list) else "NOT_A_LIST",
                    "La liste à parcourir est absente ou dépasse la limite de la boucle.",
                )
            sr.output = {"items": items, "results": [None] * len(items), "count": len(items)}
            sr.status = S.WAITING
            sr.save()
            _foreach_launch(run, node, sr, ctx)
    except _StepError as exc:
        _finish(run, sr, S.FAILED, error=exc.payload())
        add_event(run, "step_failed", f"Étape « {label} » échouée : {exc.message}", node["id"])
    except ExpressionError as exc:
        _finish(
            run,
            sr,
            S.FAILED,
            error={
                "code": "EXPRESSION_ERROR",
                "message": str(exc),
                "next_step": "Corrigez la configuration de l'étape.",
            },
        )
        add_event(run, "step_failed", f"Étape « {label} » échouée : {exc}", node["id"])
    return sr


class _StepError(Exception):
    def __init__(self, code, message, next_step="Corrigez la configuration puis relancez."):
        self.code, self.message, self.next_step = code, message, next_step

    def payload(self):
        return {"code": self.code, "message": self.message, "next_step": self.next_step}


def _dispatch_action(run, node, sr, ctx):
    pin = run.definition["actions"][node["id"]]
    av = ActionVersion.objects.select_related("action").get(pk=pin["action_version_id"])
    inputs = resolve(node.get("inputs", {}), ctx)
    try:
        jsonschema.validate(inputs, av.input_schema)
    except jsonschema.ValidationError as exc:
        raise _StepError(
            "INPUT_INVALID", f"Entrées invalides pour {av.action.name} — {explain_validation_error(exc)}"
        ) from None
    sr.attempt += 1
    sr.status = S.RUNNING
    sr.save()
    services.enqueue(
        workspace=run.workspace,
        environment=run.environment,
        action_version=av,
        user=run.requested_by,
        inputs=inputs,
        step_run=sr,
        priority=run.priority,
        connections=node.get("connections") or {},
    )


def _dispatch_ai(run, node, sr, ctx):
    from ai.builtin import get_ai_version

    if node.get("skill"):  # library skill: pinned version, instructions rendered with the mapped inputs
        from ai.models import AiSkillVersion

        sv = AiSkillVersion.objects.get(
            skill__workspace=run.workspace, skill__slug=node["skill"], version=node["skill_version"]
        )
        values = {k: resolve(v, ctx) for k, v in (node.get("inputs") or {}).items()}
        prompt = str(resolve(sv.instructions, {"inputs": values, "steps": {}, "vars": {}}))
        schema, provider = sv.output_schema, node.get("provider") or sv.default_provider
    else:
        prompt = str(resolve(node["prompt"], ctx))
        schema, provider = node["output_schema"], node["provider"]
    sr.attempt += 1
    sr.status = S.RUNNING
    sr.save()
    services.enqueue(
        workspace=run.workspace,
        environment=run.environment,
        action_version=get_ai_version(run.workspace),
        user=run.requested_by,
        inputs={"prompt": prompt, "output_schema": schema, "provider": provider},
        step_run=sr,
        priority=run.priority,
    )


def script_payload(node, ctx) -> dict:
    """Execution inputs of a script task: the code, the workflow inputs/variables and the bound values."""
    return {
        "script": node["script"],
        "inputs": ctx["inputs"],
        "vars": ctx["vars"],
        "bindings": {k: resolve(v, ctx) for k, v in (node.get("bindings") or {}).items()},
        "outputs": [o["name"] for o in node.get("outputs", [])],
    }


def _dispatch_script(run, node, sr, ctx):
    from .builtin import get_script_version

    sr.attempt += 1
    sr.status = S.RUNNING
    sr.save()
    limits = {"timeout_seconds": int(node.get("timeout_seconds", 60))}
    services.enqueue(
        workspace=run.workspace,
        environment=run.environment,
        action_version=get_script_version(run.workspace, node["language"]),
        user=run.requested_by,
        inputs=script_payload(node, ctx),
        step_run=sr,
        priority=run.priority,
        limits=limits,
    )


def _dispatch_step(run, node, sr, ctx):
    {"ai": _dispatch_ai, "script": _dispatch_script}.get(node["type"], _dispatch_action)(run, node, sr, ctx)


def _start_child(run, node, sr, ctx, inputs, index):
    from workflows.validation import resolve_workflow_version

    depth = run.definition.get("depth", 0) + 1
    if depth > MAX_DEPTH:
        raise _StepError("DEPTH_EXCEEDED", f"Profondeur maximale de sous-workflows atteinte ({MAX_DEPTH}).")
    _, version = resolve_workflow_version(run.workspace, node["workflow"], node["version"])
    try:
        jsonschema.validate(inputs, version.input_schema)
    except jsonschema.ValidationError as exc:
        raise _StepError(
            "INPUT_INVALID", f"Entrées du sous-workflow invalides — {explain_validation_error(exc)}"
        ) from None
    start_run(
        workspace=run.workspace,
        environment=run.environment,
        workflow_version=version,
        user=run.requested_by,
        inputs=inputs,
        parent_step=sr,
        child_index=index,
        priority=run.priority,
        depth=depth,
    )


def _foreach_launch(run, node, sr, ctx):
    items = sr.output["items"]
    started = sr.child_runs.count()
    running = sr.child_runs.filter(status__in=[R.RUNNING, R.COMPENSATING]).count()
    capacity = max(1, int(node.get("concurrency", 1))) - running
    for idx in range(started, min(len(items), started + max(0, capacity))):
        inputs = resolve(node.get("inputs", {}), ctx)
        inputs[node.get("item_input", "item")] = items[idx]
        _start_child(run, node, sr, ctx, inputs, idx)


# ----------------------------------------------------------------------------- poll a step
def _poll(run, node, sr, steps) -> bool:  # noqa: C901
    now = timezone.now()
    ntype, label = node["type"], node.get("name", node["id"])
    if ntype in ("action", "ai", "script"):
        if sr.status == S.RETRY_WAIT:
            if sr.available_at and sr.available_at <= now:
                _dispatch_step(run, node, sr, _ctx(run, steps))
                add_event(run, "step_retry", f"Étape « {label} » : tentative {sr.attempt}.", node["id"])
                return True
            return False
        if sr.status == S.UNCERTAIN:
            return False
        ex = sr.executions.order_by("-created_at").first()
        if ex is None or ex.status in (Execution.Status.QUEUED, Execution.Status.RUNNING):
            return False
        if ex.status == Execution.Status.SUCCEEDED:
            if ntype == "script":
                problem = _script_output_problem(node, ex.result)
                if problem:
                    _finish(
                        run,
                        sr,
                        S.FAILED,
                        error={
                            "code": "OUTPUT_INVALID",
                            "message": problem,
                            "next_step": "Corrigez le script ou les types de sortie déclarés.",
                        },
                    )
                    add_event(run, "step_failed", f"Étape « {label} » échouée : {problem}", node["id"])
                    return True
            _finish(run, sr, S.SUCCEEDED, output=ex.result)
            add_event(run, "step_succeeded", f"Étape « {label} » réussie.", node["id"])
            return True
        if ex.status == Execution.Status.CANCELLED:
            _finish(run, sr, S.CANCELLED)
            return True
        return _on_action_failure(run, node, sr, ex, label)
    if ntype == "wait":
        if sr.available_at and sr.available_at <= now:
            _finish(run, sr, S.SUCCEEDED, output={})
            add_event(run, "step_succeeded", f"Attente « {label} » terminée.", node["id"])
            return True
        return False
    if ntype == "approval":
        ap = sr.approval
        if ap.status == Approval.Status.PENDING:
            return False
        if ap.status == Approval.Status.APPROVED:
            out = {
                "approved": True,
                "decided_by": ap.decided_by.username if ap.decided_by else "",
                "comment": ap.comment,
            }
            _finish(run, sr, S.SUCCEEDED, output=out)
            add_event(run, "step_succeeded", f"Approbation « {label} » accordée.", node["id"])
        else:
            _finish(
                run,
                sr,
                S.FAILED,
                error={
                    "code": "APPROVAL_REJECTED",
                    "message": f"Refusée : {ap.comment or 'sans motif'}",
                    "next_step": "Corrigez la demande puis relancez le workflow.",
                },
            )
            add_event(run, "step_failed", f"Approbation « {label} » refusée.", node["id"])
        return True
    if ntype == "subworkflow":
        child = sr.child_runs.first()
        if child.status in (R.RUNNING, R.COMPENSATING):
            return False
        if child.status == R.SUCCEEDED:
            _finish(run, sr, S.SUCCEEDED, output=child.outputs or {})
        elif child.status == R.CANCELLED:
            _finish(run, sr, S.CANCELLED)
        else:
            _finish(
                run,
                sr,
                S.FAILED,
                error=child.error or {"code": "CHILD_FAILED", "message": "Le sous-workflow a échoué."},
            )
        add_event(run, "step_" + sr.status, f"Sous-workflow « {label} » : {sr.get_status_display()}.", node["id"])
        return True
    if ntype == "foreach":
        children = list(sr.child_runs.order_by("child_index"))
        changed = False
        results = list(sr.output["results"])
        for ch in children:
            if ch.status == R.SUCCEEDED and results[ch.child_index] != (ch.outputs or {}):
                results[ch.child_index] = ch.outputs or {}
                changed = True
        failed = [c for c in children if c.status in (R.FAILED, R.CANCELLED)]
        if failed:
            for ch in children:
                if ch.status == R.RUNNING:
                    WorkflowRun.objects.filter(pk=ch.pk).update(cancel_requested=True)
            if all(c.status not in (R.RUNNING, R.COMPENSATING) for c in children):
                _finish(
                    run,
                    sr,
                    S.FAILED,
                    error={
                        "code": "ITERATION_FAILED",
                        "message": f"L'itération {failed[0].child_index + 1} a échoué.",
                        "next_step": "Consultez le sous-workflow concerné.",
                    },
                )
                return True
            return changed
        if len(children) == sr.output["count"] and all(c.status == R.SUCCEEDED for c in children):
            _finish(run, sr, S.SUCCEEDED, output={**sr.output, "results": results, "items": None})
            add_event(run, "step_succeeded", f"Boucle « {label} » terminée ({len(children)} itérations).", node["id"])
            return True
        if changed:
            sr.output = {**sr.output, "results": results}
            sr.save()
        node_ctx = _ctx(run, steps)
        before = sr.child_runs.count()
        _foreach_launch(run, node, sr, node_ctx)
        return changed or sr.child_runs.count() != before
    return False


def _script_output_problem(node, result) -> str | None:
    """Declared script outputs must exist and have the declared type (never echo the value)."""
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
    for o in node.get("outputs", []):
        name, want = o["name"], o.get("type", "string")
        if name not in (result or {}):
            return f"La sortie « {name} » n'a pas été produite."
        val = result[name]
        ok = isinstance(val, types[want]) and not (want in ("integer", "number") and isinstance(val, bool))
        if not ok:
            return f"La sortie « {name} » doit être de type {want}."
    return None


def _on_action_failure(run, node, sr, ex, label) -> bool:
    av = ex.action_version
    err = dict(ex.error or {"code": "ERROR", "message": "Échec."})
    if err.get("code") == "OUTCOME_UNKNOWN":
        sr.status, sr.error = S.UNCERTAIN, err
        sr.save()
        add_event(run, "step_uncertain", f"Étape « {label} » : issue inconnue, une décision est requise.", node["id"])
        return True
    policy = node.get("retry") or {}
    safe = av.idempotent or av.side_effects == av.SideEffects.NONE
    if safe and err.get("code") in RETRYABLE and sr.attempt < int(policy.get("max_attempts", 1)):
        base = float(policy.get("backoff_seconds", 2))
        delay = base * (2 ** (sr.attempt - 1))
        delay *= 1 + random.uniform(-0.2, 0.2) * float(policy.get("jitter", 1))  # noqa: S311 - jitter, not crypto
        sr.status, sr.available_at, sr.error = S.RETRY_WAIT, timezone.now() + timedelta(seconds=max(0.0, delay)), err
        sr.save()
        add_event(
            run, "step_retry_scheduled", f"Étape « {label} » : nouvelle tentative dans {delay:.0f} s.", node["id"]
        )
        return True
    _finish(run, sr, S.FAILED, error=err)
    add_event(run, "step_failed", f"Étape « {label} » échouée : {err.get('message')}", node["id"])
    return True


# ----------------------------------------------------------------------------- failure, cancel, compensation
def _cancel_children(sr):
    sr_executions = sr.executions.filter(status__in=[Execution.Status.QUEUED, Execution.Status.RUNNING])
    for ex in sr_executions:
        services.request_cancel(ex)
    sr.child_runs.filter(status__in=[R.RUNNING, R.COMPENSATING]).update(cancel_requested=True)
    if sr.node_type == "approval" and hasattr(sr, "approval") and sr.approval.status == Approval.Status.PENDING:
        Approval.objects.filter(pk=sr.approval.pk).update(
            status=Approval.Status.REJECTED, comment="Annulée avec le workflow"
        )


def _finish_cancelled_or_late_success(run, sr):
    """A step whose last execution did succeed before the cancel landed keeps its real outcome, so that its
    compensation (if any) is still run."""
    last = sr.executions.order_by("-created_at").first() if sr.node_type == "action" else None
    if last is not None and last.status == Execution.Status.SUCCEEDED:
        _finish(run, sr, S.SUCCEEDED, output=last.result)
    else:
        _finish(run, sr, S.CANCELLED)


def _cancel_run(run, steps) -> bool:
    for sr in steps.values():
        if sr.status not in TERMINAL:
            _cancel_children(sr)
    settled = True
    for sr in steps.values():
        if sr.status in TERMINAL:
            continue
        ex_active = sr.executions.filter(status__in=[Execution.Status.QUEUED, Execution.Status.RUNNING]).exists()
        child_active = sr.child_runs.filter(status__in=[R.RUNNING, R.COMPENSATING]).exists()
        if ex_active or child_active:
            settled = False
        else:
            _finish_cancelled_or_late_success(run, sr)
    if settled:
        run.status, run.finished_at = R.CANCELLED, timezone.now()
        run.error = {"code": "CANCELLED", "message": "Exécution annulée. Les effets déjà réalisés ne sont pas annulés."}
        run.save()
        add_event(run, "cancelled", "Exécution annulée (les effets externes déjà produits subsistent).")
        _queue_callback(run)
    return True


def _begin_failure(run, nodes, steps, failed_sr):
    err = failed_sr.error or {"code": "STEP_FAILED", "message": "Une étape a échoué."}
    node = nodes[failed_sr.node_id]
    run.error = {
        "code": err.get("code", "STEP_FAILED"),
        "failed_step": failed_sr.node_id,
        "message": f"Étape « {node.get('name', failed_sr.node_id)} » : {err.get('message')}",
        "next_step": err.get("next_step", "Consultez le détail de l'étape."),
    }
    for sr in steps.values():
        if sr.status not in TERMINAL:
            _cancel_children(sr)
    add_event(run, "failing", "Une étape a échoué : arrêt du workflow.", failed_sr.node_id)
    run.status = R.COMPENSATING
    run.save()
    _advance_compensation(run, nodes, steps)


def _advance_compensation(run, nodes, steps):
    # 1) let cancelled work settle
    for sr in steps.values():
        if sr.node_type == "compensation" or sr.status in TERMINAL:
            continue
        ex_active = sr.executions.filter(status__in=[Execution.Status.QUEUED, Execution.Status.RUNNING]).exists()
        child_active = sr.child_runs.filter(status__in=[R.RUNNING, R.COMPENSATING]).exists()
        if ex_active or child_active:
            return
        if sr.status == S.UNCERTAIN:
            _finish(run, sr, S.FAILED, error=sr.error)
        else:
            _finish_cancelled_or_late_success(run, sr)
    # 2) run compensations sequentially, newest completed step first
    comp_steps = {k: v for k, v in steps.items() if v.node_type == "compensation"}
    for sr in comp_steps.values():
        if sr.status not in TERMINAL:
            ex = sr.executions.order_by("-created_at").first()
            if ex is None or ex.status in (Execution.Status.QUEUED, Execution.Status.RUNNING):
                return
            ok = ex.status == Execution.Status.SUCCEEDED
            _finish(run, sr, S.SUCCEEDED if ok else S.FAILED, output=ex.result, error=None if ok else ex.error)
            add_event(
                run,
                "compensation_" + ("done" if ok else "failed"),
                f"Compensation de « {sr.node_id[5:]} » : {'réussie' if ok else 'échouée'}.",
                sr.node_id,
            )
    done_for = {k[5:] for k in comp_steps}
    candidates = sorted(
        (
            s
            for s in steps.values()
            if s.status == S.SUCCEEDED
            and s.node_type == "action"
            and nodes[s.node_id].get("compensation")
            and s.node_id not in done_for
        ),
        key=lambda s: -s.seq,
    )
    if candidates:
        target = candidates[0]
        comp = nodes[target.node_id]["compensation"]
        pin = run.definition["actions"][f"comp_{target.node_id}"]
        av = ActionVersion.objects.get(pk=pin["action_version_id"])
        ctx = _ctx(run, steps)
        ctx["step"] = {"output": target.output or {}}
        csr = StepRun.objects.create(
            run=run, node_id=f"comp_{target.node_id}", node_type="compensation", status=S.RUNNING, attempt=1
        )
        try:
            inputs = resolve(comp.get("inputs", {}), ctx)
            jsonschema.validate(inputs, av.input_schema)
            services.enqueue(
                workspace=run.workspace,
                environment=run.environment,
                action_version=av,
                user=run.requested_by,
                inputs=inputs,
                step_run=csr,
                priority=run.priority,
            )
            add_event(run, "compensation_started", f"Compensation de « {target.node_id} » lancée.", csr.node_id)
        except (ExpressionError, jsonschema.ValidationError) as exc:
            _finish(run, csr, S.FAILED, error={"code": "COMPENSATION_INPUT", "message": str(exc)[:200]})
        return
    summary = [{"step": k[5:], "status": v.status} for k, v in comp_steps.items()]
    run.status, run.finished_at = R.FAILED, timezone.now()
    run.error = {**(run.error or {}), "compensation": summary}
    run.save()
    add_event(run, "failed", "Workflow échoué" + (" ; compensations exécutées." if summary else "."))
    _queue_callback(run)


# ----------------------------------------------------------------------------- human / operator actions
@transaction.atomic
def decide_approval(approval_id, user, approve: bool, comment: str = ""):
    """Server-side re-check at the sensitive point: permission, state, and (optional) self-approval ban."""
    from rest_framework.exceptions import PermissionDenied, ValidationError

    from tenancy.models import Membership
    from tenancy.permissions import APPROVE, ROLE_PERMISSIONS

    ap = Approval.objects.select_for_update().select_related("step_run__run").get(pk=approval_id)
    m = Membership.objects.filter(user=user, workspace=ap.workspace).first()
    if m is None or APPROVE not in ROLE_PERMISSIONS[m.role]:
        raise PermissionDenied("Vous n'avez pas le droit d'approuver dans cet espace.")
    if ap.status != Approval.Status.PENDING:
        raise ValidationError({"status": "Cette demande a déjà été traitée."})
    run = ap.step_run.run
    prod_ban = run.environment.kind == "prod" and (run.definition.get("governance") or {}).get("rules", {}).get(
        "forbid_self_approval_in_prod", True
    )
    if (prod_ban or not ap.allow_self_approval) and ap.requested_by_id == user.id:
        raise PermissionDenied("Vous ne pouvez pas approuver votre propre demande.")
    ap.status = Approval.Status.APPROVED if approve else Approval.Status.REJECTED
    ap.decided_by, ap.comment, ap.decided_at = user, comment[:500], timezone.now()
    ap.save()
    record("approval.decide", actor=user, workspace=ap.workspace, target=ap, approved=approve)
    return ap


@transaction.atomic
def decide_uncertain(step_run_id, user, decision: str, output=None):
    from rest_framework.exceptions import ValidationError

    sr = StepRun.objects.select_for_update().select_related("run").get(pk=step_run_id)
    if sr.status != S.UNCERTAIN:
        raise ValidationError({"status": "Cette étape n'attend pas de décision."})
    run = WorkflowRun.objects.select_for_update().get(pk=sr.run_id)
    if decision == "retry":
        sr.status, sr.available_at = S.RETRY_WAIT, timezone.now()
        sr.save()
    elif decision == "succeed":
        _finish(run, sr, S.SUCCEEDED, output=output or {})
    elif decision == "fail":
        _finish(
            run,
            sr,
            S.FAILED,
            error={**(sr.error or {}), "code": "ABANDONED", "message": "Abandonnée sur décision d'un opérateur."},
        )
    else:
        raise ValidationError({"decision": "Décision inconnue (retry, succeed, fail)."})
    add_event(
        run, "decision", f"Décision « {decision} » prise sur l'étape {sr.node_id} par {user.username}.", sr.node_id
    )
    record("step.decision", actor=user, workspace=run.workspace, target=sr, decision=decision)


@transaction.atomic
def request_run_cancel(run_id, user):
    run = WorkflowRun.objects.select_for_update().get(pk=run_id)
    if run.status in (R.RUNNING, R.COMPENSATING):
        run.cancel_requested = True
        run.save()
        add_event(run, "cancel_requested", f"Annulation demandée par {user.username}.")
        record("workflow_run.cancel", actor=user, workspace=run.workspace, target=run)
    return run
