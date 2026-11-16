"""Modifier une connexion : libellé, paramètres et secret (garder, choisir un existant, en créer un). Mêmes contrôles qu'à la création ; l'identifiant,
le type et l'environnement ne changent pas ; aucune valeur de secret dans les réponses ni l'audit (valeurs sentinelles synthétiques)."""

import pytest
from rest_framework.test import APIClient

from audit.models import AuditEvent
from tenancy.models import Membership, Role
from tests.ssh_fake import new_key
from tests.ssh_helpers import make_secret
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a/connections/"
OLD, NEW, OTHER = "valeur-ancienne-1111", "valeur-nouvelle-2222", "valeur-autre-3333"
FP = "SHA256:" + "A" * 43


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def pg(world):
    ws = world["ws_a"]
    return Connection.objects.create(
        workspace=ws,
        name="base",
        label="Base",
        kind="postgres",
        secret=make_secret(ws, "base-all", OLD),
        config={"host": "db.exemple.test", "database": "x", "username": "lecteur"},
        last_test_ok=True,
        last_test_message="ok",
    )


def patch(user, conn, body):
    return client(user).patch(f"{BASE}{conn.pk}/", body, format="json")


def test_the_label_and_the_settings_change_and_the_old_test_result_is_cleared(pg, world):
    r = patch(
        world["designer"],
        pg,
        {"label": "Base principale", "config": {"host": "db2.exemple.test", "database": "y", "username": "lecteur"}},
    )
    assert r.status_code == 200, r.content
    pg.refresh_from_db()
    assert pg.label == "Base principale" and pg.config["host"] == "db2.exemple.test" and pg.name == "base"
    assert pg.last_test_ok is None and pg.last_test_message == ""  # what was tested is no longer what is saved
    assert pg.secret.name == "base-all"  # untouched: no secret was sent


def test_the_same_checks_as_creation_apply_to_the_settings(pg, world):
    r = patch(world["designer"], pg, {"config": {"host": "", "database": "y", "username": "lecteur"}})
    assert r.status_code == 400 and "host" in str(r.json())
    pg.refresh_from_db()
    assert pg.config["host"] == "db.exemple.test"


@pytest.mark.parametrize("body", [{"name": "autre"}, {"kind": "mysql"}, {"environment": "prod"}])
def test_identifier_type_and_environment_never_change(pg, world, body):
    r = patch(world["designer"], pg, body)
    assert r.status_code == 400 and "créez une autre connexion" in str(r.json())
    assert (
        patch(world["designer"], pg, {"name": "base", "kind": "postgres", "environment": None}).status_code == 200
    )  # sending the same value is fine


def test_rights_and_workspace_boundaries(pg, world):
    assert patch(world["viewer"], pg, {"label": "x"}).status_code == 403
    assert client(world["outsider"]).patch(f"{BASE}{pg.pk}/", {"label": "x"}, format="json").status_code in (403, 404)
    pg.refresh_from_db()
    assert pg.label == "Base"
    Membership.objects.filter(user=world["designer"]).update(role=Role.DESIGNER)
    assert patch(world["designer"], pg, {"label": "Base 2"}).status_code == 200


def test_choosing_an_existing_secret_links_it_and_leaves_the_previous_one(pg, world):
    ws = world["ws_a"]
    make_secret(ws, "partage", NEW)
    r = patch(world["designer"], pg, {"secret": "partage"})
    assert r.status_code == 200 and r.json()["secret"] == "partage"
    pg.refresh_from_db()
    assert (
        pg.secret.name == "partage" and Secret.objects.filter(workspace=ws, name="base-all").exists()
    )  # nothing is deleted
    assert pg.last_test_ok is None
    assert OLD not in str(r.json()) and NEW not in str(r.json())


def test_an_unknown_secret_or_one_of_another_workspace_is_refused(pg, world):
    make_secret(world["ws_b"], "etranger", OTHER)
    for name in ("n-existe-pas", "etranger"):
        r = patch(world["designer"], pg, {"secret": name})
        assert r.status_code == 400 and "inconnu" in str(r.json()).lower() and OTHER not in str(r.json())
    pg.refresh_from_db()
    assert pg.secret.name == "base-all"


def test_creating_a_new_secret_replaces_the_link_with_a_fresh_one(pg, world):
    ws = world["ws_a"]
    r = patch(world["designer"], pg, {"secret_value": NEW})
    assert r.status_code == 200, r.content
    pg.refresh_from_db()
    assert pg.secret.name != "base-all" and pg.secret.reveal() == NEW  # a new secret with the new value ...
    assert Secret.objects.get(workspace=ws, name="base-all").reveal() == OLD  # ... the old one is intact
    assert NEW not in str(r.json())


