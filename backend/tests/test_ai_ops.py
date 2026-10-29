"""Edit operations applied to a copy of the graph: deterministic, reported, never raising."""

from ai.ops import apply_ops


def graph():
    return {
        "inputs": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        "variables": [{"name": "v", "type": "integer", "value": 1}],
        "notes": [{"id": "note_1", "text": "n1"}],
        "nodes": [
            {"id": "a", "type": "wait", "name": "A", "seconds": 1},
            {"id": "d", "type": "condition", "name": "D", "expr": "inputs.x == 'a'"},
            {"id": "b", "type": "wait", "name": "B", "seconds": 2},
            {"id": "c", "type": "wait", "name": "C", "seconds": 3},
            {"id": "ap", "type": "approval", "name": "Ok", "title": "t"},
        ],
        "edges": [
            {"from": "a", "to": "d"},
            {"from": "d", "to": "b", "label": "true"},
            {"from": "d", "to": "c", "label": "false"},
            {"from": "b", "to": "ap"},
        ],
        "outputs": {},
    }


def edges(g):
    return {(e["from"], e["to"], e.get("label")) for e in g["edges"]}


def run(*ops, g=None):
    return apply_ops(g or graph(), list(ops))


def test_the_original_graph_is_never_modified():
    g = graph()
    apply_ops(g, [{"op": "remove_step", "id": "c"}])
    assert g == graph()


def test_a_step_is_inserted_after_another_and_takes_over_what_followed():
    out, errors = run({"op": "add_step", "step": {"type": "wait", "id": "n", "seconds": 5}, "after": "a"})
    assert errors == [] and {("a", "n", None), ("n", "d", None)} <= edges(out) and ("a", "d", None) not in edges(out)


def test_a_step_is_inserted_on_a_decision_exit_and_a_decision_needs_its_label():
    out, errors = run({"op": "add_step", "step": {"type": "wait", "id": "n"}, "after": "d", "label": "false"})
    assert errors == [] and {("d", "n", "false"), ("n", "c", None)} <= edges(out)
    _, errors = run({"op": "add_step", "step": {"type": "wait"}, "after": "d"})
    assert "label" in errors[0]


def test_a_step_is_inserted_before_another_and_between_two():
    out, _ = run({"op": "add_step", "step": {"type": "wait", "id": "n"}, "before": "a"})
    assert ("n", "a", None) in edges(out)  # becomes the new start
    out, errors = run({"op": "add_step", "step": {"type": "wait", "id": "n"}, "after": "a", "before": "d"})
    assert errors == [] and {("a", "n", None), ("n", "d", None)} <= edges(out)
    _, errors = run({"op": "add_step", "step": {"type": "wait"}, "after": "a", "before": "b"})
    assert "pas de liaison" in errors[0]


def test_a_step_without_a_position_goes_to_the_only_end_or_asks():
    g = graph()
    g["edges"].append({"from": "c", "to": "ap"})  # one end: ap
    out, errors = run({"op": "add_step", "step": {"type": "wait", "id": "n"}}, g=g)
    assert errors == [] and ("ap", "n", None) in edges(out)
    _, errors = run({"op": "add_step", "step": {"type": "wait"}})  # two ends: ap and c
    assert "Plusieurs fins" in errors[0]


def test_ids_are_made_valid_and_unique_and_later_operations_follow_the_new_id():
    out, errors = run(
        {"op": "add_step", "step": {"type": "wait", "id": "Mon Étape"}, "after": "a"},
        {"op": "set_fields", "id": "Mon Étape", "fields": {"seconds": 9}},  # still the name the model knows
        {"op": "add_step", "step": {"type": "wait", "id": "a"}, "after": "a"},  # « a » is taken: this one is a_2
    )
    assert errors == []
    by_id = {n["id"]: n for n in out["nodes"]}
    assert by_id["mon_etape"]["seconds"] == 9 and "a_2" in by_id and len(by_id) == len(out["nodes"])
    assert ("a", "a_2", None) in edges(out)  # « a » stayed the existing step


