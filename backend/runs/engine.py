"""Durable workflow orchestration. PostgreSQL rows are the only source of truth.

`advance(run_id)` is idempotent and transactional: it recomputes what to do from the persisted StepRuns, so the
orchestrator can crash/restart at any point without repeating confirmed steps.

Branch activation:
- an edge is ACTIVE when its source succeeded and the label matches (next / condition true|false), or when the
  source failed and the label is "error"; DEAD when the source was skipped/cancelled or the label doesn't match.
- a non-join node starts when its single incoming edge is active, is skipped when dead.
- a join waits until every incoming branch is resolved, runs if >= 1 branch is active, is skipped otherwise.
- a race (« premier arrivé ») starts as soon as ONE incoming branch is active; the slower branches are cancelled (or left to
  finish, as chosen) and their results are not read.
Failure policy (fail-fast): a failed step without an "error" edge stops new work, cancels running work, runs the
declared compensations in reverse order (each may itself fail) and ends the run as failed.
"""

import random
import shutil
import subprocess
import time
from collections import defaultdict
from datetime import timedelta

import jsonschema
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import record
from catalog.models import ActionVersion
from tenancy import governance
from workflows import transforms
from workflows.expressions import ExpressionError, coerce_inputs, evaluate, resolve
from workflows.validation import explain_validation_error, topo_order

from . import agent, alerts, http_node, ldap_node, proposed, services, sftp_node, smtp_node, sql_node
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
    """Workflow variables; a value set for the run's environment (`env_values`) replaces the default one."""
    kind = run.environment.kind
    return {
        v["name"]: (v.get("env_values") or {}).get(kind, v.get("value"))
        for v in (run.definition["graph"].get("variables") or [])
    }


