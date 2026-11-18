"""`{{ inputs }}` : tout l'objet des entrées du workflow, lisible partout où une expression l'est."""

from tests.wf_helpers import edge, node
from workflows.expressions import resolve
from workflows.validation import validate_graph

INPUTS = {"type": "object", "properties": {"env": {"type": "string"}, "n": {"type": "integer"}}}


def _issues(ws, **script):
    nodes = [node("s", "script", language="python", script="print(1)", outputs=[], **script)]
    return validate_graph({"inputs": INPUTS, "nodes": nodes, "edges": []}, ws, "x")


def test_whole_inputs_is_accepted_and_keeps_its_type_as_a_whole_value():
    ctx = {"inputs": {"env": "prod", "n": 2}}
    assert resolve("{{ inputs }}", ctx) == {"env": "prod", "n": 2}
    assert resolve({"tout": "{{ inputs }}"}, ctx) == {"tout": {"env": "prod", "n": 2}}


def test_whole_inputs_as_a_script_binding_raises_no_input_issue(world):
    codes = {i["code"] for i in _issues(world["ws_a"], bindings={"tout": "{{ inputs }}"})}
    assert "unknown_input" not in codes and "bad_expression" not in codes


def test_a_misspelled_input_is_still_refused(world):  # non-déclenchement : seule la forme `inputs` seule est nouvelle
    codes = {i["code"] for i in _issues(world["ws_a"], bindings={"x": "{{ inputs.absent }}"})}
    assert "unknown_input" in codes


def test_whole_inputs_is_accepted_in_a_decision_expression(world):
    g = {
        "inputs": INPUTS,
        "nodes": [node("d", "condition", condition="len(keys(inputs)) > 0"), node("a", "wait", seconds=1)],
        "edges": [edge("d", "a", "true")],
    }
    assert "unknown_input" not in {i["code"] for i in validate_graph(g, world["ws_a"], "x")}


def _var_codes(ws, variables):
    g = {"inputs": INPUTS, "variables": variables, "nodes": [node("a", "wait", seconds=1)], "edges": []}
    return {i["code"] for i in validate_graph(g, ws, "x")}


def test_a_json_variable_holds_any_json_value(world):
    for value in ({"a": [1, {"b": None}]}, [1, 2], "texte", 3, True, None):
        assert not {
            c
            for c in _var_codes(world["ws_a"], [{"name": "conf", "type": "json", "value": value}])
            if c.startswith("bad_variable")
        }


def test_the_json_type_is_for_variables_only(world):
    ws = world["ws_a"]
    assert "bad_variable_type" in _var_codes(ws, [{"name": "v", "type": "jsn", "value": 1}])
    out = [{"name": "r", "type": "json"}]  # une sortie de script « json » reste refusée
    g = {"inputs": INPUTS, "nodes": [node("s", "script", language="python", script="r = 1", outputs=out)], "edges": []}
    assert any(i["node"] == "s" and i["level"] == "error" for i in validate_graph(g, ws, "x"))
