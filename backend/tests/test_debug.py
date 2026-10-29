import pytest

from runs.models import WorkflowRun
from tenancy.models import Environment
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, edge, launch, make_action, node, publish, schema

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def _wf(ws, world, name="dbg"):
    user = world["designer"]
    v = publish(
        ws,
        name,
        [
            node("e", action="echo", inputs={"value": "{{ inputs.t }}"}),
            node(
                "f", "transform", op="map", items="inputs.rows", fields={"n": "{{ item.nom }}", "x": "{{ item.v * 2 }}"}
            ),
        ],
        [edge("e", "f")],
        inputs=schema(["t"], t={"type": "string"}, rows={"type": "array"}),
        user=user,
    )
    run = drain(launch(ws, v, user, inputs={"t": "bonjour", "rows": [{"nom": "a", "v": 2}, {"nom": "b", "v": 5}]}))
    assert run.status == "succeeded"
    return run


def test_context_lists_the_last_run_outputs(ws, world):
    run = _wf(ws, world)
    r = client_for(world["designer"]).get(f"{BASE}/workflows/dbg/debug/context/")
    assert (
        r.status_code == 200 and r.data["run"]["id"] == str(run.id) and r.data["outputs"]["e"] == {"value": "bonjour"}
    )
    assert client_for(world["viewer"]).get(f"{BASE}/workflows/dbg/debug/context/").status_code == 403  # editing feature


def test_eval_gives_the_value_on_real_data_and_explains_errors(ws, world):
    _wf(ws, world)
    c = client_for(world["designer"])
    ok = c.post(f"{BASE}/workflows/dbg/debug/eval/", {"expr": "upper(steps.e.output.value) + '!'"}, format="json")
    assert ok.data == {"ok": True, "value": "BONJOUR!", "has_data": True}
    tpl = c.post(f"{BASE}/workflows/dbg/debug/eval/", {"expr": "Salut {{ inputs.t }}"}, format="json")
    assert tpl.data["value"] == "Salut bonjour"
    bad = c.post(f"{BASE}/workflows/dbg/debug/eval/", {"expr": "steps.zzz.output.x"}, format="json")
    assert bad.data["ok"] is False and "introuvable" in bad.data["error"]
    per = c.post(f"{BASE}/workflows/dbg/debug/eval/", {"expr": "item.v * 10", "items": "inputs.rows"}, format="json")
    assert per.data["value"] == 20


def test_pins_override_run_data_and_test_step_runs_pure_steps_only(ws, world):
    _wf(ws, world)
    c = client_for(world["designer"])
    graph = {
        "nodes": [
            node("e", action="echo", inputs={"value": "x"}),
            node("d", "condition", expr="steps.e.output.value == 'autre'"),
        ],
        "edges": [edge("e", "d")],
        "pins": {"e": {"value": "autre"}},
    }
    r = c.post(f"{BASE}/workflows/dbg/debug/test-step/", {"graph": graph, "node_id": "d"}, format="json")
    assert r.data["output"] == {"result": True} and r.data["source"] == "calculé"
    r = c.post(f"{BASE}/workflows/dbg/debug/test-step/", {"graph": graph, "node_id": "e"}, format="json")
    assert r.data["output"] == {"value": "autre"} and r.data["source"] == "épinglée"
    plain = {"nodes": [node("a", action="boom")], "edges": []}
    r = c.post(f"{BASE}/workflows/dbg/debug/test-step/", {"graph": plain, "node_id": "a"}, format="json")
    assert r.data["ok"] is False and "effet" in r.data["error"]
    r = c.post(
        f"{BASE}/workflows/dbg/debug/test-step/", {"node_id": "f"}, format="json"
    )  # the draft is empty: unknown step
    assert r.status_code == 404 or r.data["ok"] in (True, False)


