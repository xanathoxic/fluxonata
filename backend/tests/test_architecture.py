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


def test_no_import_time_cycle_between_files():
    """Le « cycle » gelé entre applications n'est pas un cycle d'import : entre fichiers, les imports de tête (exécutés au
    chargement) forment un graphe SANS cycle. Un cycle de ce type provoquerait une ImportError ou un ordre de chargement fragile."""
    ig = _load()
    top_cycles, _ = ig.file_cycles()
    assert top_cycles == [], (
        f"cycle d'import au chargement entre fichiers : {top_cycles}. Différez l'import dans la fonction ou extrayez la dépendance commune."
    )


def test_file_cycles_through_deferred_imports_never_grow():
    """Les 5 petits cycles entre fichiers passent tous par un import local à une fonction (différé) ; aucun fichier de plus n'y entre."""
    ig = _load()
    frozen = json.loads(ig.BASELINE.read_text(encoding="utf-8"))["cycles_fichiers_avec_imports_locaux"]
    now = ig.file_cycles()[1]
    frozen_files = {f for comp in frozen for f in comp}
    new_files = {f for comp in now for f in comp} - frozen_files
    assert not new_files, f"fichiers entrés dans un cycle d'imports différés : {sorted(new_files)}. {HELP}"
    assert len(now) <= len(frozen), f"nouveau cycle entre fichiers : {now}. {HELP}"


def test_the_file_graph_tells_deferred_imports_from_load_time_ones():
    """Contrôle du détecteur : `formcontrols` importe `formengine` seulement dans des fonctions, `formengine` importe `formcontrols` en tête."""
    ig = _load()
    top, deferred = ig.file_graph()
    assert "triggers.formcontrols" in top["triggers.formengine"]
    assert "triggers.formengine" not in top.get("triggers.formcontrols", set())
    assert "triggers.formengine" in deferred["triggers.formcontrols"]
