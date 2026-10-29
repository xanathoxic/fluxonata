import pytest

from workflows.expressions import ExpressionError, evaluate, parse, paths_in, resolve

CTX = {
    "inputs": {"n": 5, "name": "Ada"},
    "steps": {"a": {"output": {"words": 4, "items": [1, 2, 3], "text": "Bonjour"}}},
}


@pytest.mark.parametrize(
    "src,expected",
    [
        ("steps.a.output.words > 3", True),
        ("steps.a.output.words >= 5", False),
        ("inputs.n == 5 and inputs.name == 'Ada'", True),
        ("not (inputs.n < 2) or false", True),
        ("steps.a.output.items[1] == 2", True),
        ("len(steps.a.output.items) == 3", True),
        ("upper(inputs.name) == 'ADA'", True),
        ("'jou' in steps.a.output.text", True),
        ("steps.a.output.text contains 'Bon'", True),
        ("2 in steps.a.output.items", True),
        ("null == null", True),
    ],
)
def test_evaluate(src, expected):
    assert evaluate(src, CTX) is expected


@pytest.mark.parametrize(
    "src",
    [
        "__import__('os').system('id')",
        "inputs.__class__",
        "steps.a.output.words +",
        "foo(1)",
        "inputs.n > ",
        "(1 == 1",
        "a" * 600,
        "1 == 1 2",
        "x[1.5]",
        "'a' ; 1",
    ],
)
def test_malicious_or_invalid_is_rejected(src):
    with pytest.raises(ExpressionError):
        evaluate(src, CTX)


def test_attribute_lookup_cannot_reach_python_internals():
    with pytest.raises(ExpressionError):
        evaluate("inputs.name.upper", CTX)  # str attribute, not dict key
    with pytest.raises(ExpressionError):
        evaluate("steps.zzz.output", CTX)


def test_type_mismatch_is_explicit():
    with pytest.raises(ExpressionError, match="Comparaison impossible"):
        evaluate("inputs.name > 3", CTX)


def test_resolve_templates_keeps_types():
    assert resolve("{{ steps.a.output.words }}", CTX) == 4
    assert resolve("Mot : {{ inputs.name }} ({{ inputs.n }})", CTX) == "Mot : Ada (5)"
    assert resolve({"x": ["{{ inputs.n }}", "plain"]}, CTX) == {"x": [5, "plain"]}


def test_paths_in():
    assert paths_in(parse("steps.a.output.words > inputs.n")) == [("steps", "a", "output", "words"), ("inputs", "n")]


def test_adjacent_templates_are_interpolated_not_one_expression():
    assert resolve("{{ inputs.n }}{{ inputs.name }}", CTX) == "5Ada"
    assert resolve("{{ inputs.n }}", CTX) == 5


def test_default_gives_the_fallback_for_a_missing_or_null_value():
    assert evaluate("default(steps.zzz.output.x, 'repli')", CTX) == "repli"
    assert evaluate("default(steps.a.output.words, 0)", CTX) == 4
    assert evaluate("default(inputs.nothing, 'ok')", {"inputs": {"nothing": None}}) == "ok"
    assert resolve("état : {{ default(steps.zzz.output.x, 'inconnu') }}", CTX) == "état : inconnu"


def test_default_takes_exactly_two_arguments_and_its_value_is_not_a_strict_read():
    with pytest.raises(ExpressionError):
        parse("default(inputs.n)")
    tree = parse("default(steps.zzz.output.x, inputs.n)")
    assert ("steps", "zzz", "output", "x") in paths_in(tree)
    assert paths_in(tree, strict=True) == [("inputs", "n")]


ARITH_CTX = {"inputs": {"a": 3, "b": 4, "l": [3, 1, 2, 2], "t": " x "}, "vars": {}, "steps": {}}


@pytest.mark.parametrize(
    "src,expected",
    [
        ("inputs.a + inputs.b * 2", 11),
        ("-inputs.a + 10", 7),
        ("(inputs.a + inputs.b) / 2", 3.5),
        ("10 % 4", 2),
        ("round(7 / 3, 2)", 2.33),
        ("round(2.6)", 3),
        ("sum(inputs.l)", 8),
        ("join(sort(unique(inputs.l)), '-')", "1-2-3"),
        ("trim(inputs.t) + '!'", "x!"),
        ("max(inputs.l)", 3),
        ("min(4, 2, 9)", 2),
        ("number('4,5') + 1", 5.5),
        ("str(inputs.a) + 'x'", "3x"),
        ("len(inputs.l) * 2 > 7", True),
        ("first(split('a,b', ','))", "a"),
    ],
)
def test_arithmetic_and_functions(src, expected):
    assert evaluate(src, ARITH_CTX) == expected


@pytest.mark.parametrize("src", ["1 / 0", "true + 1", "inputs.t - 1", "inputs.a + 'x'", "sum(3)", "unknown(1)"])
def test_arithmetic_errors_are_expression_errors(src):
    with pytest.raises(ExpressionError):
        evaluate(src, ARITH_CTX)


def test_arithmetic_paths_are_checked_by_the_validator_helpers():
    from workflows.expressions import parse, paths_in

    assert set(paths_in(parse("inputs.a + steps.s.output.n * 2"))) == {("inputs", "a"), ("steps", "s", "output", "n")}
