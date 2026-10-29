"""The plan compiler: a model describes a workflow as a nested plan, the platform builds the graph (no model involved)."""

import pytest

from ai import service
from ai.plan import PlanError, compile_plan
from tests.test_ai import ai_ws  # noqa: F401  (fixture)


def edges(g):
    return {(e["from"], e["to"], e.get("label")) for e in g["edges"]}


def ids(g):
    return [n["id"] for n in g["nodes"]]


def wait(i, **kw):
    return {"type": "wait", "id": i, "seconds": 5, **kw}


def test_a_sequence_is_chained_and_has_a_single_start():
    g = compile_plan({"steps": [wait("a"), wait("b"), wait("c")]})
    assert ids(g) == ["a", "b", "c"] and edges(g) == {("a", "b", None), ("b", "c", None)}


def test_a_decision_gets_true_and_false_exits_and_its_branches_meet_on_a_join():
    g = compile_plan(
        {
            "steps": [
                {
                    "type": "if",
                    "id": "d",
                    "name": "D",
                    "expr": "inputs.x > 1",
                    "then": [wait("oui")],
                    "else": [wait("non")],
                },
                wait("suite"),
            ]
        }
    )
    assert ("d", "oui", "true") in edges(g) and ("d", "non", "false") in edges(g)
    join = next(n["id"] for n in g["nodes"] if n["type"] == "join")
    assert {("oui", join, None), ("non", join, None), (join, "suite", None)} <= edges(g)
    assert ids(g).index(join) < ids(g).index("suite")  # drawn before the step it feeds


def test_an_empty_branch_passes_through_with_its_label():
    g = compile_plan({"steps": [{"type": "if", "id": "d", "expr": "inputs.x", "then": [wait("oui")]}, wait("suite")]})
    join = next(n["id"] for n in g["nodes"] if n["type"] == "join")
    assert ("d", join, "false") in edges(g) and ("oui", join, None) in edges(g)


def test_a_refused_approval_hangs_on_the_error_exit_not_on_success():
    g = compile_plan(
        {
            "steps": [
                {
                    "type": "approval",
                    "id": "ok",
                    "title": "Valider",
                    "on_error": [{"type": "throw", "id": "no", "message": "Refusé"}],
                },
                wait("suite"),
            ]
        }
    )
    assert ("ok", "no", "error") in edges(g) and ("ok", "suite", None) in edges(g)
    assert not any(e["from"] == "no" for e in g["edges"])  # the exception ends its branch


def test_a_recovery_branch_that_does_not_end_rejoins_the_flow():
    g = compile_plan({"steps": [{"type": "script", "id": "s", "on_error": [wait("plan_b")]}, wait("suite")]})
    join = next(n["id"] for n in g["nodes"] if n["type"] == "join")
    assert {("s", join, None), ("plan_b", join, None), ("s", "plan_b", "error")} <= edges(g)


def test_nothing_can_follow_an_exception():
    with pytest.raises(PlanError, match="inaccessible"):
        compile_plan({"steps": [{"type": "throw", "id": "t", "message": "x"}, wait("apres")]})


def test_parallel_branches_fork_from_one_point_and_meet_on_a_join():
    g = compile_plan(
        {"steps": [wait("a"), {"type": "parallel", "branches": [[wait("b")], [wait("c"), wait("d")]]}, wait("e")]}
    )
    assert {("a", "b", None), ("a", "c", None), ("c", "d", None)} <= edges(g)
    join = next(n["id"] for n in g["nodes"] if n["type"] == "join")
    assert {("b", join, None), ("d", join, None), (join, "e", None)} <= edges(g)


def test_ids_are_normalised_and_references_follow():
    g = compile_plan(
        {
            "steps": [
                {
                    "type": "script",
                    "id": "Calcul Total",
                    "script": "x = 1",
                    "outputs": [{"name": "x", "type": "integer"}],
                },
                {"type": "wait", "id": "Calcul Total", "seconds": 1},  # duplicate id
                {
                    "type": "if",
                    "id": "ctrl",
                    "expr": "steps.Calcul Total.output.x > 1",
                    "then": [wait("z", note="{{ steps.Calcul Total.output.x }}")],
                },
            ]
        }
    )
    assert ids(g)[:3] == ["calcul_total", "calcul_total_2", "ctrl"]
    assert len(set(ids(g))) == len(ids(g))


def test_malformed_plans_say_what_to_change():
    for plan, word in [
        ("x", "objet"),
        ({"steps": []}, "aucune étape"),
        ({"steps": [{"type": "magie"}]}, "inconnu"),
        ({"steps": [{"type": "if", "then": [wait("a")]}]}, "expression"),
        ({"steps": [{"type": "if", "expr": "x"}]}, "aucune branche"),
        ({"steps": [{"type": "parallel", "branches": [[wait("a")]]}]}, "deux branches"),
        ({"steps": "oops"}, "liste"),
        ({"steps": [wait(f"s{i}") for i in range(60)]}, "trop grand"),
    ]:
        with pytest.raises(PlanError, match=word):
            compile_plan(plan)


