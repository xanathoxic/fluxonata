"""Étape SMTP : chaque refus du serveur devient une erreur explicite sans fuite ; préparation validée (serveur remplacé par une doublure)."""

import smtplib
import ssl
from types import SimpleNamespace

import pytest

from core import netsec
from runs import smtp_node
from runs.smtp_node import SmtpStepError
from vault.external import ExternalVaultError

SPEC = {"connection": "c", "to": ["b@x.test"], "cc": [], "subject": "Objet", "body": "Corps", "timeout": 5}


def _ssl_error(reason: str) -> ssl.SSLError:
    """Comme celles du module ssl, qui portent toujours `reason`."""
    exc = ssl.SSLError(1, reason)
    exc.reason = reason
    return exc


def _res(v, ctx):
    return v


class FakeSmtp:
    """Doublure du client : `fail` décrit où et quoi lever."""

    fail: tuple | None = None
    sent = None
    closed = 0

    def __init__(self, *a, **k):
        pass

    def _maybe(self, where):
        if type(self).fail and type(self).fail[0] == where:
            raise type(self).fail[1]

    def ehlo(self):
        self._maybe("ehlo")

    def starttls(self, context=None):
        self._maybe("starttls")

    def login(self, user, secret):
        self._maybe("login")

    def send_message(self, message, from_addr, to_addrs):
        self._maybe("send")
        type(self).sent = (from_addr, to_addrs)
        return {}

    def quit(self):
        pass

    def close(self):
        type(self).closed += 1


@pytest.fixture
def smtp(monkeypatch):
    FakeSmtp.fail, FakeSmtp.sent, FakeSmtp.closed = None, None, 0
    monkeypatch.setattr(smtp_node, "_PinnedSMTPSSL", FakeSmtp)
    monkeypatch.setattr(smtp_node, "_PinnedSMTP", FakeSmtp)
    monkeypatch.setattr(smtp_node, "record", lambda *a, **k: None)
    monkeypatch.setattr(netsec, "validate_url", lambda url: SimpleNamespace(host="smtp.test", ip="127.0.0.1", port=465))

    def go(tls="ssl", login=lambda: ("u", "mot-de-passe-secret"), conn_secret=True):
        cfg = {"host": "smtp.test", "from_address": "noreply@x.test", "tls": tls}
        conn = SimpleNamespace(config=cfg, login=login, secret=object() if conn_secret else None)
        monkeypatch.setattr(smtp_node, "find_connection", lambda *a: conn)
        return smtp_node.execute_spec(SPEC, SimpleNamespace(pk=1), None, None)

    return go


def test_a_sent_message_reports_recipients_and_id(smtp):
    result, err = smtp()
    assert err is None and result["sent"] is True and result["recipients"] == 1 and result["message_id"]
    assert FakeSmtp.sent == ("noreply@x.test", ["b@x.test"])


def test_starttls_mode_goes_through_the_secure_handshake(smtp):
    FakeSmtp.fail = ("starttls", smtplib.SMTPNotSupportedError("pas de TLS"))
    assert smtp(tls="starttls")[1]["code"] == "SMTP_TLS"


@pytest.mark.parametrize(
    ("where", "exc", "code"),
    [
        ("login", smtplib.SMTPAuthenticationError(535, b"refuse"), "SMTP_AUTH"),
        ("send", smtplib.SMTPRecipientsRefused({"b@x.test": (550, b"non")}), "SMTP_RECIPIENTS"),
        ("send", smtplib.SMTPSenderRefused(550, b"non", "noreply@x.test"), "SMTP_SENDER"),
        ("login", smtplib.SMTPNotSupportedError("AUTH"), "SMTP_TLS"),
        ("ehlo", _ssl_error("CERTIFICATE_VERIFY_FAILED"), "SMTP_TLS"),
        ("ehlo", smtplib.SMTPServerDisconnected("coupé"), "SMTP_ERROR"),
        ("ehlo", OSError("mot-de-passe-secret"), "SMTP_ERROR"),
    ],
)
def test_server_refusals_are_explicit_and_leak_nothing(smtp, where, exc, code):
    FakeSmtp.fail = (where, exc)
    result, err = smtp()
    assert result is None and err["code"] == code
    assert "mot-de-passe-secret" not in err["message"] + err["next_step"]
    assert FakeSmtp.closed == 1  # the connection is closed whatever happened


def test_setup_refusals(smtp, monkeypatch):
    assert smtp(conn_secret=False)[1]["code"] == "CONNECTION_MISSING"

    def down():
        raise ExternalVaultError("VAULT_DOWN", "indisponible")

    assert smtp(login=down)[1]["code"] == "VAULT_DOWN"
    monkeypatch.setattr(netsec, "validate_url", lambda url: (_ for _ in ()).throw(netsec.UnsafeURL("privée")))
    assert smtp()[1]["code"] == "SMTP_BLOCKED"


def test_a_message_that_cannot_be_built_is_refused_before_connecting(smtp, monkeypatch):
    monkeypatch.setattr(
        smtp_node, "build_message", lambda cfg, spec: (_ for _ in ()).throw(SmtpStepError("SMTP_CONFIG", "mal formé"))
    )
    result, err = smtp()
    assert result is None and err["code"] == "SMTP_CONFIG" and FakeSmtp.sent is None


def test_prepare_requires_a_recipient(monkeypatch):
    monkeypatch.setattr(smtp_node, "find_connection", lambda *a: SimpleNamespace(name="c"))
    node = {"connection": "c", "to": "", "subject": "s", "body": "b"}
    with pytest.raises(SmtpStepError, match="destinataire"):
        smtp_node.prepare(node, _res, {}, 1, None)
    monkeypatch.setattr(smtp_node, "find_connection", lambda *a: None)
    with pytest.raises(SmtpStepError) as exc:
        smtp_node.prepare(node, _res, {}, 1, None)
    assert exc.value.code == "CONNECTION_MISSING"
