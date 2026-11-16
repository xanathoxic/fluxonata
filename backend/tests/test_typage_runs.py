"""Comportements rendus explicites pendant le typage de `runs` : états inattendus qui levaient une erreur interne."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from runs import http_node


def test_etape_http_sans_requete_est_refusee_explicitement():
    with pytest.raises(ValueError, match="pas de requête"):
        http_node.execute(SimpleNamespace(http_job=None))


@pytest.mark.parametrize("job", [{}, {"claimed_at": None}, {"claimed_at": ""}])
def test_bail_sans_date_de_prise_n_est_pas_expire(job):
    assert http_node.lease_expired(job) is False


def test_bail_recent_non_expire_et_ancien_expire():
    recent = {"claimed_at": timezone.now().isoformat()}
    ancien = {"claimed_at": (timezone.now() - http_node.LEASE - timedelta(seconds=5)).isoformat()}
    assert http_node.lease_expired(recent) is False
    assert http_node.lease_expired(ancien) is True


def test_pagination_dont_l_envoi_ne_renvoie_rien_donne_une_erreur_lisible(monkeypatch):
    monkeypatch.setattr(http_node, "_send", lambda *a, **k: (None, None, {}))
    spec = {"pagination": {"type": "page", "max_pages": 2}}
    result, error = http_node._paginate(spec, "https://exemple.test/x", {}, [])
    assert result is None
    assert error["code"] == "HTTP_ERROR" and error["message"] == "Aucune réponse reçue."


def test_pagination_nominale_apres_le_garde(monkeypatch):
    page = {"ok": True, "status": 200, "body": [1, 2]}
    monkeypatch.setattr(http_node, "_send", lambda *a, **k: (dict(page), None, {}))
    spec = {"pagination": {"type": "page", "max_pages": 1}}
    result, error = http_node._paginate(spec, "https://exemple.test/x", {}, [])
    assert error is None and result["body"] == [1, 2]
