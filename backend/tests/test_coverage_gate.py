"""Seuil de couverture (scripts/coverage_report.py, `--gate`) : logique pure sur des mesures synthétiques, sans lancer la suite."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "coverage_report", Path(__file__).resolve().parents[2] / "scripts" / "coverage_report.py"
)
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)


def measure(total, **packages):
    return {"total": {"percent": total}, "packages": {k: {"percent": v} for k, v in packages.items()}}


BASE = measure(90.0, runs=90.0, vault=88.0)


def test_nominal_no_drop_passes():
    assert cr.gate(BASE, measure(90.0, runs=90.0, vault=88.0), True, False) == []


def test_an_improvement_or_a_small_drop_within_the_margin_passes():
    assert cr.gate(BASE, measure(91.0, runs=92.0, vault=88.0), True, False) == []
    assert cr.gate(BASE, measure(89.8, runs=89.2, vault=87.5), True, False) == []


def test_a_total_drop_beyond_the_margin_fails_and_names_it():
    reasons = cr.gate(BASE, measure(89.5, runs=90.0, vault=88.0), True, False)
    assert len(reasons) == 1 and reasons[0].startswith("TOTAL : 89.50 %")


def test_a_package_drop_beyond_its_margin_fails_even_if_the_total_holds():
    reasons = cr.gate(BASE, measure(90.0, runs=88.5, vault=88.0), True, False)
    assert len(reasons) == 1 and reasons[0].startswith("runs : 88.50 %")


def test_a_new_package_is_not_compared_and_a_missing_baseline_refuses_to_judge():
    assert cr.gate(BASE, measure(90.0, runs=90.0, vault=88.0, nouveau=10.0), True, False) == []
    assert cr.gate(None, measure(90.0), True, False) == ["aucune baseline : le seuil ne peut pas être évalué"]


def test_a_failed_suite_or_a_partial_run_never_counts_as_a_passing_measure():
    assert "n'est pas passée entièrement" in cr.gate(BASE, measure(95.0, runs=95.0, vault=95.0), False, False)[0]
    assert "mesure partielle" in cr.gate(BASE, measure(95.0), True, True)[0]
