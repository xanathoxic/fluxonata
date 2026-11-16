"""API des secrets et des connexions : refus (valeur absente, doublon, inconnu), sans jamais renvoyer de valeur de secret."""

import pytest
from rest_framework.test import APIClient

from tests.ssh_helpers import make_secret
from vault.models import Connection

WS = "/api/v1/workspaces/team-a"
SENTINEL = "valeur-sentinelle-9999"


def fields(r):
    """Les champs refusés, dans l'enveloppe d'erreur de l'API."""
    return r.json()["error"]["details"]


def api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _c(world, user_key="admin"):
    return api(world.get(user_key) or world["designer"])


def test_creating_a_secret_needs_a_value_and_a_free_name(world):
    c = _c(world)
    r = c.post(f"{WS}/secrets/", {"name": "api", "value": ""}, format="json")
    assert r.status_code == 400 and "value" in fields(r)
    ok = c.post(f"{WS}/secrets/", {"name": "api", "value": SENTINEL}, format="json")
    assert ok.status_code == 201 and SENTINEL not in ok.content.decode()
    dup = c.post(f"{WS}/secrets/", {"name": "api", "value": SENTINEL}, format="json")
    assert dup.status_code == 400 and "existe déjà" in dup.content.decode() and SENTINEL not in dup.content.decode()


def test_an_unknown_secret_is_a_404_for_every_verb(world):
    c = _c(world)
    assert c.put(f"{WS}/secrets/absent/", {"value": "x"}, format="json").status_code == 404
    assert c.delete(f"{WS}/secrets/absent/").status_code == 404
    assert c.post(f"{WS}/secrets/absent/test/").status_code == 404


def test_replacing_a_secret_needs_a_value(world):
    c = _c(world)
    c.post(f"{WS}/secrets/", {"name": "api", "value": SENTINEL}, format="json")
    r = c.put(f"{WS}/secrets/api/", {"value": ""}, format="json")
    assert r.status_code == 400 and "value" in fields(r)


def test_an_internal_secret_is_tested_without_any_call(world):
    c = _c(world)
    c.post(f"{WS}/secrets/", {"name": "api", "value": SENTINEL}, format="json")
    r = c.post(f"{WS}/secrets/api/test/")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["mode"] == "internal"
    assert SENTINEL not in r.content.decode()


def test_connection_creation_refusals(world):
    c = _c(world)
    base = {
        "name": "svc",
        "label": "Service",
        "kind": "http_bearer",
        "config": {"base_url": "https://93.184.216.34"},
    }
    r = c.post(f"{WS}/connections/", {**base, "environment": "nulle-part", "secret_value": "x"}, format="json")
    assert r.status_code == 400 and "environment" in fields(r)
    r = c.post(f"{WS}/connections/", {**base, "secret": "inconnu"}, format="json")
    assert r.status_code == 400 and "secret" in fields(r)
    ok = c.post(f"{WS}/connections/", {**base, "secret_value": SENTINEL}, format="json")
    assert ok.status_code == 201 and SENTINEL not in ok.content.decode()
    dup = c.post(f"{WS}/connections/", {**base, "secret_value": SENTINEL}, format="json")
    assert dup.status_code == 400 and "existe déjà" in dup.content.decode()


def test_unknown_connections_are_404(world):
    c = _c(world)
    assert c.patch(f"{WS}/connections/999999/", {"label": "x"}, format="json").status_code == 404
    assert c.delete(f"{WS}/connections/999999/").status_code == 404
    assert c.post(f"{WS}/connections/999999/test/").status_code == 404
    assert c.post(f"{WS}/connections/999999/oauth/start/").status_code == 404


def test_a_connection_without_secret_is_reported_untestable(world):
    ws = world["ws_a"]
    conn = Connection.objects.create(
        workspace=ws, name="nu", label="Nu", kind="http_bearer", config={"base_url": "https://x.test"}
    )
    r = _c(world).post(f"{WS}/connections/{conn.pk}/test/")
    assert r.status_code == 200 and r.json()["ok"] is False and "secret" in r.json()["message"].lower()
    conn.secret = make_secret(ws, "s", SENTINEL)
    conn.save()
    assert SENTINEL not in _c(world).post(f"{WS}/connections/{conn.pk}/test/").content.decode()


# ---- configuration de la plateforme (administrateur de plateforme)


@pytest.fixture
def root(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user("root", password="x", is_superuser=True)


def _config(**over):
    return {"address": "https://vault.exemple.test", "role_id": "r", "secret_id": "s", "enabled": False, **over}


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"kv_mount": "a b"}, "kv_mount"),
        ({"auth_mount": "../x"}, "auth_mount"),
        ({"base_prefix": "../etc"}, "base_prefix"),
        ({"ca_pem": "pas un certificat"}, "ca_pem"),
        ({"address": "http://vault.exemple.test"}, "address"),
        ({"enabled": True, "secret_id": ""}, "secret_id"),
    ],
)
def test_vault_configuration_refusals(world, root, over, field):
    r = api(root).put("/api/v1/platform/vault/", _config(**over), format="json")
    assert r.status_code == 400 and field in fields(r)


def test_vault_grants_are_validated(world, root):
    c = api(root)
    assert c.put("/api/v1/platform/vault/grants/absent/", {"grants": []}, format="json").status_code == 400
    r = c.put("/api/v1/platform/vault/grants/team-a/", {"grants": ["x"] * 60}, format="json")
    assert r.status_code == 400 and "grants" in fields(r)
    r = c.put("/api/v1/platform/vault/grants/team-a/", {"grants": [{"pattern": ""}]}, format="json")
    assert r.status_code == 400
    assert c.get("/api/v1/platform/vault/grants/").status_code == 200
