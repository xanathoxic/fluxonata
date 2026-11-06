import json
from pathlib import Path

import pytest

from tests.form_helpers import definition, field
from triggers import formengine, formrules

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "form_rules_cases.json").read_text())
STATES = json.loads((FIXTURES / "form_rules_states.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_conditions_on_the_shared_cases(case):
    assert formrules.eval_node(case["cond"], case["values"]) is case["expected"]


@pytest.mark.parametrize("case", STATES, ids=[c["name"] for c in STATES])
def test_field_states_on_the_shared_cases(case):
    state = formrules.apply_rules(case["field"], case["values"], formrules.initial_state(case["field"]))
    for key, want in case["expected"].items():
        assert state[key] == want, key


def test_text_templates_fill_in_other_fields():
    assert formrules.render("Bonjour {nom} ({age} ans)", {"nom": "Ada", "age": 36}) == "Bonjour Ada (36 ans)"
    assert formrules.render("{x}{y}", {"x": ["a", "b"], "y": True}) == "a,btrue"
    assert formrules.render("{inconnu} reste vide", {}) == " reste vide"
    assert formrules.render("pas de jeton { nom }", {"nom": "Ada"}) == "pas de jeton { nom }"


def rule(when, then, **kw):
    return {"when": when, "then": then, **kw}


def cond(f, op, **kw):
    return {"field": f, "op": op, **kw}


def form(*fields):
    return definition(*fields)


def test_a_valid_set_of_rules_is_accepted():
    d = form(
        field("pays", "select", options=[{"value": "FR"}, {"value": "BE"}]),
        field("age", "number"),
        field(
            "dept",
            rules=[
                rule(
                    {
                        "op": "all",
                        "items": [
                            cond("pays", "equals", value="FR", ci=True),
                            {
                                "op": "any",
                                "items": [cond("age", "gte", value=18), cond("pays", "in_list", values=["FR", "BE"])],
                            },
                        ],
                    },
                    [
                        {"do": "show"},
                        {"do": "require"},
                        {"do": "text", "target": "label", "text": "Département ({pays})"},
                    ],
                    **{"else": [{"do": "hide"}], "name": "Seulement en France"},
                )
            ],
        ),
    )
    assert formengine.check_definition(d) == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"when": cond("nope", "equals", value="x"), "then": [{"do": "show"}]},  # unknown field
        {"when": cond("x", "equals", value="x"), "then": [{"do": "show"}]},  # the field itself
        {"when": cond("a", "bogus", value="x"), "then": [{"do": "show"}]},
        {"when": cond("a", "equals"), "then": [{"do": "show"}]},  # no operand
        {"when": cond("a", "equals", value="x", ref="a"), "then": [{"do": "show"}]},  # two operands
        {"when": cond("a", "is_empty", value="x"), "then": [{"do": "show"}]},  # operand on a unary operator
        {"when": cond("a", "in_list", values=[]), "then": [{"do": "show"}]},
        {"when": cond("a", "in_list", value="x"), "then": [{"do": "show"}]},
        {"when": cond("a", "equals", ref="nope"), "then": [{"do": "show"}]},
        {"when": cond("a", "equals", value={"x": 1}), "then": [{"do": "show"}]},
        {"when": cond("a", "equals", value="x" * 501), "then": [{"do": "show"}]},
        {"when": cond("a", "is_true"), "then": [{"do": "show"}]},  # a is a text field
        {"when": cond("a", "includes_any", values=["x"]), "then": [{"do": "show"}]},
        {"when": cond("a", "equals", value="x", ci="oui"), "then": [{"do": "show"}]},
        {"when": cond("a", "equals", value="x", extra=1), "then": [{"do": "show"}]},
        {"when": {"op": "all", "items": []}, "then": [{"do": "show"}]},
        {"when": {"op": "xor", "items": [cond("a", "not_empty")]}, "then": [{"do": "show"}]},
        {"when": {"op": "all", "items": [cond("a", "not_empty")], "x": 1}, "then": [{"do": "show"}]},
        {"when": cond("a", "not_empty"), "then": []},
        {"when": cond("a", "not_empty")},
        {"when": cond("a", "not_empty"), "then": [{"do": "explode"}]},
        {"when": cond("a", "not_empty"), "then": [{"do": "show", "x": 1}]},
        {"when": cond("a", "not_empty"), "then": [{"do": "text", "target": "value", "text": "x"}]},
        {"when": cond("a", "not_empty"), "then": [{"do": "text", "target": "label", "text": "x" * 501}]},
        {"when": cond("a", "not_empty"), "then": [{"do": "text", "target": "label", "text": "{x}"}]},
        {"when": cond("a", "not_empty"), "then": [{"do": "options", "values": ["x"]}]},  # x is a text field
        {"when": cond("a", "not_empty"), "then": [{"do": "show"}] * 11},
        {"when": cond("a", "not_empty"), "then": [{"do": "show"}], "script": "x"},
        {"then": [{"do": "show"}]},
        "texte",
    ],
)
def test_malformed_rules_are_refused(bad):
    d = form(field("a"), field("x", rules=[bad]), field("later"))
    d["sections"][0]["fields"][1]["rules"] = [bad]
    assert formengine.check_definition(d), bad


