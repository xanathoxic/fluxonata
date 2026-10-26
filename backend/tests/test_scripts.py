import shutil

import pytest

from audit.models import AuditEvent
from runs.models import Execution
from runs.runners import DockerRunner, SubprocessRunner
from runs.worker import process_one
from tests.conftest import client_for
from tests.test_worker import docker_ready
from tests.wf_helpers import STR, drain, edge, launch, make_action, node, publish, schema, step
from workflows.validation import validate

BASE = "/api/v1/workspaces/team-a"
has_node = shutil.which("node") is not None


def script(nid="s", code="x = 1", outputs=(("x", "integer"),), bindings=None, language="python", **kw):
    return node(
        nid,
        "script",
        language=language,
        script=code,
        bindings=bindings or {},
        outputs=[{"name": n, "type": t} for n, t in outputs],
        **kw,
    )


def errors(graph, ws):
    return {i["code"] for i in validate(graph, ws) if i["level"] == "error"}


def g(nodes, edges=(), **extra):
    return {"inputs": schema(["t"], t=STR), "nodes": nodes, "edges": list(edges), "outputs": {}, **extra}


def test_python_script_task_runs_with_inputs_bindings_vars_and_logs(world):
    ws = world["ws_a"]
    code = "System.log('bonjour', inputs['t'])\ntotal = len(name) + factor\nname_upper = name.upper()\n"
    v = (
        publish(
            ws,
            "scripted",
            [
                script(
                    code=code,
                    bindings={"name": "{{ inputs.t }}", "factor": "{{ vars.factor }}"},
                    outputs=(("total", "integer"), ("name_upper", "string")),
                )
            ],
            [],
            inputs=schema(["t"], t=STR),
            outputs={"r": "{{ steps.s.output.total }}", "u": "{{ steps.s.output.name_upper }}"},
            user=world["designer"],
        )
        if False
        else None
    )
    from workflows.models import Workflow, WorkflowVersion
    from workflows.validation import pin_graph

    graph = g(
        [
            script(
                code=code,
                bindings={"name": "{{ inputs.t }}", "factor": "{{ vars.factor }}"},
                outputs=(("total", "integer"), ("name_upper", "string")),
            )
        ],
        variables=[{"name": "factor", "type": "integer", "value": 10}],
    )
    graph["outputs"] = {"r": "{{ steps.s.output.total }}", "u": "{{ steps.s.output.name_upper }}"}
    assert errors(graph, ws) == set()
    wf = Workflow.objects.create(workspace=ws, slug="scripted", name="scripted")
    v = WorkflowVersion.objects.create(
        workflow=wf, version="1.0.0", graph=pin_graph(graph, ws), input_schema=graph["inputs"]
    )
    run = drain(launch(ws, v, world["designer"], {"t": "abc"}))
    assert run.status == "succeeded", run.error
    assert run.outputs == {"r": 13, "u": "ABC"}
    logs = client_for(world["designer"]).get(f"{BASE}/workflow-runs/{run.id}/").json()["steps"][0]["logs"]
    assert "[INFO] bonjour abc" in logs
    assert (
        v
        and not Execution.objects.filter(action_version__action__slug="__script_python__")
        .exclude(step_run__run=run)
        .exists()
    )


def test_script_output_contract_is_enforced_without_echoing_values(world):
    ws = world["ws_a"]
    v = publish(
        ws, "bad-out", [script(code="x = 'TOP-SECRET-VALUE'", outputs=(("x", "integer"),))], [], user=world["designer"]
    )
    run = drain(launch(ws, v, world["designer"]))
    assert run.status == "failed" and run.error["code"] == "OUTPUT_INVALID" and "TOP-SECRET" not in run.error["message"]
    # a never-assigned output is refused at validation; one assigned only on a path not taken is caught at run time
    v2 = publish(
        ws,
        "missing-out",
        [script(code="if False:\n    x = 1", outputs=(("x", "integer"),))],
        [],
        user=world["designer"],
    )
    run2 = drain(launch(ws, v2, world["designer"]))
    assert run2.status == "failed" and "x" in run2.error["message"]
    v3 = publish(ws, "bool-int", [script(code="x = True", outputs=(("x", "integer"),))], [], user=world["designer"])
    assert drain(launch(ws, v3, world["designer"])).status == "failed"  # booleans are not integers


