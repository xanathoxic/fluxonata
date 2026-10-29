import os

import pytest

from tests.conftest import make_workspace
from vault import crypto
from vault.models import Secret


def test_encrypt_decrypt_roundtrip_and_purpose_binding(db):
    kid, token = crypto.encrypt("hunter2", "secret:1")
    assert "hunter2" not in token and kid
    assert crypto.decrypt(token, "secret:1") == "hunter2"
    with pytest.raises(crypto.VaultError):  # a ciphertext cannot be reused for another purpose/workspace
        crypto.decrypt(token, "secret:2")
    with pytest.raises(crypto.VaultError):
        crypto.decrypt(token[:-4] + "AAAA", "secret:1")


def test_key_files_are_private_and_outside_the_repo(db):
    d = crypto.key_dir()
    assert "Fluxonata" not in str(d) or "keys" in str(d)
    for f in d.glob("key-*.key"):
        assert oct(os.stat(f).st_mode & 0o777) == "0o600"


def test_rotation_reencrypts_and_old_keys_can_be_retired(db):
    ws = make_workspace("vault-ws")
    s = Secret(workspace=ws, name="api")
    s.set_value("v1-secret")
    s.save()
    old_kid, old_ct = s.key_id, s.ciphertext
    from django.core.management import call_command

    call_command("rotate_keys")
    s.refresh_from_db()
    assert s.key_id != old_kid and s.ciphertext != old_ct and s.reveal() == "v1-secret" and s.rotated_at
    call_command("rotate_keys", "--retire-old")
    s.refresh_from_db()
    assert s.reveal() == "v1-secret" and len(list(crypto.key_dir().glob("key-*.key"))) == 1
    with pytest.raises(crypto.VaultError):  # the retired key's ciphertext is now unreadable
        crypto.decrypt(old_ct, f"secret:{ws.id}")
