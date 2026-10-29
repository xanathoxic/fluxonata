"""What a step hands to a called workflow must satisfy THAT workflow's contract (a run would be refused otherwise)."""

from tests.test_engine import ws  # noqa: F401  (fixture)
from tests.wf_helpers import STR, node, publish, schema
from workflows.validation import validate_graph


def parent(extra_inputs, kind="subworkflow", **more):
    return {
        "inputs": {
            "type": "object",
            "properties": {"liste": {"type": "array"}, "texte": {"type": "string"}},
            "required": [],
        },
        "nodes": [node("go", kind, workflow="enfant", inputs=extra_inputs, **more)],
        "edges": [],
        "outputs": {},
    }


def codes(ws, graph):
    return {(i["node"], i["code"]) for i in validate_graph(graph, ws, "parent") if i["level"] == "error"}


def test_a_called_workflow_gets_its_inputs_checked(ws, world):
    publish(
        ws,
        "enfant",
        [node("e", action="echo", inputs={"value": "{{ inputs.ips }}"})],
        [],
        inputs=schema(["ips", "message"], ips=STR, message=STR),
        outputs={},
        user=world["designer"],
    )
    ok = parent({"ips": "{{ inputs.texte }}", "message": "Bonjour"})
    assert codes(ws, ok) == set()
    assert ("go", "type_mismatch") in codes(
        ws, parent({"ips": "{{ inputs.liste }}", "message": "x"})
    )  # a list for a text
    assert ("go", "missing_param") in codes(ws, parent({"ips": "{{ inputs.texte }}"}))  # « message » is required
    assert ("go", "unknown_param") in codes(ws, parent({"ips": "{{ inputs.texte }}", "message": "x", "zzz": "1"}))
    # a bare reference is text, not the value: the braces are missing
    assert ("go", "literal_reference") in codes(ws, parent({"ips": "inputs.texte", "message": "x"}))


def test_a_loop_checks_the_other_inputs_but_not_the_one_that_receives_the_item(ws, world):
    publish(
        ws,
        "enfant",
        [node("e", action="echo", inputs={"value": "{{ inputs.ip }}"})],
        [],
        inputs=schema(["ip", "etiquette"], ip=STR, etiquette=STR),
        outputs={},
        user=world["designer"],
    )
    loop = parent({"etiquette": "x"}, kind="foreach", items="inputs.liste", item_input="ip", max_iterations=3)
    assert codes(ws, loop) == set()  # « ip » is the item: not « missing »
    bad = parent({"etiquette": "x"}, kind="foreach", items="inputs.liste", item_input="inconnu", max_iterations=3)
    assert ("go", "unknown_param") in codes(ws, bad)


# ------------------------------------------------------------------------- « étape ignorée » : no false alarm
def _skipped(g):
    return {
        (i["node"], i["message"].split("»")[0]) for i in validate_graph(g, None, "x") if i["code"] == "maybe_skipped"
    }


def _flow(nodes, edges):
    return {
        "inputs": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": []},
        "nodes": nodes,
        "edges": edges,
        "outputs": {},
    }


def _sc(nid, out="x"):
    return {
        "id": nid,
        "type": "script",
        "name": nid.upper(),
        "language": "python",
        "script": f"{out} = 1",
        "bindings": {},
        "outputs": [{"name": out, "type": "integer"}],
        "ai_review": False,
    }


def _reader(nid, src):
    return {"id": nid, "type": "condition", "name": nid, "expr": f"steps.{src}.output.x == 1"}


def test_parallel_branches_that_both_always_run_do_not_warn_when_read_after_the_join():
    g = _flow(
        [
            _sc("start"),
            _sc("a"),
            _sc("b"),
            {"id": "j", "type": "join", "name": "J"},
            _reader("check_a", "a"),
            _sc("fin", "y"),
            _sc("fin2", "z"),
        ],
        [
            {"from": "start", "to": "a"},
            {"from": "start", "to": "b"},
            {"from": "a", "to": "j"},
            {"from": "b", "to": "j"},
            {"from": "j", "to": "check_a"},
            {"from": "check_a", "to": "fin", "label": "true"},
            {"from": "check_a", "to": "fin2", "label": "false"},
        ],
    )
    assert _skipped(g) == set()  # both branches run, whatever the data


def test_a_branch_ending_in_an_exception_is_a_run_that_fails_anyway_so_no_warning():
    g = _flow(
        [
            _sc("start"),
            _sc("loop"),
            {"id": "stop", "type": "throw", "name": "Stop", "message": "x"},
            _sc("other", "y"),
            {"id": "j", "type": "join", "name": "J"},
            _reader("check", "loop"),
            _sc("e1", "p"),
            _sc("e2", "q"),
        ],
        [
            {"from": "start", "to": "loop"},
            {"from": "start", "to": "other"},
            {"from": "loop", "to": "stop", "label": "error"},
            {"from": "loop", "to": "j"},
            {"from": "other", "to": "j"},
            {"from": "j", "to": "check"},
            {"from": "check", "to": "e1", "label": "true"},
            {"from": "check", "to": "e2", "label": "false"},
        ],
    )
    assert _skipped(g) == set()