def test_limits_and_nesting():
    many = [rule(cond("a", "not_empty"), [{"do": "show"}]) for _ in range(11)]
    assert formengine.check_definition(form(field("a"), field("x", rules=many)))
    deep: dict = cond("a", "not_empty")
    for _ in range(5):
        deep = {"op": "all", "items": [deep]}
    assert formengine.check_definition(form(field("a"), field("x", rules=[rule(deep, [{"do": "show"}])])))
    wide = {"op": "all", "items": [cond("a", "not_empty") for _ in range(31)]}
    assert formengine.check_definition(form(field("a"), field("x", rules=[rule(wide, [{"do": "show"}])])))
    assert formengine.check_definition(form(field("a"), field("x", rules="oui")))


def test_options_effect_needs_the_fields_own_options():
    ok = form(
        field("a"),
        field(
            "x",
            "select",
            options=[{"value": "p"}, {"value": "q"}],
            rules=[rule(cond("a", "not_empty"), [{"do": "options", "values": ["p"]}])],
        ),
    )
    assert formengine.check_definition(ok) == {}
    unknown = form(
        field("a"),
        field(
            "x",
            "select",
            options=[{"value": "p"}],
            rules=[rule(cond("a", "not_empty"), [{"do": "options", "values": ["zzz"]}])],
        ),
    )
    assert formengine.check_definition(unknown)
    dynamic = form(
        field("a"),
        field(
            "x",
            "select",
            source={"connection": "c", "path": "/"},
            rules=[rule(cond("a", "not_empty"), [{"do": "options", "values": ["p"]}])],
        ),
    )
    assert formengine.check_definition(dynamic)


def test_blocks_can_be_shown_hidden_and_retexted_but_never_required():
    block = {"key": "note", "type": "paragraph", "label": "Texte"}
    ok = form(
        field("a"),
        {
            **block,
            "rules": [
                rule(
                    cond("a", "equals", value="x"),
                    [{"do": "show"}, {"do": "text", "target": "label", "text": "Bonjour {a}"}],
                    **{"else": [{"do": "hide"}]},
                )
            ],
        },
    )
    assert formengine.check_definition(ok) == {}
    assert formengine.check_definition(
        form(field("a"), {**block, "rules": [rule(cond("a", "not_empty"), [{"do": "require"}])]})
    )
    assert formengine.check_definition(
        form(
            field("a"),
            {**block, "rules": [rule(cond("a", "not_empty"), [{"do": "text", "target": "help", "text": "x"}])]},
        )
    )


