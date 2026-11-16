"""Comportements rendus explicites pendant le typage de `triggers` : cas limites qui levaient une erreur interne."""

from types import SimpleNamespace

import pytest

from triggers import formcalc, formrules, lookups, poller, scheduler
from triggers.models import FormLookup, Schedule


def test_condition_dont_la_reference_n_est_pas_un_texte_est_refusee_proprement():
    for ref in (["a"], {"x": 1}, 3, None):
        cond = {"field": ref, "op": "eq", "value": "x"}
        problems = formrules._check_cond(cond, "rules.0", {"a": {"type": "text"}}, [0])
        assert problems == {"rules.0.field": "Une condition ne peut lire qu'un autre champ du formulaire."}


def test_condition_sur_un_champ_connu_reste_acceptee():
    cond = {"field": "a", "op": "gt", "value": 1}
    assert formrules._check_cond(cond, "rules.0", {"a": {"type": "text"}}, [0]) == {}


@pytest.mark.parametrize(
    ("op", "left", "right", "expected"),
    [
        ("gt", 3, 2, True),
        ("gte", 2, 2, True),
        ("lt", 3, 2, False),
        ("lte", "2026-01-01", "2026-01-02", True),  # dates ISO : ordre du texte
        ("gt", "abc", "abd", False),  # ni nombres ni dates : jamais vrai
    ],
)
def test_comparaisons_d_ordre_des_regles(op, left, right, expected):
    assert formrules.eval_cond({"field": "a", "op": op, "value": right}, {"a": left}) is expected


@pytest.mark.parametrize(
    ("op", "a", "b", "expected"),
    [("<", 1, 2, True), (">=", 2, 2, True), (">", "2026-02-01", "2026-01-01", True), ("<", "x", "y", False)],
)
def test_comparaisons_d_ordre_du_calcul(op, a, b, expected):
    assert formcalc._order(op, a, b) is expected


def test_recherche_sans_formulaire_ni_version_est_refusee_explicitement():
    with pytest.raises(ValueError, match="ni à un formulaire ni à une version"):
        lookups._context(FormLookup())


def test_planification_sans_derniere_execution_n_a_aucune_execution_active():
    assert scheduler.active_count(Schedule()) == 0


def test_interrogation_sans_reponse_du_service_donne_un_message(monkeypatch):
    monkeypatch.setattr(poller.http_node, "prepare", lambda *a, **k: {})
    monkeypatch.setattr(poller.http_node, "execute_spec", lambda *a, **k: (None, None))
    t = SimpleNamespace(source="http", request={}, workspace_id=1, environment_id=1, workspace=None)
    assert poller._fetch(t) == (None, "Le service n'a renvoyé aucune réponse.")
