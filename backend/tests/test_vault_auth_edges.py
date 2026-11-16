"""Cas limites de `vault.auth` et `vault.injection` : secret absent ou illisible, serveur d'authentification en défaut, masquage."""

import json
from types import SimpleNamespace

import pytest

from core import netsec
from vault import auth, external, injection


def conn(kind="bearer", secret="s", config=None):
    sec = None if secret is None else SimpleNamespace(reveal=(secret if callable(secret) else lambda: secret))
    return SimpleNamespace(kind=kind, secret=sec, config=config or {}, pk=1)


def test_a_connection_without_secret_is_refused():
    with pytest.raises(auth.AuthError) as exc:
        auth._secret(conn(secret=None))
    assert exc.value.code == "CONNECTION_NO_SECRET"


def test_an_unreadable_secret_never_leaks_the_cause():
    def boom():
        raise RuntimeError("clé=abc123")

    with pytest.raises(auth.AuthError) as exc:
        auth._secret(conn(secret=boom))
    assert exc.value.code == "CONNECTION_SECRET_UNREADABLE" and "abc123" not in exc.value.message


def test_an_external_vault_failure_keeps_its_code():
    def down():
        raise external.ExternalVaultError("VAULT_DOWN", "coffre indisponible")

    with pytest.raises(auth.AuthError) as exc:
        auth._secret(conn(secret=down))
    assert (exc.value.code, exc.value.message) == ("VAULT_DOWN", "coffre indisponible")


def _reply(monkeypatch, status=200, raw=b"{}", exc=None):
    def fake(*a, **k):
        if exc:
            raise exc
        return status, {}, raw

    monkeypatch.setattr(netsec, "safe_request", fake)


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"exc": netsec.UnsafeURL("privée")}, "OAUTH_BLOCKED"),
        ({"exc": TimeoutError()}, "OAUTH_UNREACHABLE"),
        ({"raw": b"pas du json"}, "OAUTH_REFUSED"),
        ({"status": 400, "raw": json.dumps({"error": "invalid_client"}).encode()}, "OAUTH_REFUSED"),
        ({"raw": json.dumps({"no": "token"}).encode()}, "OAUTH_REFUSED"),
    ],
)
def test_token_request_failures(monkeypatch, kw, code):
    _reply(monkeypatch, **kw)
    with pytest.raises(auth.AuthError) as exc:
        auth._token_request(conn(config={"token_url": "https://idp.test/token"}), {"grant_type": "x"})
    assert exc.value.code == code


def test_token_refusal_names_the_reason_but_never_more_than_80_characters(monkeypatch):
    _reply(monkeypatch, status=401, raw=json.dumps({"error": "e" * 200}).encode())
    with pytest.raises(auth.AuthError) as exc:
        auth._token_request(conn(config={"token_url": "https://idp.test/token"}), {})
    assert "e" * 80 in exc.value.message and "e" * 81 not in exc.value.message
    _reply(monkeypatch, status=502, raw=b"[]")
    with pytest.raises(auth.AuthError) as exc:
        auth._token_request(conn(config={"token_url": "https://idp.test/token"}), {})
    assert "HTTP 502" in exc.value.message


def test_a_kind_that_cannot_call_a_web_service_is_refused():
    with pytest.raises(auth.AuthError) as exc:
        auth.credentials(conn(kind="postgres"))
    assert exc.value.code == "CONNECTION_KIND"


def test_masking_a_structure_hides_secrets_in_keys_and_values():
    value = {"k-s3cret": ["x s3cret y", 3, {"n": "s3cret"}], "ok": None}
    masked = injection._mask_leaves(value, ["s3cret"])
    assert masked == {"k-***": ["x *** y", 3, {"n": "***"}], "ok": None}
    assert "s3cret" not in json.dumps(injection.mask_json(value, ["s3cret"]))
    assert injection.mask_json(value, []) == value and injection.mask_json(None, ["s3cret"]) is None


def test_masking_falls_back_to_the_leaves_when_the_text_replacement_breaks_the_syntax(monkeypatch):
    monkeypatch.setattr(injection.json, "loads", lambda text: (_ for _ in ()).throw(ValueError("cassé")))
    assert injection.mask_json({"a": "s3cret"}, ["s3cret"]) == {"a": "***"}
