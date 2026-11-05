import json
from pathlib import Path

import pytest

from tests.form_helpers import definition, field
from triggers import formcalc, formengine

CASES = json.loads((Path(__file__).parent / "fixtures" / "form_calc_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_formulas_on_the_shared_cases(case):
    got = formcalc.evaluate(case["formula"], case["values"])
    assert got == case["expected"] and type(got) is type(case["expected"]), got


@pytest.mark.parametrize(
    "formula",
    [
        "",
        "1 +",
        "(1 + 2",
        "1 2",
        "inconnue(1)",
        "round()",
        "round(1, 2, 3)",
        "if(1, 2)",
        "abc",
        "{a} $ 2",
        '"ouvert',
        "{1a}",
        "x" * 501,
        "(" * 25 + "1" + ")" * 25,
        " + ".join(["1"] * 120),
    ],
)
def test_unreadable_formulas_are_refused(formula):
    assert formcalc.check(formula, {"a": {}})


def test_a_formula_may_only_read_other_fields():
    assert formcalc.check("{a} + {b}", {"a": {}}) == "« b » doit être un autre champ du formulaire."
    assert formcalc.check("{a} + 1", {"a": {}}) == ""
    assert formcalc.refs("{a} * {b} + len({c})") == {"a", "b", "c"}


def _form(**total):
    return definition(field("prix", type="number"), field("qte", type="number"), field("total", type="number", **total))


def test_the_server_computes_the_value_whatever_was_sent():
    d = _form(compute="{prix} * {qte}")
    assert formengine.check_definition(d) == {}
    kept, errors = formengine.validate_submission(d, {"prix": 2.5, "qte": 4, "total": 999})
    assert kept["total"] == 10 and errors == {}
    kept, errors = formengine.validate_submission(d, {"prix": 2.5, "total": 999})  # not computable: nothing is kept
    assert "total" not in kept and errors == {}
    required = _form(compute="{prix} * {qte}", required=True)
    assert "total" in formengine.validate_submission(required, {"prix": 2.5})[1]


def test_a_calculated_field_is_evaluated_after_what_it_reads_wherever_it_is_laid_out():
    d = definition(
        {**field("total", type="number", compute="{ht} * 1.2"), "layout": {"row": 1, "col": 1, "span": 12}},
        {**field("ht", type="number", compute="{prix} * {qte}"), "layout": {"row": 2, "col": 1, "span": 6}},
        {**field("prix", type="number"), "layout": {"row": 3, "col": 1, "span": 6}},
        {**field("qte", type="number"), "layout": {"row": 3, "col": 7, "span": 6}},
    )
    assert formengine.check_definition(d) == {}
    assert formengine.validate_submission(d, {"prix": 10, "qte": 2})[0]["total"] == 24


def test_text_results_and_hidden_fields():
    d = definition(
        field("prenom"),
        field("nom"),
        field("complet", compute='upper({nom}) & " " & {prenom}'),
    )
    assert formengine.validate_submission(d, {"prenom": "Ada", "nom": "Lovelace"})[0]["complet"] == "LOVELACE Ada"
    # the formula sees a hidden field as empty
    hidden = definition(
        field("secret", rules=[{"when": {"field": "n", "op": "not_empty"}, "then": [{"do": "hide"}]}]),
        field("n"),
        field("vu", compute="is_empty({secret})", type="text"),
    )
    assert formengine.validate_submission(hidden, {"secret": "s", "n": "x"})[0]["vu"] == "true"


def test_calculation_is_refused_on_unsuitable_fields_and_in_loops():
    assert formengine.check_definition(definition(field("a"), field("c", type="checkbox", compute="{a}")))
    assert formengine.check_definition(
        definition(field("a"), field("c", type="select", compute="{a}", options=[{"value": "a", "label": "A"}]))
    )
    assert formengine.check_definition(definition(field("c", compute="{c} + 1")))  # itself
    assert formengine.check_definition(definition(field("c", compute="{zz}")))  # unknown
    loop = definition(field("a", compute="{b} & 'x'"), field("b", compute="{a} & 'y'"))
    assert "boucle" in formengine.check_definition(loop)["_"]
    rule_loop = definition(
        field("a", compute="{b} & 'x'"),
        field("b", rules=[{"when": {"field": "a", "op": "not_empty"}, "then": [{"do": "hide"}]}]),
    )
    assert "boucle" in formengine.check_definition(rule_loop)["_"]
