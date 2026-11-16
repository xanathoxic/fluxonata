"""Étape LDAP : refus de l'annuaire expliqués sans fuite, valeurs d'attributs rendues en texte (annuaire remplacé par une doublure)."""

from types import SimpleNamespace

import ldap3
import pytest
from ldap3.core import exceptions as lx

from core import netsec
from runs import ldap_node
from vault.external import ExternalVaultError

SPEC = {
    "connection": "ad",
    "base": "dc=x",
    "filter": "(cn=*)",
    "scope": "sub",
    "attributes": ["cn"],
    "max_rows": 2,
    "timeout": 5,
}


class FakeLdap:
    fail, entries = None, []
    instances: list = []

    def __init__(self, server, **kw):
        self.kw = kw
        type(self).instances.append(self)

    def open(self):
        pass

    def start_tls(self):
        pass

    def bind(self):
        if self.fail_at == "bind":
            raise type(self).fail

    def search(self, *a, **k):
        if type(self).fail is not None and self.fail_at == "search":
            raise type(self).fail

    @property
    def response(self):
        return type(self).entries

    def unbind(self):
        pass

    fail_at = "search"


@pytest.fixture
def run(monkeypatch):
    FakeLdap.fail, FakeLdap.entries, FakeLdap.instances, FakeLdap.fail_at = None, [], [], "search"
    monkeypatch.setattr(ldap3, "Connection", FakeLdap)
    monkeypatch.setattr(netsec, "validate_url", lambda url: SimpleNamespace(host="ad.test", ip="127.0.0.1", port=389))
    monkeypatch.setattr(ldap_node, "record", lambda *a, **k: None)

    def go(login=lambda kind: ("cn=r", "pw"), tls="none"):
        conn = SimpleNamespace(config={"host": "ad.test", "tls": tls}, login=login, secret=object())
        monkeypatch.setattr(ldap_node, "find_connection", lambda *a: conn)
        sr = SimpleNamespace(
            http_job={"spec": SPEC}, run=SimpleNamespace(workspace_id=1, environment_id=None, workspace=None)
        )
        return ldap_node.execute(sr)

    return go


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (lx.LDAPInvalidCredentialsResult(), "LDAP_AUTH"),
        (lx.LDAPBindError(), "LDAP_AUTH"),
        (lx.LDAPNoSuchObjectResult(), "LDAP_BASE"),
        (lx.LDAPOperationResult(), "LDAP_ERROR"),
    ],
)
def test_directory_refusals(run, exc, code):
    FakeLdap.fail = exc
    result, err = run()
    assert result is None and err["code"] == code and "pw" not in err["message"]


def test_a_size_limit_keeps_what_was_read(run):
    FakeLdap.fail = lx.LDAPSizeLimitExceededResult()
    result, err = run()
    assert err is None and result["rows"] == [] and result["count"] == 0


def test_an_unreachable_directory_is_an_explicit_error(run):
    FakeLdap.fail = OSError()
    result, err = run()
    assert result is None and "n'a pas répondu" in err["message"]


def test_a_vault_failure_on_the_bind_account_keeps_its_code(run):
    def down(kind):
        raise ExternalVaultError("VAULT_DOWN", "indisponible")

    assert run(login=down)[1]["code"] == "VAULT_DOWN"


def test_entries_become_plain_rows_and_are_truncated_to_the_limit(run):
    FakeLdap.entries = [
        {"type": "searchResRef"},
        {
            "type": "searchResEntry",
            "dn": "cn=a",
            "attributes": {"cn": ["a"], "mail": ["a@x", "b@x"], "photo": [b"\xff\xfe"]},
        },
        {
            "type": "searchResEntry",
            "dn": "cn=b",
            "attributes": {"cn": [b"b"], "n": 3, "obj": {1, 2}},
        },
        {"type": "searchResEntry", "dn": "cn=c", "attributes": {}},
    ]
    result, err = run(tls="starttls")
    assert err is None and result["truncated"] is True and result["count"] == 2
    first, second = result["rows"]
    assert first["dn"] == "cn=a" and first["cn"] == "a" and first["mail"] == ["a@x", "b@x"]
    assert first["photo"].startswith("base64:") and second["cn"] == "b" and second["n"] == 3
    assert isinstance(second["obj"], str)


def test_missing_or_blocked_connections(run, monkeypatch):
    run()  # installs the doubles
    monkeypatch.setattr(ldap_node, "find_connection", lambda *a: None)
    sr = SimpleNamespace(
        http_job={"spec": SPEC}, run=SimpleNamespace(workspace_id=1, environment_id=None, workspace=None)
    )
    assert ldap_node.execute(sr)[1]["code"] == "CONNECTION_MISSING"
    monkeypatch.setattr(
        ldap_node, "find_connection", lambda *a: SimpleNamespace(config={}, secret=object(), login=None)
    )
    monkeypatch.setattr(netsec, "validate_url", lambda url: (_ for _ in ()).throw(netsec.UnsafeURL("privée")))
    assert ldap_node.execute(sr)[1]["code"] == "LDAP_BLOCKED"