# ---- what the server does with a submission -----------------------------------------------------------------------------
def conditional_form():
    return form(
        field("pays", "select", required=True, options=[{"value": "FR"}, {"value": "BE"}]),
        field(
            "dept",
            required=True,
            rules=[rule(cond("pays", "equals", value="FR"), [{"do": "show"}], **{"else": [{"do": "hide"}]})],
        ),
        field("tva", rules=[rule(cond("pays", "equals", value="BE"), [{"do": "require"}])]),
        field(
            "ville",
            "select",
            options=[{"value": "paris"}, {"value": "lyon"}, {"value": "bruxelles"}],
            rules=[
                rule(
                    cond("pays", "equals", value="FR"),
                    [{"do": "options", "values": ["paris", "lyon"]}],
                    **{"else": [{"do": "options", "values": ["bruxelles"]}]},
                )
            ],
        ),
    )


def test_a_hidden_field_is_dropped_and_not_required():
    kept, errors = formengine.validate_submission(
        conditional_form(), {"pays": "BE", "dept": "75", "tva": "BE0123", "ville": "bruxelles"}
    )
    assert kept == {"pays": "BE", "tva": "BE0123", "ville": "bruxelles"} and errors == {}  # dept is hidden: dropped


def test_a_shown_required_field_is_required():
    _, errors = formengine.validate_submission(conditional_form(), {"pays": "FR", "ville": "paris"})
    assert errors == {"dept": "Ce champ est obligatoire."}


def test_a_rule_can_make_a_field_required():
    _, errors = formengine.validate_submission(conditional_form(), {"pays": "BE", "ville": "bruxelles"})
    assert errors == {"tva": "Ce champ est obligatoire."}
    _, ok = formengine.validate_submission(conditional_form(), {"pays": "FR", "dept": "75", "ville": "lyon"})
    assert ok == {}  # tva is only required in Belgium


def test_the_accepted_choices_follow_the_rules():
    _, errors = formengine.validate_submission(conditional_form(), {"pays": "FR", "dept": "75", "ville": "bruxelles"})
    assert errors == {"ville": "Choix non proposé."}  # valid in the list, not for this country
    _, errors = formengine.validate_submission(conditional_form(), {"pays": "BE", "tva": "x", "ville": "paris"})
    assert errors == {"ville": "Choix non proposé."}


def test_conditions_read_what_was_kept_so_a_hidden_field_counts_as_empty():
    d = form(
        field("a", required=False),
        field("b", rules=[rule(cond("a", "equals", value="x"), [{"do": "show"}], **{"else": [{"do": "hide"}]})]),
        field("c", rules=[rule(cond("b", "is_empty"), [{"do": "hide"}])]),
    )
    kept, _ = formengine.validate_submission(d, {"a": "y", "b": "secret", "c": "v"})
    assert kept == {"a": "y"}  # b hidden → empty → c hidden by its own rule
    kept, _ = formengine.validate_submission(d, {"a": "x", "b": "v", "c": "w"})
    assert kept == {"a": "x", "b": "v", "c": "w"}


def test_the_legacy_visibility_expression_still_applies_with_rules():
    d = form(
        field("a"), field("b", visible_if="values.a == 'x'", rules=[rule(cond("a", "not_empty"), [{"do": "require"}])])
    )
    kept, errors = formengine.validate_submission(d, {"a": "y", "b": "v"})
    assert kept == {"a": "y"} and errors == {}
    _, errors = formengine.validate_submission(d, {"a": "x"})
    assert errors == {"b": "Ce champ est obligatoire."}


def test_a_workflow_required_input_cannot_hide_behind_rules():
    from tests.wf_helpers import schema

    d = form(field("a"), field("b", rules=[rule(cond("a", "not_empty"), [{"do": "hide"}])]))
    problems = formengine.check_against_inputs(d, schema(["b"], a={"type": "string"}, b={"type": "string"}))
    assert "b" in problems
    plain = form(field("a"), field("b", rules=[rule(cond("a", "not_empty"), [{"do": "require"}])]))
    assert formengine.check_against_inputs(plain, schema(["b"], a={"type": "string"}, b={"type": "string"})) == {}


