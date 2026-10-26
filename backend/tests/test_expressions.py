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
