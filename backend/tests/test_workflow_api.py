import pytest

from catalog.models import ActionVersion
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, STR, edge, make_action, node, schema
from workflows.models import Workflow, WorkflowVersion
from workflows.validation import validate


@pytest.fixture
def ws(world):
    w = world["ws_a"]
    for slug in ACTIONS:
        if not ActionVersion.objects.filter(action__workspace=w, action__slug=slug).exists():
            make_action(w, slug)
    return w


def codes(graph, ws, slug=None):
    return {i["code"] for i in validate(graph, ws, slug) if i["level"] == "error"}


def g(nodes, edges=(), inputs=None, outputs=None):
    return {"inputs": inputs or schema(), "nodes": nodes, "edges": list(edges), "outputs": outputs or {}}


def test_valid_graph_has_no_error(ws):
    graph = g(
        [
            node("a", action="echo", inputs={"value": "x"}),
            node("b", action="echo", inputs={"value": "{{ steps.a.output.value }}"}),
        ],
        [edge("a", "b")],
    )
    assert codes(graph, ws) == set()


@pytest.mark.parametrize(
    "graph,expected",
    [
        (
            g(
                [node("a", action="echo", inputs={"value": "x"}), node("b", action="echo", inputs={"value": "x"})],
                [edge("a", "b"), edge("b", "a")],
            ),
            "cycle",
        ),
        (g([node("a", action="echo", inputs={"value": "x"})], [edge("a", "zzz")]), "dangling_edge"),
        (g([node("a", action="nope")]), "unknown_action"),
        (g([node("a", action="echo", version="9.9.9", inputs={"value": "x"})]), "version_unavailable"),
        (g([node("a", action="echo", inputs={})]), "missing_param"),
        (g([node("a", action="echo", inputs={"value": "x", "extra": 1})]), "unknown_param"),
        (g([node("a", action="add", inputs={"a": "texte", "b": 1})]), "bad_param_value"),
        (g([node("a", action="echo", inputs={"value": "{{ steps.ghost.output.v }}"})]), "unknown_step"),
        (g([node("a", action="echo", inputs={"value": "{{ inputs.nope }}"})]), "unknown_input"),
        (
            g(
                [
                    node("a", action="echo", inputs={"value": "x"}),
                    node("b", action="echo", inputs={"value": "{{ steps.c.output.value }}"}),
                    node("c", action="echo", inputs={"value": "x"}),
                ],
                [edge("a", "b")],
            ),
            "not_upstream",
        ),
        (
            g(
                [
                    node("a", action="echo", inputs={"value": "x"}),
                    node("b", action="echo", inputs={"value": "{{ steps.a.output.zzz }}"}),
                ],
                [edge("a", "b")],
            ),
            "unknown_output",
        ),
        (
            g(
                [
                    node("a", action="echo", inputs={"value": "x"}),
                    node("b", action="add", inputs={"a": "{{ steps.a.output.value }}", "b": 1}),
                ],
                [edge("a", "b")],
            ),
            "type_mismatch",
        ),
        (
            g(
                [
                    node("a", action="echo", inputs={"value": "x"}),
                    node("b", action="echo", inputs={"value": "x"}),
                    node("c", action="echo", inputs={"value": "x"}),
                ],
                [edge("a", "c"), edge("b", "c")],
            ),
            "implicit_join",
        ),
        (g([node("c", "condition", expr="1 ==")]), "bad_expression"),
        (g([node("c", "condition", expr="__import__('os')")]), "bad_expression"),
        (
            g(
                [
                    node("a", action="echo", inputs={"value": "x"}),
                    node("c", "condition", expr="true"),
                    node("b", action="echo", inputs={"value": "x"}),
                ],
                [edge("a", "c"), edge("c", "b", "maybe")],
            ),
            "bad_branches",
        ),
        (
            g(
                [node("l", "foreach", workflow="self", items="inputs.xs", max_iterations=500)],
                inputs=schema(xs={"type": "array"}),
            ),
            "unknown_workflow",
        ),
        (g([node("w", "wait", seconds=0)]), "bad_wait"),
        (g([node("ap", "approval", title="")]), "missing_title"),
    ],
)
def test_validation_catches(graph, expected, ws):
    assert expected in codes(graph, ws)