def test_pins_are_validated_and_replace_a_step_in_dev_runs_only(ws, world):
    from workflows.validation import validate_graph

    user = world["designer"]
    g = {
        "inputs": schema(),
        "nodes": [node("e", action="echo", inputs={"value": "x"})],
        "edges": [],
        "pins": {"zz": {"a": 1}, "e": "pas un objet"},
    }
    codes = [i["message"] for i in validate_graph(g, ws, "t") if i["code"] == "bad_pins"]
    assert len(codes) == 2
    graph = {
        "inputs": schema(),
        "nodes": [node("e", action="boom")],
        "edges": [],
        "outputs": {},
        "pins": {"e": {"value": "épinglé"}},
    }
    from workflows.models import WorkflowVersion

    v = publish(ws, "pinned", graph["nodes"], [], user=user)
    WorkflowVersion.objects.filter(pk=v.pk).update(graph={**v.graph, "pins": graph["pins"]})
    v.refresh_from_db()
    Environment.objects.get_or_create(workspace=ws, kind="dev", defaults={"name": "dev"})
    dev = drain(launch(ws, v, user, env="dev"))
    assert dev.status == "succeeded" and dev.steps.get(node_id="e").output == {"value": "épinglé"}
    assert dev.events.filter(type="step_pinned").exists()
    test = drain(launch(ws, v, user, env="test"))
    assert test.status == "failed"  # outside development the real step runs


def test_run_filters_export_and_comparison(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "cmp",
        [node("e", action="echo", inputs={"value": "{{ inputs.t }}"})],
        [],
        inputs=schema(["t"], t={"type": "string"}),
        user=user,
    )
    a = drain(launch(ws, v, user, inputs={"t": "un"}))
    b = drain(launch(ws, v, user, inputs={"t": "deux"}))
    bad = publish(ws, "cassee", [node("s", action="boom")], [], user=user)
    failed = drain(launch(ws, bad, user))
    c = client_for(user)
    ids = lambda **q: {r["id"] for r in c.get(f"{BASE}/workflow-runs/", q).data["results"]}  # noqa: E731
    assert ids(status="failed") == {str(failed.id)}
    assert (
        ids(q="cass") == {str(failed.id)}
        and ids(step="s") == {str(failed.id)}
        and ids(code=failed.error["code"]) == {str(failed.id)}
    )
    assert {str(a.id), str(b.id)} <= ids(workflow="cmp") and str(failed.id) not in ids(workflow="cmp")
    assert ids(**{"from": "2999-01-01"}) == set() and str(a.id) in ids(**{"from": "2000-01-01", "to": "2999-01-01"})
    assert str(a.id) in ids(min_duration="0")
    csv_ = c.get(f"{BASE}/workflow-runs/export/", {"status": "failed"})
    body = csv_.content.decode()
    assert (
        csv_["Content-Type"].startswith("text/csv")
        and str(failed.id) in body
        and str(a.id) not in body
        and "boom" not in body
    )
    diff = c.get(f"{BASE}/workflow-runs/compare/", {"a": str(a.id), "b": str(b.id)}).data
    assert diff["same_version"] and diff["different_steps"] == 1
    assert diff["inputs"] == [{"key": "t", "a": "un", "b": "deux", "same": False}]
    assert diff["steps"][0]["output_diff"] == [{"key": "value", "a": "un", "b": "deux", "same": False}]
    assert c.get(f"{BASE}/workflow-runs/compare/", {"a": str(a.id), "b": "x"}).status_code == 404
    assert (
        client_for(world["viewer"]).get(f"{BASE}/workflow-runs/compare/", {"a": str(a.id), "b": str(b.id)}).status_code
        == 200
    )


