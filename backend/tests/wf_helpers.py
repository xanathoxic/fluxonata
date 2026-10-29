import time

from catalog.models import Action, ActionVersion
from runs import engine, http_node
from runs.models import Execution, WorkflowRun
from runs.runners import SubprocessRunner
from runs.worker import process_one
from tenancy.models import Environment
from workflows.models import Workflow, WorkflowVersion
from workflows.validation import pin_graph, validate

STR = {"type": "string"}
ANY_OBJ = {"type": "object"}


def schema(required=(), **props):
    return {"type": "object", "required": list(required), "properties": props}


ACTIONS = {
    "echo": (
        "def run(i):\n    return {'value': i['value']}\n",
        schema(["value"], value=STR),
        schema(["value"], value=STR),
    ),
    "add": (
        "def run(i):\n    return {'sum': i['a'] + i['b']}\n",
        schema(["a", "b"], a={"type": "integer"}, b={"type": "integer"}),
        schema(["sum"], sum={"type": "integer"}),
    ),
    "boom": ("def run(i):\n    raise RuntimeError('boom')\n", schema(), schema()),
    "flaky": (
        "import os\n"
        "def run(i):\n"
        "    if not os.path.exists(i['marker']):\n"
        "        open(i['marker'], 'w').close()\n"
        "        raise RuntimeError('first call fails')\n"
        "    return {'ok': True}\n",
        schema(["marker"], marker=STR),
        schema(["ok"], ok={"type": "boolean"}),
    ),
    "log": (
        "def run(i):\n    open(i['path'], 'a').write(i['label'] + '\\n')\n    return {'ok': True}\n",
        schema(["path", "label"], path=STR, label=STR),
        schema(["ok"], ok={"type": "boolean"}),
    ),
    "sleepy": (
        "import time\ndef run(i):\n    time.sleep(i['seconds'])\n    return {'slept': i['seconds']}\n",
        schema(["seconds"], seconds={"type": "integer"}),
        schema(["slept"], slept={"type": "integer"}),
    ),
}


def make_action(ws, slug, version="1.0.0", **overrides):
    src, inp, out = ACTIONS[slug]
    action, _ = Action.objects.get_or_create(workspace=ws, slug=slug, defaults={"name": slug})
    fields = {
        "source": src,
        "input_schema": inp,
        "output_schema": out,
        "limits": {"timeout_seconds": 20, "memory_mb": 64},
    }
    return ActionVersion.objects.create(action=action, version=version, **(fields | overrides))


def node(nid, type_="action", **kw):
    return {"id": nid, "type": type_, "name": nid.upper(), **kw}


def edge(a, b, label=None):
    return {"from": a, "to": b, **({"label": label} if label else {})}


def publish(ws, slug, nodes, edges, inputs=None, outputs=None, user=None):
    graph = {"inputs": inputs or schema(), "nodes": nodes, "edges": edges, "outputs": outputs or {}}
    issues = [i for i in validate(graph, ws, slug) if i["level"] == "error"]
    assert not issues, issues
    wf, _ = Workflow.objects.get_or_create(workspace=ws, slug=slug, defaults={"name": slug})
    n = wf.versions.count()
    return WorkflowVersion.objects.create(
        workflow=wf, version=f"1.{n}.0", graph=pin_graph(graph, ws), input_schema=graph["inputs"], created_by=user
    )


def launch(ws, version, user, inputs=None, env="test"):
    run, _ = engine.start_run(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind=env),
        workflow_version=version,
        user=user,
        inputs=inputs or {},
    )
    return run


def drain(run_or_id, max_rounds=60, runner=None, until=None):
    """Single-threaded orchestrator + worker loop for tests."""
    rid = getattr(run_or_id, "pk", run_or_id)
    runner = runner or SubprocessRunner()
    for _ in range(max_rounds):
        engine.advance(rid)
        run = WorkflowRun.objects.get(pk=rid)
        if until and until(run):
            return run
        if run.status in ("succeeded", "failed", "cancelled"):
            # children may still need to progress; only stop when the whole tree is settled
            if not WorkflowRun.objects.filter(status__in=["running", "compensating"]).exists():
                return run
        worked = process_one("test-worker", runner) or http_node.process_one()
        for other in WorkflowRun.objects.filter(status__in=["running", "compensating"]).exclude(pk=rid):
            engine.advance(other.pk)
        if not worked:
            time.sleep(0.01)
    return WorkflowRun.objects.get(pk=rid)


def step(run, nid):
    return run.steps.get(node_id=nid)


def executions(run):
    return Execution.objects.filter(step_run__run=run)
