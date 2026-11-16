"""Comportements précisés pendant le typage de `ai` : cas limites des sorties de modèle et des opérations."""

import pytest

from ai import ops, service
from ai.providers import FakeProvider


def test_helpers_de_sortie_de_modele():
    assert service._list_of({"a": [1]}, "a") == [1]
    assert service._list_of({"a": "x"}, "a") == []
    assert service._list_of({}, "a") == []
    assert service._dict_of({"a": {"k": 1}}, "a") == {"k": 1}
    assert service._dict_of({"a": [1]}, "a") == {}
    assert service._dict_of({"a": None}, "a") == {}


@pytest.mark.parametrize("bad", [None, 3, ["x"], {"a": 1}])
def test_operation_sans_nom_textuel_est_inconnue(bad):
    _, errors = ops.apply_ops({"nodes": [], "edges": []}, [{"op": bad}])
    assert len(errors) == 1 and "est inconnue" in errors[0]


def test_suppression_de_variable_sans_nom_textuel_est_refusee():
    graph = {"nodes": [], "edges": [], "variables": [{"name": "seuil", "type": "integer", "value": 1}]}
    for name in (None, 3):
        _, errors = ops.apply_ops(graph, [{"op": "remove_variable", "name": name}])
        assert len(errors) == 1 and "n'existe pas" in errors[0]
    new, errors = ops.apply_ops(graph, [{"op": "remove_variable", "name": "seuil"}])
    assert errors == [] and new["variables"] == []


def test_fournisseur_de_test_sans_balise_de_contexte_fermee_ne_plante_pas():
    out = FakeProvider().complete("s", "<task>explain_error</task><context>{", max_tokens=100, timeout=5)
    assert out is not None


def test_nom_unique_accepte_un_nom_absent(world):
    from catalog.models import Action

    slug, renamed = service._unique_slug(Action, world["ws_b"], None, "mon action")
    assert slug == "mon-action" and renamed is True