def test_a_compiled_plan_passes_the_platform_validator_with_no_topology_error(ai_ws):  # noqa: F811
    ws, _ = ai_ws
    plan = {
        "inputs": {"texte": {"type": "string"}},
        "variables": [{"name": "seuil", "type": "integer", "value": 10}],
        "notes": ["à relire"],
        "steps": [
            {
                "type": "action",
                "id": "stats",
                "name": "Stats",
                "action": "text-stats",
                "inputs": {"text": "{{ inputs.texte }}"},
            },
            {
                "type": "if",
                "id": "long",
                "name": "Long ?",
                "expr": "steps.stats.output.words > vars.seuil",
                "then": [
                    {
                        "type": "approval",
                        "id": "ok",
                        "title": "Valider",
                        "on_error": [{"type": "throw", "id": "ko", "message": "Refusé"}],
                    }
                ],
                "else": [wait("pause")],
            },
            {"type": "parallel", "branches": [[wait("p1")], [wait("p2")]]},
            wait("fin"),
        ],
    }
    graph, issues = service.sanitize_draft(compile_plan(plan), ws, "x")
    assert [i for i in issues if i["level"] == "error"] == []
    targets = {e["to"] for e in graph["edges"]}
    assert [n["id"] for n in graph["nodes"] if n["id"] not in targets] == ["stats"]  # one start
    assert graph["notes"][0]["text"] == "à relire" if "notes" in graph else True


def test_a_decision_with_a_calculation_is_refused_with_the_way_out():
    with pytest.raises(PlanError, match="calcule le reste dans une étape script"):
        compile_plan({"steps": [{"type": "if", "id": "d", "expr": "sqrt(steps.a.output.x) > 1", "then": [wait("n")]}]})
    compile_plan({"steps": [{"type": "if", "id": "d", "expr": "{{ steps.a.output.ok == true }}", "then": [wait("n")]}]})


def test_an_unreadable_expression_can_be_left_to_the_validator():
    plan = {"steps": [{"type": "if", "id": "d", "expr": "abs(a) > 1", "then": [wait("n")]}]}
    assert compile_plan(plan, check_expressions=False)["nodes"][0]["expr"] == "abs(a) > 1"


def switch_plan():
    return {
        "inputs": {"env": {"type": "string"}},
        "steps": [
            {
                "type": "switch",
                "id": "choix",
                "name": "Environnement",
                "cases": [
                    {"id": "prod", "expr": "inputs.env == 'prod'", "steps": [wait("p")]},
                    {"id": "dev", "expr": "inputs.env == 'dev'", "steps": [wait("d")]},
                ],
                "default": [wait("o")],
            },
            wait("fin"),
        ],
    }


def test_a_switch_gets_one_labelled_exit_per_case_and_default_and_the_branches_meet_on_a_join():
    g = compile_plan(switch_plan())
    node = next(n for n in g["nodes"] if n["id"] == "choix")
    assert node["type"] == "switch" and [c["id"] for c in node["cases"]] == ["prod", "dev"]
    assert {("choix", "p", "prod"), ("choix", "d", "dev"), ("choix", "o", "default")} <= edges(g)
    joins = [n["id"] for n in g["nodes"] if n["type"] == "join"]
    assert len(joins) == 1 and ({(s, joins[0]) for s in "pdo"} <= {(e[0], e[1]) for e in edges(g)})


def test_a_case_without_steps_and_no_default_still_continues_with_its_label():
    plan = switch_plan()
    plan["steps"][0]["cases"][1]["steps"] = []
    del plan["steps"][0]["default"]
    g = compile_plan(plan)
    assert ("choix", "p", "prod") in edges(g) and any(e[0] == "choix" and e[2] == "default" for e in edges(g))


def test_a_switch_needs_cases_with_expressions():
    with pytest.raises(PlanError, match="demande des cas"):
        compile_plan({"steps": [{"type": "switch", "id": "s", "default": [wait("a")]}]})
    with pytest.raises(PlanError, match="expression"):
        compile_plan({"steps": [{"type": "switch", "id": "s", "cases": [{"id": "a", "steps": [wait("x")]}]}]})
    with pytest.raises(PlanError, match="calcule la valeur"):
        compile_plan({"steps": [{"type": "switch", "id": "s", "cases": [{"id": "a", "expr": "sqrt(inputs.x) > 1"}]}]})


def test_a_compiled_switch_passes_the_validator_and_the_sanitiser_keeps_its_labels(ai_ws):  # noqa: F811
    ws, _ = ai_ws
    graph, issues = service.sanitize_draft(compile_plan(switch_plan()), ws, "x")
    assert [i for i in issues if i["level"] == "error"] == []
    assert {e.get("label") for e in graph["edges"] if e["from"] == "choix"} == {"prod", "dev", "default"}
