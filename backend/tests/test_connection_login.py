"""`Connection.login` : identifiant et secret ; une connexion sans secret est refusée explicitement (les appelants
vérifient déjà `conn.secret is None` avant d'appeler)."""

import pytest

from vault.models import Connection, Secret


def _secret(ws, value="valeur-de-test-1234"):
    s = Secret(workspace=ws, name="s-login")
    s.set_value(value)
    s.save()
    return s


def test_login_renvoie_l_identifiant_de_la_configuration_et_le_secret(world):
    ws = world["ws_b"]
    conn = Connection.objects.create(
        workspace=ws, name="c1", label="C1", secret=_secret(ws), config={"username": "alice", "bind_dn": "cn=x"}
    )
    assert conn.login() == ("alice", "valeur-de-test-1234")
    assert conn.login("bind_dn") == ("cn=x", "valeur-de-test-1234")


def test_login_sans_identifiant_configure_renvoie_une_chaine_vide(world):
    ws = world["ws_b"]
    conn = Connection.objects.create(workspace=ws, name="c2", label="C2", secret=_secret(ws), config={})
    assert conn.login() == ("", "valeur-de-test-1234")


def test_login_sans_secret_est_refuse_explicitement(world):
    conn = Connection.objects.create(workspace=world["ws_b"], name="c3", label="C3", secret=None)
    with pytest.raises(ValueError, match="pas de secret"):
        conn.login()
