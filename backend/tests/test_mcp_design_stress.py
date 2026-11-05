"""ADR 0025 — la plate-forme tient-elle un workflow de plus de 100 étapes construit par un assistant ? Mesures et limites, pas seulement « ça passe »."""

import time
from types import SimpleNamespace

import pytest

from tests.conftest import client_for
from tests.wf_helpers import STR, drain, launch, make_action, schema
from workflows.models import WorkflowVersion
from workflows.validation import MAX_NODES

BASE = "/api/v1/workspaces/team-a"
URL = f"{BASE}/mcp/design/"


def call(c, tool, args=None, ok=True):
    r = c.post(
        URL,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}},
        format="json",
    )
    res = r.json()["result"]
    if ok:
        assert not res["isError"], f"{tool}: {res['content'][0]['text'][:300]}"
    return res


def big_graph(blocks: int):
    """`blocks` blocs en chaîne : script → décision → (deux scripts) → jonction → action. 6 étapes par bloc."""
    nodes, edges, prev = [], [], None
    for i in range(blocks):
        p = f"b{i:02d}"
        nodes += [
            {
                "id": f"{p}_prep",
                "type": "script",
                "name": f"Préparer {i}",
                "language": "python",
                "script": f"v{i} = int(x) + {i}\n",
                "bindings": {"x": "{{ inputs.n }}"},
                "outputs": [{"name": f"v{i}", "type": "integer"}],
            },
            {"id": f"{p}_test", "type": "condition", "name": f"Pair ? {i}", "expr": f"inputs.n % 2 == {i % 2}"},
            {
                "id": f"{p}_oui",
                "type": "script",
                "name": f"Oui {i}",
                "language": "python",
                "script": "r = 1\n",
                "bindings": {},
                "outputs": [{"name": "r", "type": "integer"}],
            },
            {
                "id": f"{p}_non",
                "type": "script",
                "name": f"Non {i}",
                "language": "python",
                "script": "r = 2\n",
                "bindings": {},
                "outputs": [{"name": "r", "type": "integer"}],
            },
            {"id": f"{p}_join", "type": "join", "name": f"Reprise {i}"},
            {
                "id": f"{p}_act",
                "type": "action",
                "name": f"Écho {i}",
                "action": "echo",
                "version": "1.0.0",
                "inputs": {"value": "{{ inputs.t }}"},
            },
        ]
        edges += [
            {"from": f"{p}_prep", "to": f"{p}_test"},
            {"from": f"{p}_test", "to": f"{p}_oui", "label": "true"},
            {"from": f"{p}_test", "to": f"{p}_non", "label": "false"},
            {"from": f"{p}_oui", "to": f"{p}_join"},
            {"from": f"{p}_non", "to": f"{p}_join"},
            {"from": f"{p}_join", "to": f"{p}_act"},
        ]
        if prev:
            edges.append({"from": prev, "to": f"{p}_prep"})
        prev = f"{p}_act"
    inputs = schema(["n", "t"], n={"type": "integer"}, t=STR)
    return {
        "inputs": inputs,
        "nodes": nodes,
        "edges": edges,
        "outputs": {"dernier": "{{ steps." + prev + ".output.value }}"},
    }


@pytest.fixture
def assistant(world):
    make_action(world["ws_a"], "echo")
    user = world["designer"]
    user._token = SimpleNamespace(
        scopes=["view", "edit"], workspace_id=world["ws_a"].id, workspace=world["ws_a"], environments=[]
    )
    yield client_for(user)
    del user._token


def timed(label, fn, timings):
    t = time.perf_counter()
    out = fn()
    timings[label] = time.perf_counter() - t
    return out


def test_a_workflow_of_over_100_steps_is_created_validated_published_and_run(world, assistant):
    blocks, timings = 21, {}  # 21 × 6 = 126 étapes
    g = big_graph(blocks)
    assert len(g["nodes"]) == 126 <= MAX_NODES
    out = timed(
        "create_workflow (126 étapes)",
        lambda: call(assistant, "create_workflow", {"slug": "gros", "name": "Gros", "graph": g}),
        timings,
    )
    assert out["structuredContent"]["issues"] == [], out["structuredContent"]["issues"][:3]
    timed("validate_workflow", lambda: call(assistant, "validate_workflow", {"slug": "gros"}), timings)
    timed("get_workflow", lambda: call(assistant, "get_workflow", {"slug": "gros"}), timings)
    human = client_for(type(world["designer"]).objects.get(pk=world["designer"].pk))
    p = timed("publication", lambda: human.post(f"{BASE}/workflows/gros/publish/", {}, format="json"), timings)
    assert p.status_code == 201, p.content
    v = WorkflowVersion.objects.get(workflow__slug="gros")
    run = timed(
        "exécution complète (126 étapes)",
        lambda: drain(
            launch(world["ws_a"], v, type(world["designer"]).objects.get(pk=world["designer"].pk), {"n": 4, "t": "x"}),
            max_rounds=2000,
        ),
        timings,
    )
    print("\n" + "\n".join(f"  {k:36s} {v:6.2f} s" for k, v in timings.items()))
    assert run.status == "succeeded", run.error
    assert run.outputs == {"dernier": "x"}
    assert run.steps.filter(status="succeeded").count() >= blocks * 4  # une seule branche par bloc


def test_the_ceiling_is_clear_and_never_silent(world, assistant):
    """200 étapes passent ; 201 reviennent avec une erreur lisible et ne se publient pas ; un graphe de plus de 512 Ko est refusé net."""
    ok = call(
        assistant, "create_workflow", {"slug": "plafond", "name": "Plafond", "graph": big_graph(33)}
    )  # 198 étapes
    assert ok["structuredContent"]["issues"] == []
    g = big_graph(33)
    g["nodes"] += [
        {"id": "x1", "type": "join", "name": "x"},
        {"id": "x2", "type": "join", "name": "y"},
        {"id": "x3", "type": "join", "name": "z"},
    ]  # 201
    over = call(assistant, "create_workflow", {"slug": "trop", "name": "Trop", "graph": g})
    assert [i["code"] for i in over["structuredContent"]["issues"]] == ["too_many_nodes"]
    human = client_for(type(world["designer"]).objects.get(pk=world["designer"].pk))
    assert human.post(f"{BASE}/workflows/trop/publish/", {}, format="json").status_code == 400
    huge = {
        "inputs": schema(),
        "nodes": [
            {
                "id": "s",
                "type": "script",
                "name": "S",
                "language": "python",
                "script": "x = 1\n" * 100_000,
                "bindings": {},
                "outputs": [],
            }
        ],
        "edges": [],
    }
    res = call(assistant, "create_workflow", {"slug": "enorme", "name": "Énorme", "graph": huge}, ok=False)
    assert res["isError"] and "volumineux" in res["content"][0]["text"]