def test_unbounded_loop_and_recursion(ws, world):
    child = Workflow.objects.create(workspace=ws, slug="kid", name="kid")
    WorkflowVersion.objects.create(
        workflow=child,
        version="1.0.0",
        graph=g([node("a", action="echo", inputs={"value": "x"})]),
        input_schema=schema(),
    )
    ok_loop = g(
        [node("l", "foreach", workflow="kid", version="1.0.0", items="inputs.xs", max_iterations=5, item_input="item")],
        inputs=schema(xs={"type": "array"}),
    )
    assert "unbounded_loop" not in codes(ok_loop, ws)
    for bad in (None, 0, 101):
        graph = g(
            [node("l", "foreach", workflow="kid", version="1.0.0", items="inputs.xs", max_iterations=bad)],
            inputs=schema(xs={"type": "array"}),
        )
        assert "unbounded_loop" in codes(graph, ws)
    own = Workflow.objects.create(workspace=ws, slug="me", name="me")
    WorkflowVersion.objects.create(
        workflow=own, version="1.0.0", graph=g([node("a", action="echo", inputs={"value": "x"})]), input_schema=schema()
    )
    assert "recursion" in codes(g([node("s", "subworkflow", workflow="me", version="1.0.0")]), ws, "me")


def test_cross_workspace_action_is_not_visible(ws, world):
    make_action(world["ws_b"], "echo")
    other = Workflow  # noqa: F841
    # an action that only exists in team-b must be unknown from team-a
    from catalog.models import Action

    Action.objects.create(workspace=world["ws_b"], slug="secret-b", name="b")
    assert "unknown_action" in codes(g([node("a", action="secret-b")]), ws)


def test_draft_optimistic_locking_and_validation_feedback(ws, world):
    c = client_for(world["designer"])
    base = "/api/v1/workspaces/team-a/workflows/"
    r = c.post(base, {"slug": "mine", "name": "Mon workflow"}, format="json")
    assert r.status_code == 201 and r.json()["draft_revision"] == 1
    graph = g([node("a", action="echo", inputs={})])
    saved = c.put(base + "mine/draft/", {"graph": graph, "revision": 1}, format="json")
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    assert any(i["code"] == "missing_param" for i in saved.json()["issues"])
    stale = c.put(base + "mine/draft/", {"graph": graph, "revision": 1}, format="json")
    assert stale.status_code == 409 and stale.json()["error"]["details"]["current_revision"] == 2
    assert c.post(base, {"slug": "mine", "name": "dup"}, format="json").status_code == 400


def test_publish_requires_valid_graph_and_creates_immutable_pinned_version(ws, world):
    c = client_for(world["designer"])
    base = "/api/v1/workspaces/team-a/workflows/"
    c.post(base, {"slug": "pub", "name": "Pub"}, format="json")
    assert c.post(base + "pub/publish/", {}, format="json").status_code == 400  # empty
    bad = g([node("a", action="echo", inputs={})])
    c.put(base + "pub/draft/", {"graph": bad, "revision": 1}, format="json")
    r = c.post(base + "pub/publish/", {}, format="json")
    assert r.status_code == 400 and r.json()["error"]["details"]["issues"][0]["code"] == "missing_param"
    good = g([node("a", action="echo", inputs={"value": "x"})])
    c.put(base + "pub/draft/", {"graph": good, "revision": 2}, format="json")
    r = c.post(base + "pub/publish/", {"changelog": "première"}, format="json")
    assert r.status_code == 201 and r.json()["version"] == "1.0.0"
    v = WorkflowVersion.objects.get(workflow__slug="pub")
    assert v.graph["nodes"][0]["version"] == "1.0.0"  # exact action version pinned
    with pytest.raises(PermissionError):
        v.graph = {}
        v.save()
    c.put(base + "pub/draft/", {"graph": good, "revision": 3}, format="json")
    assert c.post(base + "pub/publish/", {"bump": "major"}, format="json").json()["version"] == "2.0.0"


