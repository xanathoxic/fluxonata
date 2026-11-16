"""Comparaison à la baseline de typage (scripts/mypy_baseline.py) : logique pure, sans lancer mypy."""

import importlib.util
from collections import Counter
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mypy_baseline", Path(__file__).resolve().parents[2] / "scripts" / "mypy_baseline.py"
)
mb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mb)

BASE = {"bloquants": ["runs/"], "fichiers": {"ai/a.py": 2, "runs/b.py": 0}}


def test_comptage_ignore_les_lignes_qui_ne_sont_pas_des_erreurs():
    sortie = "ai/a.py:3: error: x  [arg-type]\nai/a.py:9:5: error: y  [index]\nai/a.py:9: note: z\nFound 2 errors"
    assert mb.compter(sortie) == Counter({"ai/a.py": 2})


def test_nominal_aucun_ecart():
    assert mb.comparer(Counter({"ai/a.py": 2}), BASE) == ([], [])


def test_baisse_acceptee():
    assert mb.comparer(Counter({"ai/a.py": 1}), BASE) == ([], [])


def test_hausse_dans_la_baseline_est_transitoire():
    dur, doux = mb.comparer(Counter({"ai/a.py": 3}), BASE)
    assert dur == [] and len(doux) == 1


def test_fichier_nouveau_avec_erreur_est_bloquant():
    dur, _ = mb.comparer(Counter({"ai/nouveau.py": 1}), BASE)
    assert len(dur) == 1 and "nouveau.py" in dur[0]


def test_tranche_bloquante_exige_zero_erreur():
    dur, _ = mb.comparer(Counter({"runs/b.py": 1}), BASE)
    assert len(dur) == 1


def test_limite_baseline_reelle_coherente_avec_ses_comptes():
    import json

    base = json.loads(mb.BASELINE.read_text(encoding="utf-8"))
    assert all(n > 0 for n in base["fichiers"].values())


def test_bilan_propre_et_bilan_avec_erreurs_sont_acceptes():
    assert mb.analyser("Success: no issues found in 200 source files\n", "", 0) == Counter()
    sortie = "ai/a.py:3: error: x  [arg-type]\nFound 1 error in 1 file (checked 200 source files)\n"
    assert mb.analyser(sortie, "", 1) == Counter({"ai/a.py": 1})


def test_panne_ou_configuration_invalide_ne_sont_jamais_une_absence_de_regression():
    import pytest

    cas = [
        ("", "mypy.ini: [mypy]: Invalid value", 2),  # configuration ou usage invalide
        ("", "", 0),  # aucune sortie
        ("Success: no issues found in 0 source files\n", "", 0),  # aucun fichier lu
        (
            "mypy.ini: error: Cannot find module\nFound 1 error in 1 file (checked 200 source files)\n",
            "",
            1,
        ),  # erreur sans fichier
        ("ai/a.py:3: error: x  [arg-type]\nSuccess: no issues found in 200 source files\n", "", 0),  # incohérent
        ("Success: no issues found in 200 source files\n", "", 1),  # statut incohérent
    ]
    for stdout, stderr, statut in cas:
        with pytest.raises(mb.MypyIndisponible):
            mb.analyser(stdout, stderr, statut)


def test_main_echoue_avec_le_code_2_quand_mypy_est_inexploitable(monkeypatch, capsys):
    def panne():
        raise mb.MypyIndisponible("panne simulée")

    monkeypatch.setattr(mb, "lancer_mypy", panne)
    assert mb.main([]) == 2
    assert "inexploitable" in capsys.readouterr().err
