"""Cas limites du client du coffre externe, sans serveur : chemins, TLS, réponses illisibles, jetons, cache, suppression, baux."""

import json
import ssl
from types import SimpleNamespace

import pytest

from core import netsec
from vault import external
from vault.external import ExternalVaultError
from vault.models import Secret


@pytest.fixture(autouse=True)
def _clean():
    external.forget()
    external._kv_cache.clear()
    yield
    external.forget()
    external._kv_cache.clear()


def code_of(fn, *a, **k):
    with pytest.raises(ExternalVaultError) as exc:
        fn(*a, **k)
    return exc.value.code


def cfg(address="https://v.test", **kw):
    base = {
        "address": address,
        "namespace": "",
        "ca_pem": "",
        "verify_tls": True,
        "auth_mount": "approle",
        "role_id": "r",
    }
    return SimpleNamespace(**{**base, "secret_id": lambda: "s", **kw})


# ---- chemins et TLS


def test_paths_are_required_and_bounded():
    assert external.clean_path("/a/b/") == "a/b"
    assert code_of(external.clean_path, "") == "VAULT_PATH_REFUSED"
    assert code_of(external.clean_path, "a" * 201) == "VAULT_PATH_REFUSED"
    assert "préfixe" in str(pytest.raises(ExternalVaultError, external.clean_path, "", "préfixe").value.message)


def test_tls_context_only_for_https_and_a_readable_authority():
    assert external._context(cfg("http://v.test")) is None
    assert code_of(external._context, cfg(ca_pem="pas un certificat")) == "VAULT_BAD_CA"
    lax = external._context(cfg(verify_tls=False))
    assert lax.verify_mode == ssl.CERT_NONE and lax.check_hostname is False
    assert external._context(cfg()).verify_mode == ssl.CERT_REQUIRED


# ---- transport


def stub_transport(monkeypatch, status=200, raw=b"{}", exc=None):
    sent = {}

    def fake(method, url, **kw):
        sent.update(method=method, url=url, **kw)
        if exc:
            raise exc
        return status, {}, raw

    monkeypatch.setattr(netsec, "safe_request", fake)
    return sent


def test_http_sends_token_and_namespace_and_reads_json(monkeypatch):
    sent = stub_transport(monkeypatch, raw=json.dumps({"ok": 1}).encode())
    status, data = external._http(cfg("http://v.test", namespace="ns"), "POST", "/x/y", {"a": 1}, "jeton")
    assert (status, data) == (200, {"ok": 1}) and sent["url"] == "http://v.test/v1/x/y"
    assert sent["headers"]["X-Vault-Token"] == "jeton" and sent["headers"]["X-Vault-Namespace"] == "ns"
    assert json.loads(sent["body"]) == {"a": 1}


def test_http_failures_are_explicit(monkeypatch):
    stub_transport(monkeypatch, raw=b"pas du json")
    assert external._http(cfg("http://v.test"), "GET", "x") == (200, {})
    for exc, code in (
        (netsec.UnsafeURL("privée"), "VAULT_BLOCKED"),
        (ssl.SSLError("certificat"), "VAULT_TLS"),
        (TimeoutError(), "VAULT_UNREACHABLE"),
    ):
        stub_transport(monkeypatch, exc=exc)
        assert code_of(external._http, cfg("http://v.test"), "GET", "x") == code


# ---- connexion AppRole et appels authentifiés


def fake_http(monkeypatch, *replies):
    queue, calls = list(replies), []

    def http(c, method, path, body=None, token=""):
        calls.append((method, path, token))
        return queue.pop(0)

    monkeypatch.setattr(external, "_http", http)
    return calls


def test_approle_login_failures(monkeypatch):
    fake_http(monkeypatch, (403, {"errors": ["denied"]}))
    assert code_of(external._login, cfg()) == "VAULT_AUTH_FAILED"
    fake_http(monkeypatch, (503, {}))
    assert code_of(external._login, cfg()) == "VAULT_UNAVAILABLE"
    fake_http(monkeypatch, (200, {"auth": "pas un objet"}))
    assert code_of(external._login, cfg()) == "VAULT_UNAVAILABLE"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, "VAULT_FORBIDDEN"),
        (404, "VAULT_NOT_FOUND"),
        (503, "VAULT_UNAVAILABLE"),
        (429, "VAULT_UNAVAILABLE"),
        (418, "VAULT_BAD_RESPONSE"),
    ],
)
def test_authenticated_request_failures(monkeypatch, status, code):
    monkeypatch.setattr(external, "_client_token", lambda c: "t")
    fake_http(monkeypatch, (status, {"errors": ["autre"]}))
    assert code_of(external.request, cfg(), "GET", "secret/data/x") == code


