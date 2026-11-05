"""AR2.5 — la clé du coffre ne se fabrique jamais toute seule en production (un process sans le volume des clés échouerait en silence autrement)."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from vault import crypto


@pytest.fixture
def keys(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("ORCH_KEY_DIR", str(tmp_path / "keys"))
    return tmp_path / "keys"


def test_default_keeps_the_development_convenience(keys, settings):
    settings.ORCH_KEY_AUTOCREATE = True
    kid, token = crypto.encrypt("secret", "p")
    assert (keys / "current").read_text() == kid and crypto.decrypt(token, "p") == "secret"


def test_in_production_a_missing_key_directory_is_an_explicit_error_and_nothing_is_created(keys, settings):
    settings.ORCH_KEY_AUTOCREATE = False
    with pytest.raises(crypto.VaultError, match="volume des clés n'est pas monté"):
        crypto.encrypt("secret", "p")
    with pytest.raises(crypto.VaultError, match="volume des clés n'est pas monté"):
        crypto.decrypt("x", "p")
    assert not keys.exists()  # aucun dossier fabriqué


def test_in_production_an_empty_but_mounted_directory_does_not_get_a_surprise_key(keys, settings):
    keys.mkdir(mode=0o700)
    settings.ORCH_KEY_AUTOCREATE = False
    with pytest.raises(crypto.VaultError, match="vault_init"):
        crypto.encrypt("secret", "p")
    assert list(keys.iterdir()) == []


def test_vault_init_creates_the_first_key_once_and_never_touches_an_existing_one(keys, settings, capsys):
    settings.ORCH_KEY_AUTOCREATE = False
    call_command("vault_init")
    first = (keys / "current").read_text()
    material = (keys / f"key-{first}.key").read_bytes()
    assert "créée" in capsys.readouterr().out and (keys / f"key-{first}.key").stat().st_mode & 0o777 == 0o600
    call_command("vault_init")  # rejouable
    assert (keys / "current").read_text() == first and (keys / f"key-{first}.key").read_bytes() == material
    assert "déjà présente" in capsys.readouterr().out
    token = crypto.encrypt("secret", "p")[1]  # et le coffre fonctionne maintenant, sans création automatique
    assert crypto.decrypt(token, "p") == "secret"


def test_vault_init_never_prints_key_material(keys, settings, capsys):
    call_command("vault_init")
    out = capsys.readouterr().out
    kid = (keys / "current").read_text()
    assert (keys / f"key-{kid}.key").read_text().strip() not in out


def test_vault_init_refuses_to_guess_when_keys_exist_without_a_current_one(keys, settings):
    keys.mkdir(mode=0o700)
    (keys / "key-aaaa1111.key").write_bytes(b"x" * 44)
    with pytest.raises(CommandError, match="aucune n'est désignée"):
        call_command("vault_init")
    assert not (keys / "current").exists()  # rien d'écrasé ni deviné


@pytest.mark.django_db
def test_bootstrap_creates_the_first_key_explicitly(keys, settings, monkeypatch):
    settings.ORCH_KEY_AUTOCREATE = False
    monkeypatch.setenv("ADMIN_PW", "un-mot-de-passe-long-123")
    call_command("bootstrap", workspace="principal", password_env="ADMIN_PW")
    assert (keys / "current").exists()
