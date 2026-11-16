"""Aides de débogage de l'éditeur (`workflows.debug`) : validation des données épinglées, masquage des valeurs sensibles à l'affichage,
calcul des étapes pures sur les données réelles, aperçu d'expression borné. Comportements d'affichage et de refus, sans exécuter d'étape."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from workflows import debug
from workflows.debug import MASK, MAX_PIN_BYTES, MAX_PINS


def test_pins_are_validated_as_an_object_of_known_steps_with_object_values():
    assert debug.validate_pins(None, {"a"}) == [] and debug.validate_pins({}, {"a"}) == []
    assert debug.validate_pins({"a": {"x": 1}}, {"a"}) == []
    assert debug.validate_pins([1], {"a"}) == ["Les données épinglées doivent former un objet (étape → sortie)."]
    assert debug.validate_pins({"zz": {"x": 1}}, {"a"}) == ["Donnée épinglée sur une étape absente : zz."]
    assert debug.validate_pins({"a": [1]}, {"a"}) == ["La donnée épinglée de « a » doit être un objet (sa sortie)."]


def test_pins_are_bounded_in_number_and_in_size():
    too_many = {f"s{i}": {} for i in range(MAX_PINS + 1)}
    assert f"{MAX_PINS} données épinglées au plus." in debug.validate_pins(too_many, set(too_many))
    big = {"a": {"x": "y" * MAX_PIN_BYTES}}
    assert debug.validate_pins(big, {"a"}) == [f"La donnée épinglée de « a » dépasse {MAX_PIN_BYTES // 1024} Ko."]


@pytest.mark.parametrize(
    "key",
    ["password", "Mot-de-passe", "api_key", "Authorization", "client_secret", "set-cookie", "private-key", "token"],
)
def test_values_under_sensitive_names_are_masked_at_any_depth(key):
    data = {"ok": 1, key: "valeur", "liste": [{key: "autre", "visible": "oui"}]}
    shown = debug.redact(data)
    assert (
        shown["ok"] == 1
        and shown[key] == MASK
        and shown["liste"][0][key] == MASK
        and shown["liste"][0]["visible"] == "oui"
    )
    assert data[key] == "valeur"  # the original is never altered


def test_an_expression_that_reads_a_sensitive_field_shows_a_mask_but_keeps_numbers_and_booleans():
    assert debug.redact_for_expr("steps.a.output.password", "abc") == MASK
    assert debug.redact_for_expr("steps.a.output.token", {"x": 1}) == MASK
    assert debug.redact_for_expr("len(steps.a.output.password) > 3", True) is True
    assert (
        debug.redact_for_expr("steps.a.output.n", 5) == 5
        and debug.redact_for_expr("steps.a.output.token", None) is None
    )
    assert debug.redact_for_expr("steps.a.output.x", {"secret": "s", "n": 1}) == {"secret": MASK, "n": 1}


def _step(nid, status, output):
    return SimpleNamespace(node_id=nid, status=status, output=output)


class FakeRun:
    def __init__(self):
        self.id, self.inputs = "r1", {"n": 2}
        self.environment = SimpleNamespace(kind="prod")
        self.created_at = datetime(2026, 1, 2, tzinfo=UTC)
        steps = [
            _step("a", "succeeded", {"v": 1}),
            _step("b", "failed", {"v": 2}),
            _step("c", "succeeded", "pas un objet"),
        ]
        self.steps = SimpleNamespace(all=lambda: steps)


def test_the_context_comes_from_the_last_run_and_pins_win_over_it():
    graph = {
        "pins": {"a": {"v": 99}, "z": {"v": 3}, "bad": "x"},
        "variables": [
            {"name": "seuil", "value": 1, "env_values": {"prod": 7}},
            {"name": "autre", "value": 5},
            "ignoré",
            {"sans": "nom"},
        ],
    }
    ctx, where = debug.build_context(graph, FakeRun())
    assert ctx["steps"] == {
        "a": {"output": {"v": 99}},
        "z": {"output": {"v": 3}},
    }  # failed step and non-object output are not data
    assert where == {"a": "pin", "z": "pin"}
    assert ctx["vars"] == {"seuil": 7, "autre": 5} and ctx["inputs"] == {"n": 2}
    assert ctx["run"] == {"id": "r1", "started_at": "2026-01-02T00:00:00+00:00", "environment": "prod"}


def test_without_a_run_the_context_is_a_fixed_example_with_the_dev_values():
    ctx, where = debug.build_context({"variables": [{"name": "v", "value": 1, "env_values": {"dev": 4}}]})
    assert (
        ctx["inputs"] == {}
        and ctx["run"]["id"] == "essai"
        and ctx["run"]["environment"] == "dev"
        and ctx["vars"] == {"v": 1}
    )
    assert where == {}


def test_an_expression_or_a_template_is_previewed_with_its_error_when_it_fails():
    ctx = {"inputs": {"n": 3}, "steps": {}}
    assert debug.evaluate_preview("inputs.n * 2", ctx) == {"ok": True, "value": 6}
    assert debug.evaluate_preview("n = {{ inputs.n }}", ctx) == {"ok": True, "value": "n = 3"}
    assert debug.evaluate_preview("item.x + 1", ctx, item={"x": 1}) == {"ok": True, "value": 2}
    bad = debug.evaluate_preview("inputs.absent.x", ctx)
    assert bad["ok"] is False and bad["error"]


def test_a_huge_preview_is_cut():
    ctx = {"inputs": {"t": "x" * (debug.MAX_PREVIEW_BYTES + 10)}, "steps": {}}
    out = debug.evaluate_preview("inputs.t", ctx)
    assert (
        out["ok"] is True
        and out["value"]["truncated"] is True
        and len(out["value"]["preview"]) == debug.MAX_PREVIEW_BYTES
    )


def test_pure_steps_are_computed_and_others_answer_from_their_pin_or_last_output():
    ctx = {"inputs": {"n": 5}, "steps": {"a": {"output": {"v": 1}}, "b": {"output": {"v": 2}}}}
    where = {"a": "pin", "b": "run"}
    assert debug.test_step({"id": "c", "type": "condition", "expr": "inputs.n > 3"}, ctx, where)["output"] == {
        "result": True
    }
    assert debug.test_step({"id": "c", "type": "condition", "expr": "inputs.nul.x"}, ctx, where)["ok"] is False
    sw = {
        "id": "s",
        "type": "switch",
        "cases": [{"id": "x", "expr": "inputs.n > 9"}, {"id": "y", "expr": "inputs.n > 1"}],
    }
    assert debug.test_step(sw, ctx, where)["output"] == {"result": "y"}
    assert debug.test_step({"id": "s", "type": "switch", "cases": [{"id": "x", "expr": "inputs.n > 9"}]}, ctx, where)[
        "output"
    ] == {"result": "default"}
    broken = debug.test_step({"id": "s", "type": "switch", "cases": [{"id": "x", "expr": "inputs.nul.x"}]}, ctx, where)
    assert broken["ok"] is False and "Cas « x »" in broken["error"]
    tr = debug.test_step(
        {"id": "t", "type": "transform", "op": "limit", "items": "inputs.l", "count": 1},
        {**ctx, "inputs": {"l": [1, 2]}},
        where,
    )
    assert tr["ok"] is True and tr["output"]["items"] == [1]
    assert debug.test_step({"id": "t", "type": "transform", "op": "zip"}, ctx, where)["ok"] is False
    assert debug.test_step({"id": "a", "type": "http"}, ctx, where)["source"] == "épinglée"
    assert debug.test_step({"id": "b", "type": "http"}, ctx, where)["source"] == "dernière exécution"
    assert debug.test_step({"id": "inconnu", "type": "http"}, ctx, where)["ok"] is False
