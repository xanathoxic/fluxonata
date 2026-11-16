"""Caractérisation de l'évaluateur d'expressions (`workflows.expressions.evaluate_ast`) : chaque forme de nœud, ses limites et ses erreurs.

Écrit avant le découpage de la fonction : tout doit passer à l'identique avant et après."""

import pytest

from workflows.expressions import MAX_TEXT, ExpressionError, evaluate, evaluate_ast

CTX = {"inputs": {"n": 5, "s": "ab", "l": [1, 2], "d": {"k": 1}, "none": None, "flag": True}}


@pytest.mark.parametrize(
    "src,expected",
    [
        ("1 + 2", 3),
        ("7 - 2", 5),
        ("3 * 4", 12),
        ("9 / 2", 4.5),
        ("9 % 4", 1),
        ("'a' + 'b'", "ab"),
        ("2.5 + 1", 3.5),
        ("true and false", False),
        ("false or inputs.n > 1", True),
        ("not inputs.flag", False),
        ("inputs.n == 5", True),
        ("inputs.n != 5", False),
        ("2 in inputs.l", True),
        ("inputs.s contains 'a'", True),
        ("inputs.n < 6", True),
        ("inputs.n <= 5", True),
        ("inputs.n > 5", False),
        ("inputs.n >= 5", True),
        ("'a' < 'b'", True),
        ("null == null", True),
        ("len(inputs.l)", 2),
        ("upper(inputs.s)", "AB"),
    ],
)
def test_each_form_of_node_gives_its_value(src, expected):
    assert evaluate(src, CTX) == expected


def test_and_or_short_circuit_and_return_booleans():
    # the right side is never evaluated when the left one decides: no error from the unknown path
    assert evaluate("false and inputs.absent.x", CTX) is False
    assert evaluate("true or inputs.absent.x", CTX) is True
    assert evaluate("inputs.n and inputs.s", CTX) is True


def test_default_replaces_a_missing_or_null_value_only():
    assert evaluate("default(inputs.absent, 'x')", CTX) == "x"
    assert evaluate("default(inputs.none, 'x')", CTX) == "x"
    assert evaluate("default(inputs.n, 'x')", CTX) == 5
    assert evaluate("default(inputs.flag, 'x')", CTX) is True


@pytest.mark.parametrize(
    "src,message",
    [
        ("1 / 0", "Division par zéro."),
        ("1 % 0", "Division par zéro."),
        ("1 + 'a'", "Calcul impossible"),
        ("true + 1", "Calcul impossible"),
        ("'a' - 'b'", "Calcul impossible"),
        ("inputs.l + 1", "Calcul impossible"),
        ("inputs.n < 'a'", "Comparaison impossible entre int et str."),
        ("inputs.d < inputs.d", "Comparaison impossible entre dict et dict."),
        ("inputs.n in 3", "Comparaison impossible"),
        ("len(5)", "Appel de len() impossible"),
        ("inputs.absent.x", ""),
    ],
)
def test_errors_are_expression_errors_with_a_readable_message(src, message):
    with pytest.raises(ExpressionError) as exc:
        evaluate(src, CTX)
    assert message in str(exc.value)


def test_text_concatenation_is_bounded():
    big = "x" * MAX_TEXT
    with pytest.raises(ExpressionError, match="Texte trop long"):
        evaluate_ast(("arith", "+", ("lit", big), ("lit", "y")), {})
    assert len(evaluate_ast(("arith", "+", ("lit", big[:-1]), ("lit", "y")), {})) == MAX_TEXT


def test_an_unknown_node_kind_is_refused():
    with pytest.raises(ExpressionError, match="Expression invalide"):
        evaluate_ast(("inconnu",), {})


def test_an_unknown_function_is_refused_as_a_call_error():
    with pytest.raises(ExpressionError, match=r"Appel de eval\(\) impossible"):
        evaluate_ast(("call", "eval", [("lit", 1)]), {})