def test_a_refused_token_triggers_one_fresh_login(monkeypatch):
    monkeypatch.setattr(external, "_client_token", lambda c: "vieux")
    monkeypatch.setattr(external, "_login", lambda c: "neuf")
    calls = fake_http(monkeypatch, (403, {"errors": ["permission denied"]}), (200, {"data": {}}))
    assert external.request(cfg(), "GET", "x")[0] == 200 and [c[2] for c in calls] == ["vieux", "neuf"]


# ---- KV


def test_kv_read_needs_a_current_value_and_caches_it(monkeypatch):
    monkeypatch.setattr(external, "request", lambda *a, **k: (200, {"data": {"data": None}}))
    assert code_of(external.kv_read, cfg(), "secret", "x") == "VAULT_NOT_FOUND"
    n = {"calls": 0}

    def once(*a, **k):
        n["calls"] += 1
        return 200, {"data": {"data": {"value": "v"}}}

    monkeypatch.setattr(external, "request", once)
    assert external.kv_read(cfg(), "secret", "y") == {"value": "v"} and external.kv_read(cfg(), "secret", "y") == {
        "value": "v"
    }
    assert n["calls"] == 1


def test_the_cache_is_emptied_when_it_grows_too_large(monkeypatch):
    external._kv_cache.update({(i, i, None): (0, {}) for i in range(300)})
    monkeypatch.setattr(external, "request", lambda *a, **k: (200, {"data": {"data": {"value": "v"}}}))
    external.kv_read(cfg(), "secret", "z")
    assert len(external._kv_cache) == 1


def test_destroy_ignores_what_is_already_gone_but_not_other_failures(monkeypatch):
    def gone(*a, **k):
        raise ExternalVaultError("VAULT_NOT_FOUND", "absent")

    monkeypatch.setattr(external, "request", gone)
    external.kv_destroy(cfg(), "secret", "x")

    def down(*a, **k):
        raise ExternalVaultError("VAULT_UNAVAILABLE", "indisponible")

    monkeypatch.setattr(external, "request", down)
    assert code_of(external.kv_destroy, cfg(), "secret", "x") == "VAULT_UNAVAILABLE"


def test_kv_write_returns_the_version_and_clears_the_cache(monkeypatch):
    external._kv_cache[("a", "b", None)] = (0, {})
    monkeypatch.setattr(external, "request", lambda *a, **k: (200, {"data": {"version": 4}}))
    assert external.kv_write(cfg(), "secret", "x", {"value": "v"}) == 4 and not external._kv_cache


# ---- baux et références


def test_dynamic_credentials_need_values(monkeypatch):
    monkeypatch.setattr(external, "request", lambda *a, **k: (200, {"data": {}}))
    assert code_of(external.read_dynamic, cfg(), "database/creds/x") == "VAULT_BAD_RESPONSE"
    monkeypatch.setattr(
        external, "request", lambda *a, **k: (200, {"data": {"username": "u"}, "lease_id": "l", "lease_duration": "60"})
    )
    assert external.read_dynamic(cfg(), "p") == ({"username": "u"}, "l", 60)


def test_revoking_a_lease_is_best_effort(monkeypatch):
    calls = []

    def fail(*a, **k):
        calls.append(a)
        raise ExternalVaultError("VAULT_UNAVAILABLE", "x")

    monkeypatch.setattr(external, "request", fail)
    external.revoke_lease(cfg(), "lease-1")  # never raises
    external.revoke_lease(cfg(), "")  # nothing to revoke: no call
    assert len(calls) == 1


def test_references_need_a_known_mode_and_a_positive_version(monkeypatch, world):
    monkeypatch.setattr(external, "require_allowed", lambda *a, **k: None)
    secret = Secret(name="s", workspace=world["ws_a"])
    assert code_of(external.configure_reference, secret, cfg(), "autre", "secret", "x", "value") == "VAULT_BAD_MODE"
    for bad in (0, -1, "2"):
        assert (
            code_of(external.configure_reference, secret, cfg(), "reference", "secret", "x", "value", bad)
            == "VAULT_BAD_MODE"
        )