def _ctx(run, steps):
    return {
        "inputs": run.inputs,
        "vars": _variables(run),
        "run": {"id": str(run.id), "started_at": run.created_at.isoformat(), "environment": run.environment.kind},
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
    """Empreinte de l'image d'exécution tracée dans la définition d'un run (AR3.8).

    1) Une image épinglée par empreinte (`nom@sha256:…`, voir scripts/pin-images.sh) EST son empreinte : aucune interrogation, reproductible, valable dans tous
       les rôles. 2) Sinon, ce que dit le démon Docker local — seulement si le CLI existe : l'API et l'orchestrateur de la pile de production n'en ont plus (AR2.5)
       et ne l'appellent donc plus ; la valeur est alors None et la santé de la plateforme le signale (« images non épinglées »)."""
    if image not in _digest_cache:
        if "@sha256:" in image:
            _digest_cache[image] = image.split("@", 1)[1]
        elif shutil.which("docker") is None:
            _digest_cache[image] = None
        else:
            try:
                out = subprocess.run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", image],
                    capture_output=True,
                    text=True,
                    timeout=10,
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
    until=None,
    graph_override=None,
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
    graph = graph_override or workflow_version.graph
    proposed.refuse_proposed_in_graph(workspace, graph)  # ADR 0025 : aucune proposition non relue n'est exécutée
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
    for n in graph["nodes"]:
        if n["type"] == "agent":
            for slug in agent.tool_slugs(n):
                av = (
                    ActionVersion.objects.filter(action__workspace=workspace, action__slug=slug)
                    .order_by("-created_at")
                    .first()
                )
                if av is not None:
                    pins[agent.pin_key(n["id"], slug)] = {"action_version_id": av.id, "source_digest": av.source_digest}
    definition = {
        "graph": graph,
        "workflow": {
            "slug": workflow_version.workflow.slug,
            "version": workflow_version.version,
            "digest": workflow_version.digest,
        },
        "actions": pins,
        **({"until": until} if until else {}),
        **({"draft": True} if graph_override else {}),
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


def reusable_steps(old) -> dict:
    """node id -> finished StepRun that a « relancer depuis l'étape en échec » may carry over from a failed run: steps that
    SUCCEEDED (or were skipped) whose effects were NOT undone by compensation, and whose predecessors are all carried over
    too (so the new run restarts exactly at the first thing that has to be done again)."""
    nodes, incoming, _, order = _graph_views(old.definition["graph"])
    undone = {c["step"] for c in (old.error or {}).get("compensation", []) if c.get("status") == "succeeded"}
    done = {sr.node_id: sr for sr in old.steps.filter(status__in=[S.SUCCEEDED, S.SKIPPED])}
    kept: dict = {}
    for nid in order:
        sr = done.get(nid)
        if sr is None or nid in undone:
            continue
        if all(e["from"] in kept for e in incoming[nid]):
            kept[nid] = sr
    return kept


def start_run_from_failed(old, user):
    """New run of the same version and inputs that restarts at the failed step: earlier results are reused as they were
    (their effects are not repeated), everything from the failed step on is executed again."""
    kept = reusable_steps(old)
    run, _ = start_run(
        workspace=old.workspace,
        environment=old.environment,
        workflow_version=old.workflow_version,
        user=user,
        inputs=old.inputs,
        trigger="rerun",
    )
    for seq, (nid, sr) in enumerate(kept.items(), start=1):
        StepRun.objects.create(
            run=run,
            node_id=nid,
            node_type=sr.node_type,
            status=sr.status,
            output=sr.output,
            attempt=sr.attempt,
            finished_at=timezone.now(),
            seq=seq,
        )
    add_event(
        run, "resumed_from_failed", f"Relance depuis l'étape en échec : {len(kept)} étape(s) reprise(s) telles quelles."
    )
    return run, len(kept)


# ----------------------------------------------------------------------------- advance
# --- Quand repasser sur un run ? (AR4.4) -----------------------------------------------------------------------------------------------------------
# Mesuré (scripts/loadtest_orchestrator.py) : un passage de `advance` sur un run qui n'a RIEN à faire coûte un verrou de ligne, donc une écriture dans le journal de
# PostgreSQL et un `fsync` au COMMIT (20-38 ms sur la machine de développement) ; avec 150 runs en attente, un tour durait 5,5 s. On ne repasse donc que lorsqu'il y a
# une raison :
#   - attente d'une minuterie ou d'une décision humaine (« wait », « approval ») : à l'échéance, ou au plus tard après IDLE_RECHECK (60 s : à 10 s, 400 runs en attente
#     coûtaient encore 40 passages par seconde, soit 2,8 s par tour, à 20 ms de COMMIT) ;
#   - attente d'un résultat de worker (étape « action » en cours) : après EXEC_RECHECK ;
#   - tout autre état (sous-workflow, boucle, HTTP, agent, nouvelle tentative, run neuf…) : au prochain tour, comme avant.
# Les événements externes ramènent l'échéance à « maintenant » (`wake`) : l'attente n'est donc pas allongée en fonctionnement normal. Si un chemin oubliait de
# réveiller le run, le filet ci-dessus borne le retard (jamais de run perdu).
IDLE_RECHECK = timedelta(
    seconds=60
)  # filet de sécurité d'un événement non annoncé ; les événements connus réveillent le run immédiatement
EXEC_RECHECK = timedelta(seconds=2)


def wake(run_id) -> None:
    """Un événement externe vient d'arriver pour ce run : l'orchestrateur doit repasser dessus au prochain tour."""
    WorkflowRun.objects.filter(pk=run_id).update(next_check_at=timezone.now())


def _schedule_next_check(run) -> None:
    now = timezone.now()
    if run.status not in (R.RUNNING, R.COMPENSATING):
        when = None
    else:
        candidates = []
        for sr in run.steps.exclude(status__in=TERMINAL):
            if sr.status == S.WAITING and sr.node_type in ("wait", "approval"):
                due = sr.available_at if sr.node_type == "wait" and sr.available_at else None
                candidates.append(min(due, now + IDLE_RECHECK) if due else now + IDLE_RECHECK)
            elif sr.status == S.RUNNING and sr.node_type == "action":
                candidates.append(now + EXEC_RECHECK)
            else:
                candidates.append(now)
        when = min(candidates) if candidates else now
    WorkflowRun.objects.filter(pk=run.pk).update(next_check_at=when)
    run.next_check_at = when


def advance(run_id, only_if_due: bool = False) -> bool:
    """One orchestration step for a run. Returns True when the run was processed.

    `only_if_due` (tour de l'orchestrateur, AR4.5) : ne rien faire si le run n'est plus « dû » une fois le verrou obtenu. Plusieurs orchestrateurs lisent la même liste de runs
    dus ; celui qui arrive second trouve le run déjà traité (son `next_check_at` est dans le futur) et passe au suivant sans écrire — sans cela chacun referait le travail des autres."""
    with transaction.atomic():
        qs = WorkflowRun.objects.select_for_update(skip_locked=True).filter(
            pk=run_id, status__in=[R.RUNNING, R.COMPENSATING]
        )
        if only_if_due:
            qs = qs.filter(Q(next_check_at__isnull=True) | Q(next_check_at__lte=timezone.now()))
        run = qs.first()
        if run is None:
            return False
        processed = _advance_locked(run)
        _schedule_next_check(run)
        return processed


def _advance_locked(run) -> bool:
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
        until = run.definition.get("until")
        if until and until in steps and steps[until].status in TERMINAL:
            break
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
    until = run.definition.get("until")
    if until and until in steps and steps[until].status in TERMINAL:
        # « exécuter jusqu'ici » (development): nothing new starts after that step; once the running ones are over the
        # run ends as a partial success
        if all(s.status in TERMINAL for s in steps.values()):
            _complete_partial(run, nodes[until].get("name", until))
        return True
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
            elif nodes[e["from"]]["type"] == "switch":
                active = label == (src.output or {}).get("result")
            else:
                active = label == "next"
        elif src.status == S.FAILED:
            active = label == "error"
        else:
            active = False
        states.append("active" if active else "dead")
    if node["type"] == "race":
        return "ready" if "active" in states else ("wait" if "pending" in states else "skipped")
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


def _start_error_workflow(run):
    _start_followup(run, "error")


def _start_followup(run, kind: str):
    """A finished top-level run starts the workflow chosen for that outcome (« workflow d'erreur » when it failed,
    « workflow de succès » when it succeeded), with what happened as inputs. It never chains: a run started this way does
    not start another one. Failing to start it (not published, required inputs, not promoted…) is recorded on the run,
    never raised."""
    if run.parent_step_id is not None or run.definition.get("trigger") in ("error_workflow", "success_workflow"):
        return
    wf = run.workflow_version.workflow
    slug = wf.error_workflow if kind == "error" else wf.success_workflow
    if not slug:
        return
    from workflows.models import Workflow

    target = Workflow.objects.filter(workspace=run.workspace, slug=slug).first()
    version = target.latest_version() if target else None
    tag = "error_workflow" if kind == "error" else "success_workflow"
    if version is None:
        add_event(run, f"{tag}_skipped", f"Workflow « {slug} » : aucune version publiée.")
        return
    err = run.error or {}
    facts = {
        "workflow": wf.slug,
        "workflow_name": wf.name,
        "run_id": str(run.id),
        "environment": run.environment.kind,
        "step": str(err.get("failed_step", "")),
        "code": str(err.get("code", "")),
        "message": str(err.get("message", ""))[:500],
    }
    if kind == "success" and len(str(run.outputs or {})) <= 16000:
        facts["outputs"] = run.outputs or {}
    declared = (version.input_schema or {}).get("properties", {})
    try:
        with transaction.atomic():
            child, _ = start_run(
                workspace=run.workspace,
                environment=run.environment,
                workflow_version=version,
                user=run.requested_by,
                inputs={k: v for k, v in facts.items() if k in declared},
                trigger=tag,
            )
            child.definition = {**child.definition, "trigger": tag}
            child.save(update_fields=["definition"])
        add_event(run, f"{tag}_started", f"Workflow « {target.name} » lancé.")
    except Exception as exc:  # noqa: BLE001  (the follow-up must never break the run's own end)
        add_event(run, f"{tag}_skipped", f"Workflow « {target.name} » non lancé : {str(exc)[:150]}")


def _complete_partial(run, label: str):
    run.status, run.outputs, run.finished_at = R.SUCCEEDED, {}, timezone.now()
    run.save()
    add_event(run, "succeeded", f"Exécution arrêtée volontairement après « {label} » (essai de développement).")


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
        _start_error_workflow(run)
        alerts.enqueue_run_failed(run)
        return
    run.status, run.outputs, run.finished_at = R.SUCCEEDED, outputs, timezone.now()
    run.save()
    add_event(run, "succeeded", "Workflow terminé avec succès.")
    _queue_callback(run)
    _start_followup(run, "success")


# ----------------------------------------------------------------------------- start a step
def _start(run, node, steps) -> StepRun:
    ntype = node["type"]
    sr = StepRun.objects.create(run=run, node_id=node["id"], node_type=ntype, status=S.RUNNING)
    label = node.get("name", node["id"])
    add_event(run, "step_started", f"Étape « {label} » démarrée.", node["id"])
    ctx = _ctx(run, steps)
    pin = (run.definition["graph"].get("pins") or {}).get(node["id"])
    if isinstance(pin, dict) and run.environment.kind == "dev" and ntype not in ("join", "race", "condition", "switch"):
        # development only: a pinned output replaces the step (nothing is executed, no effect is produced)
        _finish(run, sr, S.SUCCEEDED, output=pin)
        add_event(run, "step_pinned", f"Étape « {label} » : sortie épinglée utilisée (non exécutée).", node["id"])
        return sr
    try:
        if ntype == "action":
            _dispatch_action(run, node, sr, ctx)
        elif ntype == "condition":
            result = bool(evaluate(node["expr"], ctx))
            _finish(run, sr, S.SUCCEEDED, output={"result": result})
            add_event(run, "step_succeeded", f"Condition « {label} » : {'oui' if result else 'non'}.", node["id"])
        elif ntype == "switch":
            chosen = next((c["id"] for c in node["cases"] if evaluate(c["expr"], ctx)), "default")
            _finish(run, sr, S.SUCCEEDED, output={"result": chosen})
            add_event(run, "step_succeeded", f"Choix « {label} » : {chosen}.", node["id"])
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
        elif ntype == "race":
            _start_race(run, node, sr, steps)
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
        elif ntype == "transform":
            try:
                out = transforms.apply(node, ctx)
            except transforms.TransformError as exc:
                raise _StepError(
                    "TRANSFORM_ERROR", str(exc), "Vérifiez la liste en entrée et les expressions."
                ) from None
            _finish(run, sr, S.SUCCEEDED, output=out)
            add_event(run, "step_succeeded", f"Transformation « {label} » terminée.", node["id"])
        elif ntype == "agent":
            tools = agent.specs(run, node)
            if not tools:
                raise _StepError(
                    "AGENT_NO_TOOL",
                    "L'agent n'a aucun outil utilisable (actions introuvables).",
                    "Choisissez des actions du catalogue.",
                )
            agent.start(run, node, sr, str(resolve(node.get("goal", ""), ctx)))
            add_event(
                run,
                "agent_started",
                f"Agent « {label} » lancé ({'réel' if node.get('mode') == 'reel' else 'test : outils simulés'}).",
                node["id"],
            )
        elif ntype == "sftp":
            try:
                spec = sftp_node.prepare(node, resolve, ctx, run.workspace_id, run.environment_id)
            except sftp_node.SftpStepError as exc:
                raise _StepError(exc.code, exc.message, exc.next_step) from None
            if spec["mode"] == "test":
                _finish(run, sr, S.SUCCEEDED, output=sftp_node.simulated_output(spec))
                add_event(
                    run,
                    "step_succeeded",
                    f"Fichier SFTP « {label} » simulé (mode test : rien n'est envoyé).",
                    node["id"],
                )
            else:
                http_node.queue(sr, spec)
                add_event(run, "http_queued", f"Fichier SFTP « {label} » en attente d'un travailleur.", node["id"])
        elif ntype == "smtp":
            try:
                spec = smtp_node.prepare(node, resolve, ctx, run.workspace_id, run.environment_id)
            except smtp_node.SmtpStepError as exc:
                raise _StepError(exc.code, exc.message, exc.next_step) from None
            if spec["mode"] == "test":
                _finish(run, sr, S.SUCCEEDED, output=smtp_node.simulated_output(spec))
                add_event(
                    run, "step_succeeded", f"E-mail « {label} » simulé (mode test : rien n'est envoyé).", node["id"]
                )
            else:
                http_node.queue(sr, spec)
                add_event(run, "http_queued", f"E-mail « {label} » en attente d'un travailleur.", node["id"])
        elif ntype == "ldap":
            try:
                spec = ldap_node.prepare(node, resolve, ctx, run.workspace_id, run.environment_id)
            except ldap_node.LdapStepError as exc:
                raise _StepError(exc.code, exc.message, exc.next_step) from None
            if spec["mode"] == "test":
                _finish(run, sr, S.SUCCEEDED, output=ldap_node.simulated_output(spec))
                add_event(run, "step_succeeded", f"Recherche d'annuaire « {label} » simulée (mode test).", node["id"])
            else:
                http_node.queue(sr, spec)
                add_event(
                    run, "http_queued", f"Recherche d'annuaire « {label} » en attente d'un travailleur.", node["id"]
                )
        elif ntype == "sql":
            try:
                spec = sql_node.prepare(node, resolve, ctx, run.workspace_id, run.environment_id)
            except sql_node.SqlStepError as exc:
                raise _StepError(exc.code, exc.message, exc.next_step) from None
            if spec["mode"] == "test":
                _finish(run, sr, S.SUCCEEDED, output=sql_node.simulated_output(spec))
                add_event(
                    run,
                    "step_succeeded",
                    f"Requête SQL « {label} » simulée (mode test : rien n'est envoyé).",
                    node["id"],
                )
            else:
                http_node.queue(sr, spec)
                add_event(run, "http_queued", f"Requête SQL « {label} » en attente d'un travailleur.", node["id"])
        elif ntype == "http":
            try:
                spec = http_node.prepare(node, resolve, ctx, run.workspace_id, run.environment_id)
            except http_node.HttpStepError as exc:
                raise _StepError(exc.code, exc.message, exc.next_step) from None
            if spec["mode"] == "test":
                _finish(run, sr, S.SUCCEEDED, output=http_node.simulated_output(spec))
                add_event(
                    run, "step_succeeded", f"Requête « {label} » simulée (mode test : rien n'est envoyé).", node["id"]
                )
            else:
                http_node.queue(sr, spec)
                add_event(run, "http_queued", f"Requête « {label} » en attente d'un travailleur.", node["id"])
        elif ntype == "subworkflow":
            _start_child(run, node, sr, ctx, resolve(node.get("inputs", {}), ctx), None)
            sr.status = S.WAITING
            sr.save()
        elif ntype == "while":
            sr.output = {"iterations": 0, "results": [], "last": {}, "stopped": ""}
            _start_child(run, node, sr, ctx, resolve(node.get("inputs", {}), {**ctx, "last": {}, "iteration": 0}), 0)
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
    inputs = resolve(coerce_inputs(node.get("inputs", {}), av.input_schema), ctx)
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


def _poll_while(run, node, sr, steps, now, label) -> bool:
    """« Répéter tant que » : one child run at a time; after each one the condition (which sees `last`, the previous result,
    and `iteration`, how many are done) decides whether to go on, after an optional delay; bounded by max_iterations."""
    children = list(sr.child_runs.order_by("child_index"))
    done = sr.output["iterations"]
    if sr.available_at is not None:  # waiting between two repetitions
        if sr.available_at > now:
            return False
        sr.available_at = None
        ctx = {**_ctx(run, steps), "last": sr.output["last"], "iteration": done}
        _start_child(run, node, sr, ctx, resolve(node.get("inputs", {}), ctx), done)
        sr.save()
        return True
    cur = children[-1]
    if cur.status in (R.RUNNING, R.COMPENSATING):
        return False
    if cur.status != R.SUCCEEDED:
        _finish(
            run,
            sr,
            S.FAILED,
            error={
                "code": "ITERATION_FAILED",
                "message": f"La répétition {cur.child_index + 1} a échoué.",
                "next_step": "Consultez le sous-workflow concerné.",
            },
        )
        add_event(run, "step_failed", f"Boucle « {label} » : la répétition {cur.child_index + 1} a échoué.", node["id"])
        return True
    last = cur.outputs or {}
    results = [*sr.output["results"], last]
    done += 1
    ctx = {**_ctx(run, steps), "last": last, "iteration": done}
    try:
        again = bool(evaluate(str(node["condition"]), ctx))
    except ExpressionError as exc:
        _finish(
            run,
            sr,
            S.FAILED,
            error={
                "code": "EXPRESSION_ERROR",
                "message": f"Condition de la boucle : {exc}",
                "next_step": "Corrigez la condition.",
            },
        )
        return True
    cap = int(node.get("max_iterations", 10))
    if again and done < cap:
        sr.output = {"iterations": done, "results": results, "last": last, "stopped": ""}
        delay = int(node.get("delay_seconds", 0))
        if delay:
            sr.available_at = now + timedelta(seconds=delay)
            sr.save()
            return True
        _start_child(run, node, sr, ctx, resolve(node.get("inputs", {}), ctx), done)
        sr.save()
        return True
    stopped = "condition" if not again else "max_iterations"
    _finish(run, sr, S.SUCCEEDED, output={"iterations": done, "results": results, "last": last, "stopped": stopped})
    add_event(
        run, "step_succeeded", f"Boucle « {label} » terminée ({done} répétition(s), arrêt : {stopped}).", node["id"]
    )
    return True


def _foreach_launch(run, node, sr, ctx):
    items = sr.output["items"]
    started = sr.child_runs.count()
    running = sr.child_runs.filter(status__in=[R.RUNNING, R.COMPENSATING]).count()
    capacity = max(1, int(node.get("concurrency", 1))) - running
    interval = int(node.get("interval_seconds") or 0)
    if interval:  # rate limit: at most one iteration starts per interval
        last = sr.output.get("last_start")
        now_ts = timezone.now().timestamp()
        if last is not None and now_ts - last < interval:
            return
        capacity = min(capacity, 1)
        if started < len(items) and capacity > 0:
            sr.output = {**sr.output, "last_start": now_ts}
            sr.save(update_fields=["output"])
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
    if ntype == "agent":
        outcome = agent.advance(run, node, sr)
        if outcome is None:
            return False
        if outcome[0] == "done":
            _finish(run, sr, S.SUCCEEDED, output=outcome[1])
            add_event(
                run,
                "step_succeeded",
                f"Agent « {label} » terminé ({outcome[1]['iterations']} tour(s), {outcome[1]['stopped']}).",
                node["id"],
            )
        else:
            _finish(run, sr, S.FAILED, error=outcome[1])
            add_event(run, "step_failed", f"Agent « {label} » échoué : {outcome[1].get('message', '')}", node["id"])
        return True
    if ntype in ("sql", "ldap", "sftp", "smtp"):
        job = sr.http_job or {}
        if job.get("state") == "running" and http_node.lease_expired(job):
            _finish(
                run,
                sr,
                S.FAILED,
                error={
                    "code": "SQL_INTERRUPTED",
                    "message": "L'appel à la base a été interrompu.",
                    "next_step": "Relancez l'étape.",
                },
            )
            return True
        if job.get("state") != "done":
            return False
        if job.get("error"):
            _finish(run, sr, S.FAILED, error=job["error"])
            add_event(run, "step_failed", f"Étape « {label} » échouée : {job['error']['message']}", node["id"])
        else:
            _finish(run, sr, S.SUCCEEDED, output=job["result"])
            res = job["result"]
            what = {"sql": "Requête SQL", "ldap": "Recherche d'annuaire", "sftp": "Fichier SFTP", "smtp": "E-mail"}[
                ntype
            ]
            detail = f"{res['count']} résultat(s)" if "count" in res else "terminé"
            add_event(run, "step_succeeded", f"{what} « {label} » : {detail}.", node["id"])
        return True
    if ntype == "http":
        job = sr.http_job or {}
        if job.get("state") == "running" and http_node.lease_expired(job):
            job = {
                **job,
                "state": "done",
                "error": {
                    "code": "HTTP_INTERRUPTED",
                    "message": "L'appel a été interrompu : la requête a peut-être été reçue par le service.",
                    "next_step": "Vérifiez côté service avant de relancer.",
                },
            }
        if job.get("state") != "done":
            return False
        spec = job.get("spec") or {}
        wait = http_node.retry_delay(spec, sr.attempt + 1, job.get("error"), job.get("result"))
        if wait is not None:
            sr.attempt += 1
            sr.http_job = {
                **job,
                "state": "queued",
                "error": None,
                "result": None,
                "claimed_at": None,
                "not_before": time.time() + wait,
            }
            sr.save(update_fields=["attempt", "http_job"])
            add_event(run, "http_retry", f"Requête « {label} » : nouvelle tentative dans {int(wait)} s.", node["id"])
            return True
        if job.get("error"):
            _finish(run, sr, S.FAILED, error=job["error"])
            add_event(run, "step_failed", f"Requête « {label} » échouée : {job['error']['message']}", node["id"])
            return True
        res = job["result"]
        if not res["ok"] and (job.get("spec") or {}).get("fail_on_status", True):
            _finish(
                run,
                sr,
                S.FAILED,
                output=res,
                error={
                    "code": f"HTTP_{res['status']}",
                    "message": f"Le service a répondu {res['status']}.",
                    "next_step": "Consultez la réponse de l'étape.",
                },
            )
            add_event(run, "step_failed", f"Requête « {label} » : réponse {res['status']}.", node["id"])
            return True
        _finish(run, sr, S.SUCCEEDED, output=res)
        add_event(run, "step_succeeded", f"Requête « {label} » : réponse {res['status']}.", node["id"])
        return True
    if ntype == "wait" and node.get("resume") in ("webhook", "form"):
        if sr.resume_payload is not None:
            _finish(run, sr, S.SUCCEEDED, output={"resumed": True, "payload": sr.resume_payload})
            add_event(run, "step_succeeded", f"Attente « {label} » reprise par appel HTTP.", node["id"])
            return True
        if sr.available_at and sr.available_at <= now:
            _finish(run, sr, S.SUCCEEDED, output={"resumed": False, "payload": {}})
            add_event(
                run, "step_succeeded", f"Attente « {label} » : délai maximum atteint, aucune reprise reçue.", node["id"]
            )
            return True
        return False
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
    if ntype == "while":
        return _poll_while(run, node, sr, steps, now, label)
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
            _finish(
                run,
                sr,
                S.SUCCEEDED,
                output={k: v for k, v in {**sr.output, "results": results, "items": None}.items() if k != "last_start"},
            )
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


def _start_race(run, node, sr, steps):
    """The first active incoming branch wins: the output names it and carries its result (`winner`, `winner_name`, `value`);
    the branches still running are cancelled unless the step says `losers: finish`. An effect already produced by a slower
    branch is not undone (same rule as cancelling a run)."""
    nodes, incoming, _, _ = _graph_views(run.definition["graph"])
    active = []
    for e in incoming[node["id"]]:
        src = steps.get(e["from"])
        if src is not None and src.status == S.SUCCEEDED and (e.get("label") or "next") == "next":
            active.append(src)
    if not active:  # an error exit or a decision label reached the race: the branch that arrived first still wins
        active = [
            steps[e["from"]]
            for e in incoming[node["id"]]
            if e["from"] in steps and steps[e["from"]].status in TERMINAL and steps[e["from"]].status != S.SKIPPED
        ]
    win = min(active, key=lambda s: (s.finished_at or timezone.now(), s.node_id))
    _finish(
        run,
        sr,
        S.SUCCEEDED,
        output={"winner": win.node_id, "winner_name": nodes[win.node_id].get("name", win.node_id), "value": win.output},
    )
    add_event(run, "race_won", f"Premier arrivé : « {nodes[win.node_id].get('name', win.node_id)} ».", node["id"])
    if node.get("losers", "cancel") != "cancel":
        return
    behind, stack = set(), [e["from"] for e in incoming[node["id"]]]
    while stack:  # every step upstream of the race that is still going belongs to a slower branch
        nid = stack.pop()
        if nid in behind:
            continue
        behind.add(nid)
        stack += [e["from"] for e in incoming[nid]]
    for nid in behind:
        slow = steps.get(nid)
        if slow is None or slow.status in TERMINAL:
            continue
        _cancel_children(slow)
        still = (
            slow.executions.filter(status__in=[Execution.Status.QUEUED, Execution.Status.RUNNING]).exists()
            or slow.child_runs.filter(status__in=[R.RUNNING, R.COMPENSATING]).exists()
        )
        if not still:
            _finish_cancelled_or_late_success(run, slow)
        add_event(
            run, "race_lost", f"Étape « {nodes[nid].get('name', nid)} » annulée : une autre branche est arrivée.", nid
        )


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
    _start_error_workflow(run)
    alerts.enqueue_run_failed(run)


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
    wake(run.pk)  # le run attendait cette décision : pas question de la laisser attendre le filet de sécurité
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
        run.next_check_at = timezone.now()
        run.save(update_fields=["next_check_at"])
    return run
