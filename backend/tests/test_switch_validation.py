"""The multi-way choice (switch): its own rules in the validator, and the same safety analysis as a decision."""

from tests.wf_helpers import edge, node
from workflows.validation import validate_graph


def graph(**over):
    cases = [{"id": "prod", "expr": "inputs.env == 'prod'"}, {"id": "dev", "expr": "inputs.env == 'dev'"}]
    nodes = [
        node("s", "switch", cases=cases),
        node("a", "wait", seconds=1),
        node("b", "wait", seconds=1),
        node("c", "wait", seconds=1),
        node("j", "join"),
    ]
    edges = [
        edge("s", "a", "prod"),
        edge("s", "b", "dev"),
        edge("s", "c", "default"),
        edge("a", "j"),
        edge("b", "j"),
        edge("c", "j"),
    ]
    g = {"inputs": {"type": "object", "properties": {"env": {"type": "string"}}}, "nodes": nodes, "edges": edges}
    return {**g, **over}


def codes(g, ws):
    return {(i["code"], i["level"]) for i in validate_graph(g, ws, "x")}


def test_a_valid_switch_has_no_error(world):
    ws = world["ws_a"]
    assert not [i for i in validate_graph(graph(), ws, "x") if i["level"] == "error"]


def test_labels_must_be_case_ids_or_default(world):
    ws = world["ws_a"]
    g = graph()
    g["edges"][0]["label"] = "inconnu"
    assert ("bad_branches", "error") in codes(g, ws)
    g = graph()
    g["edges"][1]["label"] = "prod"  # twice the same case
    assert ("bad_branches", "error") in codes(g, ws)


def test_cases_need_valid_unique_ids_and_conditions(world):
    ws = world["ws_a"]
    for bad in (
        [],
        [{"id": "default", "expr": "true"}],
        [{"id": "a", "expr": "true"}, {"id": "a", "expr": "false"}],
        [{"id": "a", "expr": ""}],
    ):
        g = graph()
        g["nodes"][0]["cases"] = bad
        assert any(level == "error" for _, level in codes(g, ws)), bad


def test_a_case_condition_is_checked_like_any_expression(world):
    ws = world["ws_a"]
    g = graph()
    g["nodes"][0]["cases"][0]["expr"] = "inputs.nope == 'x'"
    assert ("unknown_input", "error") in codes(g, ws)


def test_reading_a_step_of_one_case_after_the_join_is_flagged(world):
    ws = world["ws_a"]
    g = graph()
    g["nodes"].append(
        node("fin", "script", language="python", script="m = 1", bindings={"v": "{{ steps.a.output.x }}"})
    )
    g["edges"].append(edge("j", "fin"))
    assert ("maybe_skipped", "warning") in codes(g, ws)
