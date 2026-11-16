"""Expressions : défauts de syntaxe, imbrication bornée, valeurs de date et de nombre illisibles, chemins protégés par `default`."""

import pytest

from workflows.expressions import ExpressionError, evaluate, parse, paths_in


def refused(src, match=None):
    with pytest.raises(ExpressionError, match=match):
        parse(src)


def test_nesting_is_bounded_for_signs_and_parentheses():
    refused("-" * 40 + "1", "trop imbriquée")
    refused("(" * 40 + "1" + ")" * 40, "trop imbriquée")


def test_syntax_errors_are_named():
    refused("(1 + 2", "Parenthèse fermante manquante")
    refused("a.", "Nom attendu après")
    refused("a.1", "Nom attendu après")
    refused("a[1.5]", "Index entier attendu")
    refused("a[x]", "Index entier attendu")
    refused("a[1", "Crochet fermant manquant")
    refused("in", "Mot réservé")
    refused("1 + contains", "Mot réservé")


def test_indexes_and_paths_are_read():
    assert evaluate("rows[1].n", {"rows": [{"n": 1}, {"n": 2}]}) == 2


@pytest.mark.parametrize(
    "src",
    [
        "number(true)",
        "number(null)",
        "date_add(5, 1, 'days')",
        "date_add('2026-01-01T00:00:00Z', 1, 'fortnights')",
    ],
)
def test_functions_refuse_what_they_cannot_read(src):
    with pytest.raises(ExpressionError):
        evaluate(src, {})


def test_paths_protected_by_default_are_left_out_of_the_strict_list():
    tree = parse("default(steps.a.output.x, inputs.y) == inputs.z and not inputs.w")
    assert ("steps", "a", "output", "x") in paths_in(tree)
    strict = paths_in(tree, strict=True)
    assert ("steps", "a", "output", "x") not in strict and ("inputs", "y") in strict
    assert ("inputs", "z") in strict and ("inputs", "w") in strict