def test_a_rule_may_read_any_other_field_even_side_by_side_or_below():
    d = form(field("a", rules=[{"when": cond("b", "equals", value="x"), "then": [{"do": "hide"}]}]), field("b"))
    assert formengine.check_definition(d) == {}
    kept, errors = formengine.validate_submission(d, {"a": "v", "b": "x"})
    assert kept == {"b": "x"} and errors == {}
    assert formengine.validate_submission(d, {"a": "v", "b": "y"})[0] == {"a": "v", "b": "y"}


def test_fields_reading_each_other_in_a_loop_are_refused():
    hide = lambda other: [{"when": cond(other, "not_empty"), "then": [{"do": "hide"}]}]  # noqa: E731
    d = form(field("a", rules=hide("b")), field("b", rules=hide("c")), field("c", rules=hide("a")))
    assert "boucle" in formengine.check_definition(d)["_"]
    legacy = form(field("a", visible_if="values.b == 'x'"), field("b", rules=hide("a")))
    assert "boucle" in formengine.check_definition(legacy)["_"]


def test_dependencies_are_evaluated_first_whatever_the_reading_order():
    # c (first) reads b, b reads a (last): a is evaluated before b, b before c
    hide = lambda other: [{"when": cond(other, "equals", value="x"), "then": [{"do": "hide"}]}]  # noqa: E731
    d = form(field("c", rules=hide("b")), field("b", rules=hide("a")), field("a"))
    assert [f["key"] for f in formengine.evaluation_order(list(formengine.data_fields(d)))] == ["a", "b", "c"]
    # a=x hides b, so c (reading the hidden, dropped b) stays shown
    kept, _ = formengine.validate_submission(d, {"a": "x", "b": "x", "c": "v"})
    assert kept == {"a": "x", "c": "v"}


def _fill(value, **extra):
    return field(
        "x", rules=[{"when": cond("a", "equals", value="FR"), "then": [{"do": "fill", "value": value}]}], **extra
    )


def test_fill_sets_the_value_whatever_was_sent_and_reads_other_fields():
    d = form(field("a"), field("b"), _fill("{a}/{b}"))
    assert formengine.check_definition(d) == {}
    kept, errors = formengine.validate_submission(d, {"a": "FR", "b": "7", "x": "forgé"})
    assert kept["x"] == "FR/7" and errors == {}
    kept, _ = formengine.validate_submission(d, {"a": "DE", "x": "libre"})
    assert kept["x"] == "libre"  # the rule does not apply: the person's value stays


def test_fill_converts_to_the_field_type():
    n = form(field("a"), _fill("12,5", type="number"))
    assert formengine.validate_submission(n, {"a": "FR"})[0]["x"] == 12.5
    c = form(field("a"), _fill("oui", type="checkbox"))
    assert formengine.validate_submission(c, {"a": "FR"})[0]["x"] is True
    bad = form(field("a"), _fill("abc", type="number"))
    assert "x" in formengine.validate_submission(bad, {"a": "FR"})[1]  # not a number: the usual check refuses it


def test_fill_is_refused_on_blocks_lists_and_unknown_fields():
    effect = {"do": "fill", "value": "v"}
    for bad_field in (
        field("x", type="checkbox_list", options=[{"value": "a", "label": "A"}]),
        {"key": "t", "type": "heading", "label": "T", "rules": [{"when": cond("a", "not_empty"), "then": [effect]}]},
    ):
        bad_field = {**bad_field, "rules": [{"when": cond("a", "not_empty"), "then": [effect]}]}
        assert formengine.check_definition(form(field("a"), bad_field))
    assert formengine.check_definition(form(field("a"), _fill("{inconnu}")))
    assert formengine.check_definition(form(field("a"), _fill("x" * 501)))


def _composed(**c):
    return field("x", rules=[{"when": c, "then": [{"do": "hide"}]}])


