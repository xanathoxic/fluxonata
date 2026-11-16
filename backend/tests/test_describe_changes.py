"""Caractérisation de `ai.service.describe_changes` : le résumé en français de ce qu'une modification a changé (étapes, liaisons, notes,
variables, entrées, sorties), dans un ordre fixe. Écrit avant le découpage de la fonction : tout doit passer à l'identique avant et après."""

from ai.service import describe_changes


def g(nodes=(), edges=(), **extra):
    return {"nodes": list(nodes), "edges": list(edges), **extra}


def step(i, type_="action", **kw):
    return {"id": i, "type": type_, "name": i.upper(), **kw}


def test_nothing_changed_gives_an_empty_summary():
    graph = g([step("a")], [{"from": "a", "to": "a2"}])
    assert describe_changes(graph, graph) == []


def test_added_steps_name_their_kind_in_words_and_removed_steps_are_listed_after():
    old = g([step("gone")])
    new = g([step("w", "wait"), step("c", "condition"), step("x", "inconnu"), {"id": "noname", "type": "join"}])
    assert describe_changes(old, new) == [
        "Étape ajoutée : « W » (attente)",
        "Étape ajoutée : « C » (décision)",
        "Étape ajoutée : « X » (inconnu)",
        "Étape ajoutée : « noname » (jonction)",
        "Étape supprimée : « GONE »",
    ]


def test_a_changed_step_says_what_kind_of_change_in_a_fixed_order():
    old = g(
        [
            step("a", action="x", inputs={"v": 1}),
            step("c", "condition", expr="1"),
            step("s", "switch", cases=[1]),
            step("k", "script", script="a"),
        ]
    )
    new = g(
        [
            {**step("a", action="y", inputs={"v": 1}), "name": "Autre"},
            step("c", "condition", expr="2"),
            step("s", "switch", cases=[2]),
            step("k", "script", script="b"),
        ]
    )
    assert describe_changes(old, new) == [
        "Étape « Autre » : renommée, action ou paramètres modifiés",
        "Étape « C » : condition modifiée",
        "Étape « S » : cas modifiés",
        "Étape « K » : script modifié",
    ]


def test_unchanged_steps_are_not_mentioned_and_inputs_count_as_parameters_of_an_action():
    old = g([step("a", action="x", inputs={"v": 1}), step("b", action="x")])
    new = g([step("a", action="x", inputs={"v": 2}), step("b", action="x")])
    assert describe_changes(old, new) == ["Étape « A » : action ou paramètres modifiés"]


def test_connections_are_counted_added_then_removed():
    old = g(edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "c"}])
    new = g(edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "d"}, {"from": "a", "to": "c", "label": "error"}])
    assert describe_changes(old, new) == [
        "2 liaison(s) ajoutée(s) ou modifiée(s)",
        "1 liaison(s) retirée(s) ou modifiée(s)",
    ]


def test_notes_added_changed_and_removed():
    old = g(notes=[{"id": "n1", "text": "a"}, {"id": "n2", "text": "b"}, {"id": "n3", "text": "z" * 80}])
    new = g(notes=[{"id": "n1", "text": "A"}, {"id": "n2", "text": "b"}, {"id": "n4", "text": "y" * 80}])
    assert describe_changes(old, new) == [
        "Note n1 : texte modifié",
        f"Note ajoutée : « {'y' * 60} »",
        f"Note supprimée : « {'z' * 60} »",
    ]


def test_variables_added_changed_and_removed():
    old = g(variables=[{"name": "a", "value": 1}, {"name": "b", "value": 2}, {"name": "c", "value": 3}])
    new = g(variables=[{"name": "a", "value": 9}, {"name": "b", "value": 2}, {"name": "d", "value": 4}])
    assert describe_changes(old, new) == [
        "Variable a : valeur modifiée",
        "Variable ajoutée : d",
        "Variable supprimée : c",
    ]


def test_new_workflow_inputs_are_listed_sorted_and_removed_ones_are_not_mentioned():
    old = g(inputs={"properties": {"keep": {}, "gone": {}}})
    new = g(inputs={"properties": {"keep": {}, "zeta": {}, "alpha": {}}})
    assert describe_changes(old, new) == ["Entrée(s) du workflow ajoutée(s) : alpha, zeta"]
    assert describe_changes(old, g(inputs={"properties": {"keep": {}}})) == []


def test_changed_outputs_are_flagged_once_and_none_equals_empty():
    assert describe_changes(g(outputs={"a": "1"}), g(outputs={"a": "2"})) == ["Sorties du workflow modifiées"]
    assert describe_changes(g(), g(outputs={})) == []


def test_the_sections_come_in_a_fixed_order():
    old = g(
        [step("gone")],
        [{"from": "a", "to": "b"}],
        notes=[{"id": "n", "text": "x"}],
        variables=[{"name": "v", "value": 1}],
        outputs={"a": "1"},
    )
    new = g(
        [step("new")],
        [{"from": "a", "to": "c"}],
        notes=[{"id": "m", "text": "y"}],
        variables=[{"name": "w", "value": 1}],
        inputs={"properties": {"p": {}}},
        outputs={"a": "2"},
    )
    out = describe_changes(old, new)
    prefixes = [
        "Étape ajoutée",
        "Étape supprimée",
        "1 liaison(s) ajoutée",
        "1 liaison(s) retirée",
        "Note ajoutée",
        "Note supprimée",
        "Variable ajoutée",
        "Variable supprimée",
        "Entrée(s)",
        "Sorties",
    ]
    assert [next(i for i, line in enumerate(out) if line.startswith(p)) for p in prefixes] == list(range(len(prefixes)))