def test_what_only_runs_on_one_branch_of_a_decision_or_after_a_recovery_still_warns():
    one_branch = _flow(
        [
            {"id": "d", "type": "condition", "name": "D", "expr": "inputs.n > 1"},
            _sc("a"),
            {"id": "j", "type": "join", "name": "J"},
            _reader("check", "a"),
            _sc("e1", "p"),
            _sc("e2", "q"),
        ],
        [
            {"from": "d", "to": "a", "label": "true"},
            {"from": "a", "to": "j"},
            {"from": "d", "to": "j", "label": "false"},
            {"from": "j", "to": "check"},
            {"from": "check", "to": "e1", "label": "true"},
            {"from": "check", "to": "e2", "label": "false"},
        ],
    )
    assert _skipped(one_branch) != set()
    recovery = _flow(
        [
            _sc("a"),
            _sc("plan_b", "y"),
            {"id": "j", "type": "join", "name": "J"},
            _reader("check", "a"),
            _sc("e1", "p"),
            _sc("e2", "q"),
        ],
        [
            {"from": "a", "to": "plan_b", "label": "error"},
            {"from": "a", "to": "j"},
            {"from": "plan_b", "to": "j"},
            {"from": "j", "to": "check"},
            {"from": "check", "to": "e1", "label": "true"},
            {"from": "check", "to": "e2", "label": "false"},
        ],
    )
    assert _skipped(recovery) != set()  # a may have failed: its output would not exist after the recovery


def test_two_decisions_on_the_same_expression_are_one_choice():
    g = _flow(
        [
            {"id": "d1", "type": "condition", "name": "D1", "expr": "inputs.n > 1"},
            _sc("a"),
            {"id": "j", "type": "join", "name": "J"},
            {"id": "d2", "type": "condition", "name": "D2", "expr": "inputs.n  >  1"},
            _reader("check", "a"),
            _sc("e", "q"),
            _sc("o", "p"),
        ],
        [
            {"from": "d1", "to": "a", "label": "true"},
            {"from": "a", "to": "j"},
            {"from": "d1", "to": "j", "label": "false"},
            {"from": "j", "to": "d2"},
            {"from": "d2", "to": "check", "label": "true"},
            {"from": "check", "to": "e", "label": "true"},
            {"from": "check", "to": "o", "label": "false"},
        ],
    )
    assert _skipped(g) == set()  # check only runs when n > 1, which is when « a » ran


# --------------------------------------------------------------------- the failure branch cannot read the failed step
def _codes(g):
    return {(i["node"], i["code"]) for i in validate_graph(g, None, "x") if i["code"] == "reads_failed_output"}


def test_a_step_that_only_runs_after_a_failure_cannot_read_the_failed_steps_output():
    g = _flow(
        [
            _sc("appel"),
            {
                "id": "analyse",
                "type": "script",
                "name": "Analyse",
                "language": "python",
                "script": "y = t",
                "bindings": {"t": "{{ steps.appel.output.x }}"},
                "outputs": [{"name": "y", "type": "integer"}],
                "ai_review": False,
            },
            {"id": "ko", "type": "throw", "name": "KO", "message": "x"},
            _sc("suite", "z"),
        ],
        [
            {"from": "appel", "to": "analyse", "label": "error"},
            {"from": "analyse", "to": "ko"},
            {"from": "appel", "to": "suite"},
        ],
    )
    assert _codes(g) == {("analyse", "reads_failed_output")}
    g["nodes"][1]["bindings"] = {"t": "{{ inputs.n }}"}  # a value known before the step: fine
    assert _codes(g) == set()


def test_reading_a_failed_output_after_the_paths_rejoin_is_left_to_the_other_check():
    g = _flow(
        [
            _sc("appel"),
            _sc("plan_b", "y"),
            {"id": "j", "type": "join", "name": "J"},
            _reader("check", "appel"),
            _sc("e1", "p"),
            _sc("e2", "q"),
        ],
        [
            {"from": "appel", "to": "plan_b", "label": "error"},
            {"from": "appel", "to": "j"},
            {"from": "plan_b", "to": "j"},
            {"from": "j", "to": "check"},
            {"from": "check", "to": "e1", "label": "true"},
            {"from": "check", "to": "e2", "label": "false"},
        ],
    )
    assert _codes(g) == set()  # reachable by success too: « étape ignorée » speaks about it


def test_an_unreadable_expression_that_names_the_failed_step_is_still_caught():
    g = _flow(
        [
            _sc("appel"),
            {
                "id": "analyse",
                "type": "script",
                "name": "Analyse",
                "language": "python",
                "script": "y = d",
                "bindings": {"d": "{{ steps.appel.output.x | json_encode }}"},
                "outputs": [{"name": "y", "type": "string"}],
                "ai_review": False,
            },
            {"id": "ko", "type": "throw", "name": "KO", "message": "x"},
            _sc("suite", "z"),
        ],
        [
            {"from": "appel", "to": "analyse", "label": "error"},
            {"from": "analyse", "to": "ko"},
            {"from": "appel", "to": "suite"},
        ],
    )
    assert _codes(g) == {("analyse", "reads_failed_output")}