def test_removing_a_step_bridges_its_neighbours_and_cleans_what_only_served_it():
    out, errors = run({"op": "remove_step", "id": "b"})
    assert (
        errors == []
        and ("d", "ap", "true") in edges(out)
        and all("b" not in (e["from"], e["to"]) for e in out["edges"])
    )
    g = graph()
    g["nodes"].append({"id": "t", "type": "throw", "name": "T", "message": "x"})
    g["edges"].append({"from": "c", "to": "t", "label": "error"})
    out, _ = run({"op": "remove_step", "id": "c"}, g=g)
    assert "t" not in {n["id"] for n in out["nodes"]}  # the exception hung on the removed step only


def test_removing_a_decision_keeps_one_branch_and_drops_the_other():
    _, errors = run({"op": "remove_step", "id": "d"})
    assert "keep" in errors[0]
    out, errors = run({"op": "remove_step", "id": "d", "keep": "true"})
    assert errors == [] and ("a", "b", None) in edges(out) and "c" not in {n["id"] for n in out["nodes"]}


def test_an_approval_is_never_removed():
    out, errors = run({"op": "remove_step", "id": "ap"})
    assert "Une approbation" in errors[0] and "ap" in {n["id"] for n in out["nodes"]}
    out, _ = run({"op": "remove_step", "id": "b"})  # not removed as a side effect either
    assert "ap" in {n["id"] for n in out["nodes"]}


def test_fields_are_changed_only_where_they_exist_for_that_type():
    out, errors = run({"op": "set_fields", "id": "d", "fields": {"expr": "inputs.x == 'b'", "name": "Nouveau"}})
    assert errors == [] and next(n for n in out["nodes"] if n["id"] == "d")["expr"] == "inputs.x == 'b'"
    _, errors = run({"op": "set_fields", "id": "a", "fields": {"expr": "x", "allow_self_approval": True}})
    assert "expr" in errors[0] and "allow_self_approval" in errors[0]
    _, errors = run({"op": "set_fields", "id": "zzz", "fields": {"name": "x"}})
    assert "n'existe pas" in errors[0]


def test_connect_labels_a_decision_exit_and_refuses_a_loop():
    g = graph()
    g["edges"] = [e for e in g["edges"] if e.get("label") != "false"]
    out, errors = run({"op": "connect", "from": "d", "to": "ap"}, g=g)
    assert errors == [] and ("d", "ap", "false") in edges(out)  # the free exit
    _, errors = run({"op": "connect", "from": "ap", "to": "a"})
    assert "boucle" in errors[0]
    _, errors = run({"op": "connect", "from": "d", "to": "ap"})
    assert "deux sorties" in errors[0]
    out, errors = run({"op": "disconnect", "from": "a", "to": "d"})
    assert errors == [] and ("a", "d", None) not in edges(out)
    _, errors = run({"op": "disconnect", "from": "a", "to": "ap"})
    assert "pas de liaison" in errors[0]


def test_variables_notes_inputs_and_outputs():
    out, errors = run(
        {"op": "set_variable", "name": "v", "value": 5},
        {"op": "set_variable", "name": "w", "value": 2.5},
        {"op": "remove_variable", "name": "v"},
        {"op": "add_note", "text": "nouvelle"},
        {"op": "edit_note", "id": "note_1", "text": "modifiée"},
        {"op": "set_input", "name": "y", "schema": {"type": "number"}, "required": True},
        {"op": "set_output", "name": "r", "expr": "{{ steps.b.output.x }}"},
    )
    assert errors == [] and [v["name"] for v in out["variables"]] == ["w"] and out["variables"][0]["type"] == "number"
    assert out["removed_variables"] == ["v"] and [n["text"] for n in out["notes"]] == ["modifiée", "nouvelle"]
    assert out["inputs"]["properties"]["y"] == {"type": "number"} and out["inputs"]["required"] == ["x", "y"]
    assert out["outputs"] == {"r": "{{ steps.b.output.x }}"}
    out, errors = run({"op": "remove_note", "id": "note_1"}, {"op": "remove_input", "name": "x"})
    assert (
        errors == [] and out["notes"] == [] and out["removed_notes"] == ["note_1"] and out["inputs"]["required"] == []
    )
    _, errors = run(
        {"op": "remove_variable", "name": "zz"}, {"op": "remove_note", "id": "zz"}, {"op": "remove_input", "name": "zz"}
    )
    assert len(errors) == 3


