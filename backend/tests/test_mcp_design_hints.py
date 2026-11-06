"""ADR 0025 — l'assistant décide « action réutilisable ou script ? » avec des faits : usage réel d'une action, scripts dupliqués, taille."""

from types import SimpleNamespace

import pytest

from tests.conftest import client_for
from tests.test_mcp_design_stress import big_graph
from tests.wf_helpers import ACTIONS, make_action, node, publish
from workflows.mcp_design_hints import BIG_GRAPH, DUPLICATE_AT, LONG_SCRIPT_LINES, design_hints
from workflows.models import Workflow

URL = "/api/v1/workspaces/team-a/mcp/design/"


def call(c, tool, args=None):
    r = c.post(
        URL,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}},
        format="json",
    )
    res = r.json()["result"]
    assert not res["isError"], res["content"][0]["text"][:200]
    return res["structuredContent"]


def script(nid, code):
    return {
        "id": nid,
        "type": "script",
        "name": nid,
        "language": "python",
        "script": code,
        "bindings": {},
        "outputs": [],
    }


def codes(graph):
    return [h["code"] for h in design_hints(graph)]


def test_the_same_script_three_times_suggests_an_action_but_two_do_not():
    same = [script(f"s{i}", "x = 1\ny = x + 1\n") for i in range(DUPLICATE_AT)]
    hints = design_hints({"nodes": same})
    assert [h["code"] for h in hints] == ["duplicate_script"] and hints[0]["nodes"] == ["s0", "s1", "s2"]
    assert codes({"nodes": same[: DUPLICATE_AT - 1]}) == []  # deux répétitions : pas encore
    assert (
        codes({"nodes": [script("a", "x = 1"), script("b", "x = 2"), script("c", "x = 3")]}) == []
    )  # trois scripts DIFFÉRENTS
    assert codes({"nodes": [script("a", "x  =  1"), script("b", "x = 1\n"), script("c", " x = 1")]}) == [
        "duplicate_script"
    ]  # espaces ignorés


def test_a_long_script_and_a_big_workflow_are_flagged():
    assert codes({"nodes": [script("l", "x = 1\n" * LONG_SCRIPT_LINES)]}) == ["long_script"]
    assert codes({"nodes": [script("l", "x = 1\n" * (LONG_SCRIPT_LINES - 1))]}) == []
    big = {"nodes": [{"id": f"j{i}", "type": "join", "name": "j"} for i in range(BIG_GRAPH)]}
    assert codes(big) == ["big_workflow"] and codes({"nodes": big["nodes"][:-1]}) == []


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


@pytest.fixture
def assistant(world):
    user = world["designer"]
    user._token = SimpleNamespace(
        scopes=["view", "edit"], workspace_id=world["ws_a"].id, workspace=world["ws_a"], environments=[]
    )
    yield client_for(user)
    del user._token


def test_search_actions_tells_how_much_each_action_is_really_used(ws, world, assistant):
    publish(ws, "a", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    publish(
        ws,
        "b",
        [node("e", action="echo", inputs={"value": "x"}), node("f", action="echo", inputs={"value": "y"})],
        [],
        user=world["designer"],
    )
    Workflow.objects.create(
        workspace=ws,
        slug="brouillon",
        name="B",
        draft_graph={"nodes": [node("e", action="echo", inputs={"value": "z"})], "edges": []},
    )
    rows = {r["slug"]: r for r in call(assistant, "search_actions")["results"]}
    assert rows["echo"]["used_by"] == {
        "workflows": 3,
        "steps": 4,
    }  # deux publiés (3 étapes en brouillon + version) et un brouillon seul
    assert rows["add"]["used_by"] == {
        "workflows": 0,
        "steps": 0,
    }  # jamais utilisée : ne pas en créer une jumelle, ni l'ignorer
    assert call(assistant, "get_action", {"slug": "echo"})["used_by"]["workflows"] == 3


def test_workflow_tools_return_hints_next_to_issues(ws, world, assistant):
    g = big_graph(18)  # 108 étapes dont 36 scripts « r = 1 » / « r = 2 » identiques
    out = call(assistant, "create_workflow", {"slug": "gros", "name": "Gros", "graph": g})
    assert out["issues"] == [] and {h["code"] for h in out["hints"]} == {"duplicate_script", "big_workflow"}
    dup = next(h for h in out["hints"] if h["code"] == "duplicate_script")
    assert len(dup["nodes"]) >= DUPLICATE_AT
    assert {h["code"] for h in call(assistant, "validate_workflow", {"slug": "gros"})["hints"]} == {
        "duplicate_script",
        "big_workflow",
    }
    assert (
        call(assistant, "validate_workflow", {"graph": {"inputs": {"type": "object"}, "nodes": [], "edges": []}})[
            "hints"
        ]
        == []
    )


def test_the_guide_has_a_decision_section_that_names_the_signals():
    from workflows.mcp_design_guide import guide

    text = guide("decision")["guide"]
    assert (
        text.startswith("ÉLÉMENT NATIF D'ABORD") and "ACTION OU SCRIPT ?" in text
    )  # le réseau se règle avant la question
    assert "used_by" in text and "duplicate_script" in text and "network_in_script" in text and "pourquoi" in text
