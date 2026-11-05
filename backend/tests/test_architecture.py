"""AR0.1 — garde-fou d'architecture : le graphe d'imports entre modules ne change que volontairement.

Pas de base de données. Voir `scripts/import_graph.py` et `docs/ROADMAP_ARCHITECTURE.md`.
"""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_graph.py"
HELP = "Si c'est voulu : `python scripts/import_graph.py --write`, et justifier la dépendance dans le commit (ADR si elle franchit un rôle)."


def _load():
    spec = importlib.util.spec_from_file_location("import_graph", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_import_graph_matches_frozen_baseline():
    ig = _load()
    frozen = json.loads(ig.BASELINE.read_text(encoding="utf-8"))
    added, removed = ig.diff(ig.snapshot(), frozen)
    assert not added, f"nouvelle(s) dépendance(s) entre modules : {added}. {HELP}"
    assert not removed, f"dépendance(s) retirée(s) (à verrouiller) : {removed}. {HELP}"


def test_cycle_groups_never_grow():
    """Les cycles existants sont connus et gelés ; un module de plus dans un cycle, ou un nouveau cycle, échoue."""
    ig = _load()
    frozen = json.loads(ig.BASELINE.read_text(encoding="utf-8"))
    now = ig.snapshot()["cycles"]
    frozen_members = {m for comp in frozen["cycles"] for m in comp}
    new_members = {m for comp in now for m in comp} - frozen_members
    assert not new_members, f"modules entrés dans un cycle d'imports : {sorted(new_members)}. {HELP}"


def test_audit_stays_a_leaf():
    """`audit` est la brique la plus basse : elle n'importe aucun autre module (invariant à ne pas perdre)."""
    ig = _load()
    assert not [e for e in ig.edges() if e[0] == "audit"]


def test_detects_a_new_cycle():
    """Le détecteur lui-même : A -> B -> C -> A est un cycle, A -> B -> C n'en est pas un."""
    ig = _load()
    assert ig.cycles([("a", "b"), ("b", "c"), ("c", "a")]) == [["a", "b", "c"]]
    assert ig.cycles([("a", "b"), ("b", "c")]) == []
