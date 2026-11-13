"""Comparaison à la baseline du linter frontend (scripts/lint_frontend_baseline.py) : logique pure, sans lancer biome."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "lint_frontend_baseline", Path(__file__).resolve().parents[2] / "scripts" / "lint_frontend_baseline.py"
)
lb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lb)

BASE = {"bloquants": ["src/workflow/"], "fichiers": {"src/a.tsx": 2, "src/workflow/b.ts": 0}}


def _sortie(diags, **resume):
    base = {"errors": 0, "warnings": 0, "infos": 0, "unchanged": 10, "changed": 0, "diagnosticsNotPrinted": 0}
    for d in diags:
        base["errors" if d["severity"] == "error" else "warnings"] += 1
    return json.dumps({"summary": base | resume, "diagnostics": diags})


def _diag(path, rule="lint/x/y", severity="error"):
    return {"category": rule, "severity": severity, "location": {"path": path}}


def test_analyse_nominale_compte_par_fichier_et_par_regle():
    out = _sortie([_diag("src/a.tsx"), _diag("src/a.tsx", "lint/x/z"), _diag("src/b.tsx", severity="warning")])
    par_fichier, par_regle = lb.analyser(out, 1)
    assert par_fichier == Counter({"src/a.tsx": 2, "src/b.tsx": 1})
    assert par_regle == Counter({"lint/x/y": 2, "lint/x/z": 1})


def test_analyse_propre():
    assert lb.analyser(_sortie([]), 0) == (Counter(), Counter())


@pytest.mark.parametrize(
    ("stdout", "statut"),
    [
        ("", 2),  # panne
        ("pas du json", 1),  # sortie illisible
        ("{}", 1),  # bilan absent
        (_sortie([], unchanged=0), 0),  # aucun fichier lu
        (_sortie([_diag("src/a.tsx")], errors=3), 1),  # bilan annoncé incohérent
        (_sortie([_diag("src/a.tsx")], diagnosticsNotPrinted=4), 1),  # diagnostics non affichés
        (_sortie([_diag("src/a.tsx")]), 0),  # erreur annoncée mais statut 0
    ],
)
def test_panne_ou_bilan_incoherent_ne_sont_jamais_une_absence_de_regression(stdout, statut):
    with pytest.raises(lb.LinterIndisponible):
        lb.analyser(stdout, statut)


def test_comparaison_nominale_baisse_hausse_nouveau_et_perimetre_bloquant():
    assert lb.comparer(Counter({"src/a.tsx": 2}), BASE) == ([], [])
    assert lb.comparer(Counter({"src/a.tsx": 1}), BASE) == ([], [])
    dur, doux = lb.comparer(Counter({"src/a.tsx": 3}), BASE)
    assert dur == [] and len(doux) == 1
    dur, _ = lb.comparer(Counter({"src/nouveau.tsx": 1}), BASE)
    assert len(dur) == 1
    dur, _ = lb.comparer(Counter({"src/workflow/b.ts": 1}), BASE)
    assert len(dur) == 1


def test_main_echoue_avec_le_code_2_quand_biome_est_inexploitable(monkeypatch, capsys):
    def panne():
        raise lb.LinterIndisponible("panne simulée")

    monkeypatch.setattr(lb, "lancer_biome", panne)
    assert lb.main([]) == 2
    assert "inexploitable" in capsys.readouterr().err


def test_baseline_reelle_est_coherente():
    base = json.loads(lb.BASELINE.read_text(encoding="utf-8"))
    assert all(n > 0 for n in base["fichiers"].values())
    assert sum(base["fichiers"].values()) == sum(base["par_regle"].values())
