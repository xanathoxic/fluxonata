"""Connexions : l'option « ne pas vérifier le certificat du serveur » (`skip_tls_verify`). Sans elle, un certificat auto-signé est refusé ; avec elle, la
connexion est chiffrée mais n'importe quel certificat est accepté. Les contrôles d'adresse (réseaux interdits, épinglage) ne changent pas."""

import http.server
import ssl
import threading

import pytest
from rest_framework.test import APIClient

from audit.models import AuditEvent
from core import netsec
from tests.test_mail_imap_tls import ImapServer, _cert, config
from triggers import mail
from vault.models import Connection

WS = "/api/v1/workspaces/team-a"
SENTINEL = "valeur-sentinelle-9999"


@pytest.fixture
def api(world):
    c = APIClient()
    c.force_authenticate(world["designer"])
    return c


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def https_server(tmp_path, settings):
    cert, key = _cert()
    (tmp_path / "c.pem").write_bytes(cert)
    (tmp_path / "k.pem").write_bytes(key)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "localhost"
    yield f"https://localhost:{httpd.server_address[1]}/"
    httpd.shutdown()


# --- le contexte ---------------------------------------------------------------------------------------------------------------------------
def test_the_option_is_off_unless_explicitly_one():
    for off in (
        None,
        {},
        {"skip_tls_verify": ""},
        {"skip_tls_verify": "0"},
        {"skip_tls_verify": "oui"},
        {"skip_tls_verify": 1},
    ):
        assert not netsec.skip_tls(off) and netsec.context_for(off) is None
    ctx = netsec.context_for({"skip_tls_verify": "1"})
    assert ctx is not None and ctx.verify_mode == ssl.CERT_NONE and ctx.check_hostname is False
    assert netsec.skip_tls({"skip_tls_verify": "1"})


def test_a_self_signed_service_is_refused_by_default_and_reachable_when_verification_is_skipped(https_server):
    with pytest.raises(OSError, match="CERTIFICATE|certificate"):  # SSLError
        netsec.safe_request("GET", https_server, timeout=5)
    status, _, body = netsec.safe_request(
        "GET", https_server, timeout=5, ssl_context=netsec.context_for({"skip_tls_verify": "1"})
    )
    assert status == 200 and b"ok" in body


def test_skipping_verification_never_relaxes_the_address_checks(settings):
    settings.ORCH_EGRESS_ALLOWLIST = ""
    ctx = netsec.context_for({"skip_tls_verify": "1"})
    for url in ("https://127.0.0.1/", "https://169.254.169.254/latest/meta-data/", "https://10.0.0.5/"):
        with pytest.raises(netsec.UnsafeURL):
            netsec.safe_request("GET", url, timeout=2, ssl_context=ctx)


@pytest.fixture
def imap(tmp_path, settings):
    srv = ImapServer(tmp_path)
    settings.ORCH_EGRESS_ALLOWLIST = "localhost"
    yield srv
    srv.sock.close()


# --- IMAP (poignée de main TLS réelle) -----------------------------------------------------------------------------------------------------
def test_imap_refuses_an_unknown_certificate_unless_the_connection_skips_verification(imap):
    # le serveur de test est monopolisé par une poignée de main refusée : la connexion qui réussit passe en premier
    conn = mail.connect(config(imap, skip_tls_verify="1"), "bon")
    assert [i["subject"] for i in mail.fetch_new(conn, "INBOX")] == ["Panne urgente"]
    conn.logout()
    with pytest.raises(mail.MailError, match="certificat"):
        mail.connect(config(imap, skip_tls_verify="0"), "bon")


# --- API des connexions --------------------------------------------------------------------------------------------------------------------
def create(api, name="svc", **config):
    return api.post(
        f"{WS}/connections/",
        {
            "name": name,
            "label": "Service",
            "kind": "http_bearer",
            "config": {"base_url": "https://93.184.216.34", **config},
            "secret_value": SENTINEL,
        },
        format="json",
    )


def test_the_option_is_saved_audited_and_changeable(api, world):
    r = create(api, skip_tls_verify="1")
    assert r.status_code == 201 and r.json()["config"]["skip_tls_verify"] == "1" and SENTINEL not in r.content.decode()
    created = AuditEvent.objects.filter(action="connection.create").latest("id")
    assert created.data["tls_verification_disabled"] is True
    conn = Connection.objects.get(name="svc")
    r = api.patch(f"{WS}/connections/{conn.pk}/", {"config": {"base_url": "https://93.184.216.34"}}, format="json")
    assert (
        r.status_code == 200 and "skip_tls_verify" not in r.json()["config"]
    )  # retirée : la vérification redevient la règle
    assert AuditEvent.objects.filter(action="connection.update").latest("id").data["tls_verification_disabled"] is False


def test_a_value_other_than_zero_or_one_is_refused_and_a_kind_without_tls_choice_drops_it(api):
    r = create(api, skip_tls_verify="peut-être")
    assert r.status_code == 400 and "skip_tls_verify" in r.content.decode()
    r = api.post(
        f"{WS}/connections/",
        {
            "name": "pg",
            "label": "PG",
            "kind": "postgres",
            "config": {"host": "db.exemple.test", "database": "d", "username": "u", "skip_tls_verify": "1"},
            "secret_value": SENTINEL,
        },
        format="json",
    )
    assert (
        r.status_code == 201 and "skip_tls_verify" not in r.json()["config"]
    )  # PostgreSQL a déjà son `sslmode` : pas de second réglage


def test_the_connection_test_uses_the_option(api, monkeypatch):
    seen = []

    def fake(method, url, **kw):
        seen.append(kw.get("ssl_context"))
        return 200, {}, b"{}"

    monkeypatch.setattr(netsec, "safe_request", fake)
    for given, expect_skip in (("1", True), ("", False)):
        name = f"svc-{given or 'strict'}"
        assert create(api, name, **({"skip_tls_verify": given} if given else {})).status_code == 201
        conn = Connection.objects.get(name=name)
        assert api.post(f"{WS}/connections/{conn.pk}/test/").json()["ok"] is True
        ctx = seen[-1]
        assert (ctx is not None and ctx.verify_mode == ssl.CERT_NONE) is expect_skip


# --- étape HTTP d'un workflow, de bout en bout --------------------------------------------------------------------------------------------
def _http_step(ws, world, base_url, **config):
    from tests.wf_helpers import drain, launch, node, publish, schema
    from vault.models import Secret

    s = Secret(workspace=ws, name=f"s-{len(config)}")
    s.set_value("sekret-token-123")
    s.save()
    Connection.objects.create(
        workspace=ws,
        name=f"svc{len(config)}",
        label="S",
        kind="http_bearer",
        config={"base_url": base_url, **config},
        secret=s,
    )
    v = publish(
        ws,
        f"h{len(config)}",
        [node("h", "http", name="Appel", connection=f"svc{len(config)}", mode="reel", path="/")],
        [],
        inputs=schema(),
        user=world["designer"],
    )
    return drain(launch(ws, v, world["designer"]))


def test_an_http_step_fails_on_a_self_signed_certificate_and_succeeds_when_the_connection_skips_verification(
    ws, world, https_server
):
    strict = _http_step(ws, world, https_server)
    assert strict.status == "failed", strict.error  # le certificat auto-signé est refusé : l'étape échoue
    relaxed = _http_step(ws, world, https_server, skip_tls_verify="1")
    out = relaxed.steps.get(node_id="h").output
    assert relaxed.status == "succeeded" and out["status"] == 200
    assert "sekret-token-123" not in str(out)
