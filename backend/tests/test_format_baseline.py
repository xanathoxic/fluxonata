"""Comparaison à la baseline de mise en forme du frontend (scripts/format_frontend_baseline.py) : logique pure, sans lancer Biome."""

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "format_frontend_baseline", _ROOT / "scripts" / "format_frontend_baseline.py"
)
fb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fb)


def test_nominal_no_difference():
    assert fb.compare({"a.ts", "b.ts"}, {"a.ts", "b.ts"}) == ([], [])


def test_a_new_or_previously_conforming_file_that_is_not_conforming_fails():
    assert fb.compare({"a.ts", "neuf.ts"}, {"a.ts"}) == (["neuf.ts"], [])


def test_a_file_that_became_conforming_passes_and_is_reported():
    assert fb.compare({"a.ts"}, {"a.ts", "b.ts"}) == ([], ["b.ts"])


def test_the_committed_baseline_lists_existing_files_only():
    data = json.loads(fb.BASELINE.read_text())
    assert data["fichiers"] and all((fb.FRONTEND / f).is_file() for f in data["fichiers"])
