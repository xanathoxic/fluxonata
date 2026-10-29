"""IMAP sur TLS contre un vrai serveur local (la classe d'épinglage d'adresse n'était jamais exercée) et ordre des adresses."""

import datetime
import ipaddress
import socket
import ssl
import threading
from email.message import EmailMessage

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from core import netsec
from triggers import mail


def _cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    )
    return pem, key_pem


MESSAGE = EmailMessage()
MESSAGE["From"], MESSAGE["Subject"], MESSAGE["Message-ID"] = (
    "Ada <ada@exemple.test>",
    "Panne urgente",
    "<m1@exemple.test>",
)
MESSAGE.set_content("Le portail est en panne.")


class ImapServer:
    """A minimal IMAP server over TLS: LOGIN, SELECT, SEARCH, FETCH, LOGOUT — one unread message."""

    def __init__(self, tmp_path, password="bon"):
        self.cert_pem, key_pem = _cert()
        (tmp_path / "c.pem").write_bytes(self.cert_pem)
        (tmp_path / "k.pem").write_bytes(key_pem)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
        self.password, self.commands = password, []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(2)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def client_context(self):
        return ssl.create_default_context(cadata=self.cert_pem.decode())

    def serve(self):
        while True:
            try:
                raw, _ = self.sock.accept()
                conn = self.ctx.wrap_socket(raw, server_side=True)
            except OSError:
                return
            threading.Thread(target=self.session, args=(conn,), daemon=True).start()

    def session(self, conn):
        f = conn.makefile("rwb")
        try:
            f.write(b"* OK ready\r\n")
            f.flush()
            for line in f:
                tag, _, rest = line.decode().strip().partition(" ")
                cmd = rest.split(" ")[0].upper()
                self.commands.append(rest)
                if cmd == "CAPABILITY":
                    f.write(f"* CAPABILITY IMAP4rev1\r\n{tag} OK done\r\n".encode())
                elif cmd == "LOGIN":
                    ok = rest.endswith(f'"{self.password}"') or rest.endswith(f" {self.password}")
                    f.write(f"{tag} OK logged in\r\n".encode() if ok else f"{tag} NO invalid credentials\r\n".encode())
                elif cmd in ("SELECT", "EXAMINE"):
                    f.write(f"* 1 EXISTS\r\n{tag} OK [READ-ONLY] selected\r\n".encode())
                elif cmd == "SEARCH":
                    f.write(f"* SEARCH 1\r\n{tag} OK done\r\n".encode())
                elif cmd == "FETCH":
                    body = MESSAGE.as_bytes()
                    f.write(b"* 1 FETCH (BODY[] {%d}\r\n" % len(body) + body + b")\r\n" + f"{tag} OK done\r\n".encode())
                elif cmd == "LOGOUT":
                    f.write(f"* BYE\r\n{tag} OK bye\r\n".encode())
                    f.flush()
                    break
                else:
                    f.write(f"{tag} BAD unknown\r\n".encode())
                f.flush()
        finally:
            conn.close()


@pytest.fixture
def server(tmp_path, settings):
    srv = ImapServer(tmp_path)
    settings.ORCH_EGRESS_ALLOWLIST = "localhost"  # a local server must be allowed explicitly, like any internal host
    yield srv
    srv.sock.close()


def config(srv, **over):
    return {"host": "localhost", "port": srv.port, "username": "ada", **over}


def test_imap_over_tls_connects_reads_unseen_with_peek_and_never_modifies(server):
    conn = mail.connect(config(server), "bon", ssl_context=server.client_context())
    items = mail.fetch_new(conn, "INBOX")
    conn.logout()
    assert [i["subject"] for i in items] == ["Panne urgente"] and items[0]["from"] == "ada@exemple.test"
    assert any("PEEK" in c for c in server.commands) and not any(c.upper().startswith("STORE") for c in server.commands)
    assert any(c.upper().startswith("EXAMINE") for c in server.commands)  # opened read-only: nothing is marked as read


def test_imap_bad_password_and_untrusted_certificate_are_explained(server):
    with pytest.raises(mail.MailError, match="identifiant ou mot de passe") as exc:
        mail.connect(config(server), "mauvais", ssl_context=server.client_context())
    assert "mauvais" not in str(exc.value)
    with pytest.raises(mail.MailError, match="certificat"):
        mail.connect(config(server), "bon")  # the default trust store does not know the test certificate


def test_an_internal_failure_is_not_disguised_as_a_network_problem(server, monkeypatch, caplog):
    def broken(*a, **k):
        raise AttributeError("bug interne")

    monkeypatch.setattr(mail, "_PinnedIMAP", broken)
    with pytest.raises(mail.MailError, match=r"Erreur interne .*AttributeError.*journal"):
        mail.connect(config(server), "bon")
    assert "Erreur interne du client IMAP" in caplog.text


def test_ipv4_addresses_come_before_ipv6(monkeypatch):
    answers = [
        (socket.AF_INET6, 0, 0, "", ("2a00:1450:400c:c09::6c", 0)),
        (socket.AF_INET, 0, 0, "", ("74.125.1.1", 0)),
        (socket.AF_INET, 0, 0, "", ("64.233.1.1", 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers)
    ips = netsec.resolve("imap.exemple.test")
    assert [ipaddress.ip_address(i).version for i in ips] == [4, 4, 6] and ips[0] == "64.233.1.1"
    assert netsec.validate_url("https://imap.exemple.test:993/").ips == tuple(ips)


def test_connection_tries_each_validated_address_in_turn(monkeypatch):
    tried = []

    def fake(addr, timeout):
        tried.append(addr[0])
        if addr[0] != "192.0.2.2":
            raise OSError(101, "Network is unreachable")
        return "socket"

    monkeypatch.setattr(socket, "create_connection", fake)
    target = netsec.Target("https", "h", 993, "/", "192.0.2.1", ("192.0.2.1", "192.0.2.2", "2001:db8::1"))
    assert netsec.open_connection(target, 5) == "socket" and tried == ["192.0.2.1", "192.0.2.2"]
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: (_ for _ in ()).throw(OSError("non")))
    with pytest.raises(OSError):
        netsec.open_connection(target, 5)