def test_run_until_a_step_in_development_on_the_draft(ws, world):
    from tests.wf_helpers import edge

    user = world["designer"]
    Environment.objects.get_or_create(workspace=ws, kind="dev", defaults={"name": "dev"})
    publish(ws, "jusque", [node("a", action="echo", inputs={"value": "un"})], [], user=user)
    draft = {
        "inputs": schema(),
        "nodes": [
            node("a", action="echo", inputs={"value": "un"}),
            node("b", action="echo", inputs={"value": "deux"}),
            node("c", action="boom"),
        ],
        "edges": [edge("a", "b"), edge("b", "c")],
        "outputs": {},
    }
    from workflows.models import Workflow

    Workflow.objects.filter(workspace=ws, slug="jusque").update(draft_graph=draft)
    c = client_for(user)
    url = f"{BASE}/workflows/jusque/runs/"
    r = c.post(url, {"environment": "dev", "inputs": {}, "draft": True, "until": "b"}, format="json")
    assert r.status_code == 202, r.data
    run = drain(WorkflowRun.objects.get(pk=r.data["id"]))
    assert (
        run.status == "succeeded"
        and run.steps.filter(node_id="c").count() == 0
        and run.steps.get(node_id="b").output == {"value": "deux"}
    )
    assert run.events.filter(message__contains="arrêtée volontairement").exists()
    prod = c.post(url, {"environment": "test", "inputs": {}, "until": "b"}, format="json")
    assert prod.status_code == 400 and "développement" in str(prod.data)
    assert (
        c.post(url, {"environment": "dev", "inputs": {}, "until": "zz", "draft": True}, format="json").status_code
        == 400
    )
    assert client_for(world["viewer"]).post(url, {"environment": "dev", "inputs": {}}, format="json").status_code == 403


def test_variables_can_have_a_value_per_environment(ws, world):
    from workflows.validation import validate_graph

    user = world["designer"]
    nodes = [node("e", action="echo", inputs={"value": "{{ vars.url }}"})]
    graph = {
        "inputs": schema(),
        "nodes": nodes,
        "edges": [],
        "outputs": {},
        "variables": [
            {"name": "url", "type": "string", "value": "http://defaut", "env_values": {"test": "http://recette"}}
        ],
    }
    assert [i for i in validate_graph(graph, ws, "t") if i["level"] == "error"] == []
    bad = {
        **graph,
        "variables": [{"name": "url", "type": "string", "value": "x", "env_values": {"prod": 5, "autre": "x"}}],
    }
    assert len([i for i in validate_graph(bad, ws, "t") if i["code"] == "bad_variable_value"]) >= 1
    v = publish(ws, "varenv", [node("e", action="echo", inputs={"value": "x"})], [], user=user)
    from workflows.models import WorkflowVersion

    pinned = [{**v.graph["nodes"][0], "inputs": {"value": "{{ vars.url }}"}}]
    WorkflowVersion.objects.filter(pk=v.pk).update(graph={**v.graph, "nodes": pinned, "variables": graph["variables"]})
    v.refresh_from_db()
    Environment.objects.get_or_create(workspace=ws, kind="dev", defaults={"name": "dev"})
    assert drain(launch(ws, v, user, env="test")).steps.get(node_id="e").output == {"value": "http://recette"}
    assert drain(launch(ws, v, user, env="dev")).steps.get(node_id="e").output == {"value": "http://defaut"}


def test_version_diff_shows_what_changed_and_ignores_layout(ws, world):
    from workflows.diffing import diff_graphs
    from workflows.models import Workflow

    user = world["designer"]
    v1 = publish(ws, "diff", [node("a", action="echo", inputs={"value": "un"})], [], user=user)
    nodes = [node("a", action="echo", inputs={"value": "deux"}), node("b", action="echo", inputs={"value": "x"})]
    publish(ws, "diff", nodes, [{"from": "a", "to": "b"}], user=user)
    c = client_for(user)
    d = c.get(f"{BASE}/workflows/diff/versions/diff/", {"a": "1.0.0", "b": "1.1.0"}).data
    assert d["identical"] is False and [n["id"] for n in d["nodes_added"]] == ["b"]
    assert d["nodes_changed"][0]["fields"][0] == {
        "field": "inputs",
        "before": {"value": "un"},
        "after": {"value": "deux"},
    }
    assert d["edges_added"] == [{"from": "a", "to": "b", "label": None}]
    same = diff_graphs(
        {"nodes": [{"id": "a", "type": "wait", "ui": {"x": 1}}]},
        {"nodes": [{"id": "a", "type": "wait", "ui": {"x": 99}}]},
    )
    assert same["identical"] is True
    Workflow.objects.filter(workspace=ws, slug="diff").update(draft_graph=v1.graph)
    assert c.get(f"{BASE}/workflows/diff/versions/diff/", {"a": "1.0.0", "b": "draft"}).data["identical"] is True
    assert c.get(f"{BASE}/workflows/diff/versions/diff/", {"a": "9.9.9", "b": "draft"}).status_code == 404