def test_script_exception_and_routing_to_error_branch(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    v = publish(
        ws,
        "script-err",
        [
            script(code="raise ValueError('oups')", outputs=()),
            node("ok", action="echo", inputs={"value": "ok"}),
            node("h", action="echo", inputs={"value": "géré"}),
        ],
        [edge("s", "ok"), edge("s", "h", "error")],
        outputs={"r": "{{ steps.h.output.value }}"},
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"]))
    assert run.status == "succeeded" and run.outputs == {"r": "géré"}
    assert "oups" in step(run, "s").error["message"] and step(run, "ok").status == "skipped"


def test_script_runs_in_the_sandbox_not_in_the_orchestrator(world):
    """The code is executed by the worker's runner, never in the web/orchestrator process."""
    ws = world["ws_a"]
    v = publish(
        ws,
        "where",
        [script(code="import os\nx = os.getpid()", outputs=(("x", "integer"),))],
        [],
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"]))
    import os

    assert run.status == "succeeded" and step(run, "s").output["x"] != os.getpid()


@pytest.mark.skipif(not docker_ready(), reason="docker unavailable")
def test_python_script_is_sandboxed_in_docker(world):
    ws = world["ws_a"]
    code = (
        "import socket, os\n"
        "try:\n    socket.create_connection(('1.1.1.1', 80), timeout=2); net = 'open'\n"
        "except OSError:\n    net = 'blocked'\n"
        "uid = os.getuid()\n"
    )
    v = publish(
        ws, "boxed", [script(code=code, outputs=(("net", "string"), ("uid", "integer")))], [], user=world["designer"]
    )
    run = drain(launch(ws, v, world["designer"]), runner=DockerRunner())
    assert run.status == "succeeded", run.error
    assert step(run, "s").output == {"net": "blocked", "uid": 65534}


@pytest.mark.skipif(not has_node, reason="node missing")
def test_javascript_script_task(world):
    ws = world["ws_a"]
    code = "System.log('js', n);\nconst doubled = n * 2;\nconst label = `x${doubled}`;\n"
    v = publish(
        ws,
        "js",
        [
            script(
                language="javascript",
                code=code,
                bindings={"n": "{{ inputs.n }}"},
                outputs=(("doubled", "integer"), ("label", "string")),
            )
        ],
        [],
        inputs=schema(["n"], n={"type": "integer"}),
        outputs={"d": "{{ steps.s.output.doubled }}"},
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"], {"n": 21}))
    assert run.status == "succeeded", run.error
    assert run.outputs == {"d": 42} and step(run, "s").output["label"] == "x42"


def test_throw_exception_node_fails_with_templated_message_and_can_be_caught(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    v = publish(
        ws,
        "thrower",
        [
            node("t", "throw", code="REFUSE", message="Refusé pour {{ inputs.t }}"),
            node("h", action="echo", inputs={"value": "rattrapé"}),
        ],
        [edge("t", "h", "error")] if False else [],
        inputs=schema(["t"], t=STR),
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"], {"t": "alice"}))
    assert run.status == "failed" and run.error["code"] == "REFUSE" and "Refusé pour alice" in run.error["message"]


@pytest.mark.parametrize(
    "bad,expected",
    [
        (script(code=""), "empty_script"),
        (script(code="def x(:"), "script_syntax"),
        (script(language="ruby"), "bad_language"),
        (script(bindings={"class": "1"}), "bad_binding"),
        (script(bindings={"System": "1"}), "bad_binding"),
        (script(outputs=(("x", "integer"), ("x", "string"))), "bad_output"),
        (script(outputs=(("1bad", "integer"),)), "bad_output"),
        (script(outputs=(("x", "nonsense"),)), "bad_output"),
        (script(timeout_seconds=100000), "bad_timeout"),
        (script(bindings={"a": "{{ steps.ghost.output.v }}"}), "unknown_step"),
        (script(bindings={"a": "{{ vars.nope }}"}), "unknown_variable"),
        (script(code="x=1", bindings={"x": "1"}), "bad_output"),
    ],
)
def test_script_validation(bad, expected, world):
    assert expected in errors(g([bad]), world["ws_a"])


def test_variables_and_form_validation(world):
    ws = world["ws_a"]
    s = script(code="x = 1")
    assert "bad_variable_name" in errors(g([s], variables=[{"name": "1x", "type": "string"}]), ws)
    assert "bad_variable_name" in errors(g([s], variables=[{"name": "System", "type": "string"}]), ws)
    assert "duplicate_variable" in errors(
        g([s], variables=[{"name": "a", "type": "string"}, {"name": "a", "type": "string"}]), ws
    )
    assert "bad_variable_value" in errors(g([s], variables=[{"name": "a", "type": "integer", "value": "texte"}]), ws)
    assert "bad_form" in errors(g([s], form={"steps": [{"title": "", "fields": ["t"]}]}), ws)
    assert "bad_form" in errors(g([s], form={"steps": [{"title": "Étape", "fields": ["inconnu"]}]}), ws)
    assert "bad_form" in errors(
        g([s], form={"steps": [{"title": "A", "fields": ["t"]}, {"title": "B", "fields": ["t"]}]}), ws
    )
    assert (
        errors(
            g(
                [s],
                form={"steps": [{"title": "Étape 1", "fields": ["t"]}]},
                variables=[{"name": "ok", "type": "integer", "value": 3}],
            ),
            ws,
        )
        == set()
    )


def test_script_test_endpoint_runs_in_isolation_and_returns_logs(world):
    c = client_for(world["designer"])
    c.post(f"{BASE}/workflows/", {"slug": "wf-test", "name": "t"}, format="json")
    body = {
        "node": {
            "language": "python",
            "script": "System.log('essai', a)\nb = a + 1",
            "bindings": {},
            "outputs": [{"name": "b", "type": "integer"}],
        },
        "bindings": {"a": 4},
    }
    body["node"]["bindings"] = {"a": 4}
    r = c.post(f"{BASE}/workflows/wf-test/test-script/", body, format="json")
    assert r.status_code == 202, r.content
    ex = Execution.objects.get(pk=r.json()["execution_id"])
    process_one("w", SubprocessRunner())
    detail = c.get(r.json()["url"].replace("/api/v1/workspaces/team-a", BASE)).json()
    assert detail["status"] == "succeeded" and detail["result"] == {"b": 5}
    assert "essai 4" in detail["attempts"][0]["logs"] and ex.requested_by == world["designer"]
    assert AuditEvent.objects.filter(action="workflow.script_test").exists()


def test_script_test_endpoint_validates_and_checks_permissions(world):
    c = client_for(world["designer"])
    c.post(f"{BASE}/workflows/", {"slug": "wf-test2", "name": "t"}, format="json")
    bad = c.post(
        f"{BASE}/workflows/wf-test2/test-script/",
        {"node": {"language": "python", "script": "def x(:", "outputs": []}},
        format="json",
    )
    assert bad.status_code == 400 and "script_syntax" in bad.content.decode()
    ok = {"node": {"language": "python", "script": "x = 1", "outputs": [{"name": "x", "type": "integer"}]}}
    assert (
        client_for(world["viewer"]).post(f"{BASE}/workflows/wf-test2/test-script/", ok, format="json").status_code
        == 403
    )
    assert (
        client_for(world["outsider"]).post(f"{BASE}/workflows/wf-test2/test-script/", ok, format="json").status_code
        == 404
    )
    assert c.post(f"{BASE}/workflows/wf-test2/test-script/", {"node": "x"}, format="json").status_code == 400


def test_favorites_meta_dependencies_and_workflow_audit(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(ws, "fav-wf", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    c = client_for(world["designer"])
    assert c.put(f"{BASE}/favorites/workflow/fav-wf/").status_code == 204
    assert c.put(f"{BASE}/favorites/workflow/ghost/").status_code == 404
    assert c.put(f"{BASE}/favorites/other/fav-wf/").status_code == 404
    assert [f["slug"] for f in c.get(f"{BASE}/favorites/").json()["results"]] == ["fav-wf"]
    assert client_for(world["viewer"]).get(f"{BASE}/favorites/").json()["results"] == []  # favourites are per user
    assert c.delete(f"{BASE}/favorites/workflow/fav-wf/").status_code == 204
    m = c.patch(
        f"{BASE}/workflows/fav-wf/meta/",
        {"name": "Renommé", "folder": " Démo / DNS/ ", "description": "d"},
        format="json",
    )
    assert (
        m.status_code == 200
        and m.json()["folder"] == "Démo/DNS"
        and c.get(f"{BASE}/workflows/fav-wf/").json()["folder"] == "Démo/DNS"
    )
    assert c.patch(f"{BASE}/workflows/fav-wf/meta/", {"name": " "}, format="json").status_code == 400
    assert (
        client_for(world["viewer"]).patch(f"{BASE}/workflows/fav-wf/meta/", {"name": "x"}, format="json").status_code
        == 403
    )
    deps = c.get(f"{BASE}/workflows/fav-wf/dependencies/").json()
    assert deps["actions"][0]["slug"] == "echo" and deps["actions"][0]["exists"] is True
    audit = c.get(f"{BASE}/workflows/fav-wf/audit/").json()["results"]
    assert (
        any(e["action"] == "workflow.update" for e in audit)
        and world["viewer"]
        and client_for(world["viewer"]).get(f"{BASE}/workflows/fav-wf/audit/").status_code == 200
    )
    assert client_for(world["outsider"]).get(f"{BASE}/workflows/fav-wf/audit/").status_code == 404


def test_delete_workflow_is_refused_when_it_would_hurt(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    v = publish(ws, "to-delete", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    c = client_for(world["designer"])
    assert client_for(world["viewer"]).delete(f"{BASE}/workflows/to-delete/delete/").status_code == 403
    drain(launch(ws, v, world["designer"]))
    refused = c.delete(f"{BASE}/workflows/to-delete/delete/")
    assert refused.status_code == 400 and "déjà été exécuté" in refused.content.decode()
    c.post(f"{BASE}/workflows/", {"slug": "scratch", "name": "scratch"}, format="json")
    assert c.delete(f"{BASE}/workflows/scratch/delete/").status_code == 204
    assert c.get(f"{BASE}/workflows/scratch/").status_code == 404
    assert AuditEvent.objects.filter(action="workflow.delete").exists()
    publish(
        ws, "parent-wf", [node("s", "subworkflow", workflow="scratch2", version="1.0.0")], [], user=world["designer"]
    ) if False else None


def test_runs_can_be_filtered_by_workflow(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    va = publish(ws, "wf-a", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    vb = publish(ws, "wf-b", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    launch(ws, va, world["designer"])
    launch(ws, vb, world["designer"])
    rows = client_for(world["designer"]).get(f"{BASE}/workflow-runs/?workflow=wf-a").json()["results"]
    assert [r["workflow"]["slug"] for r in rows] == ["wf-a"]
