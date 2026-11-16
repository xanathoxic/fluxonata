"""Comparaison à la baseline de complexité (scripts/complexity_baseline.py) : logique pure, sans lancer Ruff, plus le gel de l'état réel."""

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("complexity_baseline", _ROOT / "scripts" / "complexity_baseline.py")
cb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cb)

BASE = {"a.py::f": 15, "b.py::g": 12}


def test_nominal_no_difference():
    assert cb.compare({"a.py::f": 15, "b.py::g": 12}, BASE) == ([], [])


def test_a_new_function_above_the_limit_fails():
    failures, better = cb.compare({**BASE, "c.py::h": 11}, BASE)
    assert failures == ["nouvelle fonction trop complexe : c.py::h (11)"] and better == []


def test_a_function_getting_more_complex_fails_even_if_it_was_already_above():
    failures, _ = cb.compare({"a.py::f": 16, "b.py::g": 12}, BASE)
    assert failures == ["complexité en hausse : a.py::f (15 -> 16)"]


def test_a_decrease_or_a_disappearance_passes_and_is_reported():
    failures, better = cb.compare({"a.py::f": 14}, BASE)
    assert failures == [] and better == ["a.py::f", "b.py::g"]


def test_the_message_of_ruff_is_understood():
    m = cb.MESSAGE.search("`evaluate_ast` is too complex (25 > 10)")
    assert (m["name"], m["score"]) == ("evaluate_ast", "25")


def test_the_committed_baseline_only_lists_functions_above_the_limit():
    data = json.loads((_ROOT / "backend" / "tests" / "fixtures" / "complexity_baseline.json").read_text())
    assert data["functions"] and all(v > 10 for v in data["functions"].values())