def test_a_new_secret_can_be_named_and_a_taken_name_is_refused(pg, world):
    ws = world["ws_a"]
    assert (
        patch(world["designer"], pg, {"secret_value": NEW, "new_secret_name": "mot-de-passe-base"}).status_code == 200
    )
    assert Secret.objects.filter(workspace=ws, name="mot-de-passe-base").exists()
    r = patch(world["designer"], pg, {"secret_value": OTHER, "new_secret_name": "mot-de-passe-base"})
    assert r.status_code == 400 and "existe déjà" in str(r.json())
    pg.refresh_from_db()
    assert pg.secret.reveal() == NEW  # the refused call changed nothing


def test_an_existing_secret_and_a_new_one_cannot_be_given_together(pg, world):
    make_secret(world["ws_a"], "partage", NEW)
    r = patch(world["designer"], pg, {"secret": "partage", "secret_value": OTHER})
    assert r.status_code == 400 and "pas les deux" in str(r.json())


def test_the_audit_names_what_changed_never_a_value(pg, world):
    patch(world["designer"], pg, {"label": "Autre", "secret_value": NEW})
    ev = AuditEvent.objects.filter(action="connection.update").latest("id")
    assert (
        ev.data["changed"] == ["label"]
        and ev.data["secret_changed"] is True
        and ev.data["previous_secret"] == "base-all"
    )
    for v in (OLD, NEW):
        assert v not in repr(ev.data)


# ---- SSH: an identity without a host keeps to private keys, whichever way the secret is chosen
@pytest.fixture
def ssh_conn(world):
    ws = world["ws_a"]
    _, pem = new_key()
    return Connection.objects.create(
        workspace=ws,
        name="srv",
        label="Serveur",
        kind="ssh",
        secret=make_secret(ws, "cle-srv", pem),
        config={"username": "robot", "host": "h.exemple.test", "host_key_sha256": FP},
    )


def test_removing_the_host_needs_a_private_key_whatever_the_secret_source(ssh_conn, world):
    ws = world["ws_a"]
    no_host = {"config": {"username": "robot"}}
    password = make_secret(ws, "mdp", "un-mot-de-passe-1234")
    assert (
        patch(world["designer"], ssh_conn, {**no_host, "secret": "mdp"}).status_code == 400
    )  # an existing password secret
    r = patch(world["designer"], ssh_conn, {**no_host, "secret_value": "un-mot-de-passe-1234"})
    assert (
        r.status_code == 400 and "clé privée" in str(r.json()) and "un-mot-de-passe-1234" not in str(r.json())
    )  # a new password
    ssh_conn.refresh_from_db()
    assert ssh_conn.config["host"] == "h.exemple.test" and password.name == "mdp"  # nothing changed
    assert patch(world["designer"], ssh_conn, no_host).status_code == 200  # the current secret IS a key: fine


def test_the_hostless_check_also_covers_a_new_key_and_the_creation_path(ssh_conn, world):
    _, pem = new_key()
    assert patch(world["designer"], ssh_conn, {"config": {"username": "robot"}, "secret_value": pem}).status_code == 200
    make_secret(world["ws_a"], "mdp2", "un-mot-de-passe-5678")
    r = client(world["designer"]).post(
        BASE,
        {"name": "id2", "label": "Identité", "kind": "ssh", "config": {"username": "robot"}, "secret": "mdp2"},
        format="json",
    )
    assert r.status_code == 400 and "clé privée" in str(
        r.json()
    )  # creating with an existing password secret is refused too


def test_adding_a_host_back_requires_its_fingerprint(ssh_conn, world):
    r = patch(world["designer"], ssh_conn, {"config": {"username": "robot", "host": "autre.exemple.test"}})
    assert r.status_code == 400 and "empreinte" in str(r.json())


def test_creation_can_name_the_new_secret(world):
    r = client(world["designer"]).post(
        BASE,
        {
            "name": "svc",
            "label": "Service",
            "kind": "http_bearer",
            "config": {"base_url": "https://8.8.8.8/"},
            "secret_value": OLD,
            "new_secret_name": "jeton-service",
        },
        format="json",
    )
    assert r.status_code == 201 and Secret.objects.filter(workspace=world["ws_a"], name="jeton-service").exists()