def test_permissions_on_workflow_authoring(ws, world):
    base = "/api/v1/workspaces/team-a/workflows/"
    assert client_for(world["viewer"]).post(base, {"slug": "x", "name": "x"}, format="json").status_code == 403
    assert client_for(world["viewer"]).get(base).status_code == 200
    assert client_for(world["outsider"]).get(base).status_code == 404
    client_for(world["designer"]).post(base, {"slug": "p", "name": "p"}, format="json")
    assert (
        client_for(world["outsider"]).put(base + "p/draft/", {"graph": {}, "revision": 1}, format="json").status_code
        == 404
    )
    assert (
        client_for(world["viewer"]).put(base + "p/draft/", {"graph": g([]), "revision": 1}, format="json").status_code
        == 403
    )


def test_str_constant_used():
    assert STR == {"type": "string"}


BASE = "/api/v1/workspaces/team-a"


def test_generated_call_contract_and_event_catch_up(ws, world):
    from tests.wf_helpers import drain, publish

    user = world["designer"]
    publish(
        ws,
        "contract-wf",
        [node("a", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v={"type": "string", "title": "V", "default": "salut"}),
        user=user,
    )
    c = client_for(user)
    k = c.get(f"{BASE}/workflows/contract-wf/contract/").json()
    assert k["endpoint"] == f"{BASE}/workflows/contract-wf/runs/" and k["input_schema"]["required"] == ["v"]
    assert k["example_request"] == {"environment": "test", "inputs": {"v": "salut"}}
    assert "Idempotency-Key" in k["curl"] and "202" in k["notes"][0]
    # the generated example is a valid call
    r = c.post(k["endpoint"], k["example_request"], format="json")
    assert r.status_code == 202
    rid = r.json()["id"]
    first = c.get(f"{BASE}/workflow-runs/{rid}/events/").json()
    assert [e["seq"] for e in first["results"]] == list(range(1, len(first["results"]) + 1)) and first["results"]
    drain(rid)
    later = c.get(f"{BASE}/workflow-runs/{rid}/events/?after={first['last_seq']}").json()
    assert (
        later["results"]
        and all(e["seq"] > first["last_seq"] for e in later["results"])
        and later["status"] == "succeeded"
    )
    assert c.get(f"{BASE}/workflow-runs/{rid}/events/?after=x").status_code == 400
    assert client_for(world["outsider"]).get(f"{BASE}/workflow-runs/{rid}/events/").status_code == 404
    assert client_for(world["outsider"]).get(f"{BASE}/workflows/contract-wf/contract/").status_code == 404


def test_validation_messages_never_echo_the_offending_value(ws, world):
    from tests.wf_helpers import drain, launch, publish

    user = world["designer"]
    secret_like = "TOP-SECRET-" + "x" * 12000
    v = publish(
        ws,
        "noecho",
        [node("a", action="echo", inputs={"value": "{{ inputs.t }}"})],
        [],
        inputs=schema(["t"], t=STR),
        user=user,
    )
    from catalog.models import ActionVersion

    # tighten the contract of the pinned action version through a new action version is not possible (immutable): use a length-limited action
    from tests.wf_helpers import make_action

    make_action(ws, "echo", version="1.1.0", input_schema=schema(["value"], value={"type": "string", "maxLength": 50}))
    v2 = publish(
        ws,
        "noecho2",
        [node("a", action="echo", version="1.1.0", inputs={"value": "{{ inputs.t }}"})],
        [],
        inputs=schema(["t"], t=STR),
        user=user,
    )
    run = drain(launch(ws, v2, user, {"t": secret_like}))
    assert run.status == "failed" and run.error["code"] == "INPUT_INVALID"
    assert "TOP-SECRET" not in run.error["message"] and "trop long (maximum 50 caractères)" in run.error["message"]
    api = client_for(user).post(
        f"{BASE}/executions/",
        {"action": "echo", "environment": "test", "inputs": {"value": secret_like}},
        format="json",
    )
    assert api.status_code == 400 and "TOP-SECRET" not in api.content.decode()
    assert ActionVersion and v