def test_bad_input_is_reported_not_raised():
    _, errors = apply_ops(graph(), "oops")
    assert "liste" in errors[0]
    _, errors = run("x", {"op": "teleport"}, {"op": "add_step", "step": {"type": "magie"}})
    assert len(errors) == 3 and "inconnue" in errors[1]
    out, errors = apply_ops(graph(), [{"op": "add_note", "text": "x"}] * 50)
    assert len(out["notes"]) == 41 and "40 premières" in errors[-1]


def test_inputs_and_bindings_are_merged_and_null_removes_a_key():
    g = graph()
    g["nodes"][0] = {"id": "a", "type": "subworkflow", "workflow": "x", "inputs": {"p": "1", "q": "2"}}
    out, errors = run({"op": "set_fields", "id": "a", "fields": {"inputs": {"q": "9", "r": "3"}}}, g=g)
    assert errors == [] and out["nodes"][0]["inputs"] == {"p": "1", "q": "9", "r": "3"}
    out, _ = run({"op": "set_fields", "id": "a", "fields": {"inputs": {"p": None}}}, g=g)
    assert out["nodes"][0]["inputs"] == {"q": "2"}


def test_a_branch_of_several_steps_hangs_on_one_exit_and_is_chained_on_the_normal_exit():
    steps = [
        {"type": "wait", "id": "prevenir", "seconds": 1},
        {"type": "throw", "name": "Création refusée", "message": "Création refusée"},
    ]
    out, errors = run({"op": "add_branch", "after": "ap", "label": "error", "steps": steps})
    assert errors == []
    added = [n["id"] for n in out["nodes"] if n["id"] not in {"a", "d", "b", "c", "ap"}]
    assert added == ["prevenir", "creation_refusee"]  # the id of the throw comes from its name
    assert {("ap", "prevenir", "error"), ("prevenir", "creation_refusee", None)} <= edges(
        out
    )  # only the first is on the error exit
    _, errors = run({"op": "add_branch", "after": "d", "steps": steps})
    assert "label" in errors[0]
    _, errors = run({"op": "add_branch", "after": "ap", "label": "error", "steps": []})
    assert "steps" in errors[0]


def switch_graph():
    g = graph()
    g["nodes"][1] = {
        "id": "d",
        "type": "switch",
        "name": "D",
        "cases": [{"id": "b", "expr": "inputs.x == 'a'"}, {"id": "c", "expr": "inputs.x == 'b'"}],
    }
    g["edges"] = [
        {"from": "a", "to": "d"},
        {"from": "d", "to": "b", "label": "b"},
        {"from": "d", "to": "c", "label": "c"},
        {"from": "b", "to": "ap"},
    ]
    return g


def test_a_switch_exit_needs_a_known_case_label():
    out, errors = run(
        {"op": "add_step", "step": {"type": "wait", "id": "n"}, "after": "d", "label": "default"}, g=switch_graph()
    )
    assert errors == [] and ("d", "n", "default") in edges(out)
    _, errors = run({"op": "add_step", "step": {"type": "wait"}, "after": "d", "label": "inconnu"}, g=switch_graph())
    assert "choix multiple" in errors[0]
    _, errors = run({"op": "connect", "from": "a", "to": "ap"}, g=switch_graph())
    out, errors = run(
        {"op": "set_fields", "id": "d", "fields": {"cases": [{"id": "z", "expr": "true"}]}}, g=switch_graph()
    )
    assert errors == [] and next(n for n in out["nodes"] if n["id"] == "d")["cases"] == [{"id": "z", "expr": "true"}]