def test_a_composed_value_is_checked_and_read_like_a_field():
    ok = {"template": "{prenom} {nom}", "op": "equals", "value": "Ada Lovelace"}
    d = form(field("prenom"), field("nom"), _composed(**ok))
    assert formengine.check_definition(d) == {}
    assert formrules.reads(d["sections"][0]["fields"][2]) == {"prenom", "nom"}
    assert formengine.validate_submission(d, {"prenom": "Ada", "nom": "Lovelace", "x": "v"})[0] == {
        "prenom": "Ada",
        "nom": "Lovelace",
    }
    for bad in (
        {**ok, "template": "{inconnu}"},
        {**ok, "field": "prenom"},
        {**ok, "template": ""},
        {**ok, "template": "x" * 501},
        {**ok, "op": "is_true"},
        {**ok, "op": "includes_any", "values": ["a"]},
        {"template": "{x}", "op": "equals", "value": "a"},  # the field itself
    ):
        bad_def = form(field("prenom"), field("nom"), _composed(**bad))
        assert formengine.check_definition(bad_def), bad


def test_composed_values_take_part_in_loop_detection():
    loop = form(
        field("a", rules=[{"when": {"template": "{b}!", "op": "not_empty"}, "then": [{"do": "hide"}]}]),
        field("b", rules=[{"when": cond("a", "not_empty"), "then": [{"do": "hide"}]}]),
    )
    assert "boucle" in formengine.check_definition(loop)["_"]


def _load_field(what="options", **extra):
    ctl = {"ref": "c", "version": "1.0.0", "name": "C", "spec": {"version": 1, "mode": "items", "dynamic": True}}
    return field(
        "m",
        "custom",
        control=ctl,
        rules=[rule(cond("a", "equals", value="FR"), [{"do": "load", "what": what}])],
        **extra,
    )


def test_a_rule_may_put_the_loading_of_a_service_under_conditions():
    svc = {"connection": "svc", "path": "/m"}
    ok = form(field("a"), _load_field("options", source=svc))
    assert formengine.check_definition(ok) == {}
    assert formengine.check_definition(form(field("a"), _load_field("prefill", prefill=svc))) == {}
    assert formrules.gated_loads(ok["sections"][0]["fields"][1]) == {"options"}
    assert formrules.gated_loads(field("x")) == set()
    # the field must read that service, and only a control field does
    assert formengine.check_definition(form(field("a"), _load_field("options")))
    assert formengine.check_definition(form(field("a"), _load_field("prefill", source=svc)))
    assert formengine.check_definition(form(field("a"), _load_field("autre", source=svc)))
    assert formengine.check_definition(
        form(field("a"), field("t", rules=[rule(cond("a", "not_empty"), [{"do": "load", "what": "options"}])]))
    )
    bad = _load_field("options", source=svc)
    bad["rules"][0]["then"][0]["extra"] = 1
    assert formengine.check_definition(form(field("a"), bad))


def test_readonly_and_editable_are_effects_without_parameter_and_not_for_blocks():
    ok = form(
        field("a"),
        field(
            "b", rules=[rule(cond("a", "equals", value="x"), [{"do": "readonly"}], **{"else": [{"do": "editable"}]})]
        ),
    )
    assert formengine.check_definition(ok) == {}
    assert formengine.check_definition(
        form(field("a"), field("b", rules=[rule(cond("a", "not_empty"), [{"do": "readonly", "x": 1}])]))
    )
    block = {"key": "note", "type": "paragraph", "label": "Texte"}
    assert formengine.check_definition(
        form(field("a"), {**block, "rules": [rule(cond("a", "not_empty"), [{"do": "readonly"}])]})
    )


def test_readonly_is_a_presentation_effect_the_server_does_not_enforce():
    """Le serveur n'a pas de valeur de référence pour comparer ce qui est envoyé : pour IMPOSER une valeur, « fill » ou un champ calculé."""
    d = form(
        field("a"),
        field("b", rules=[rule(cond("a", "equals", value="x"), [{"do": "readonly"}])]),
    )
    state = formrules.apply_rules(
        d["sections"][0]["fields"][1], {"a": "x"}, formrules.initial_state(d["sections"][0]["fields"][1])
    )
    assert state["readonly"] is True
    kept, problems = formengine.validate_submission(d, {"a": "x", "b": "valeur envoyée"})
    assert problems == {} and kept["b"] == "valeur envoyée"  # limite connue et documentée, pas une garantie
