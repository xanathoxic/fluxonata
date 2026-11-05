"""Étape « Envoyer un e-mail » : contre un vrai serveur SMTP local (TLS direct et STARTTLS), jamais contre un service réel."""

import base64
import re
import socket
import ssl
import threading

import pytest

from audit.models import AuditEvent
from runs import smtp_node
from tests.conftest import client_for
from tests.test_mail_imap_tls import _cert
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish, schema
from vault.models import Connection, Secret

USER, PASSWORD = "robot@exemple.test", "mdp-smtp-12345"


class SmtpServer:
    """A minimal ESMTP server: EHLO, STARTTLS (optional), AUTH PLAIN, MAIL, RCPT, DATA, QUIT. Keeps what it received."""

    def __init__(self, tmp_path, starttls=False):
        self.cert_pem, key_pem = _cert()
        (tmp_path / "c.pem").write_bytes(self.cert_pem)
        (tmp_path / "k.pem").write_bytes(key_pem)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
        self.starttls, self.messages, self.logins = starttls, [], []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def client_context(self):
        return ssl.create_default_context(cadata=self.cert_pem.decode())

    def serve(self):
        while True:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.session, args=(raw,), daemon=True).start()

    def session(self, raw):
        try:
            conn = raw if self.starttls else self.ctx.wrap_socket(raw, server_side=True)
            f = conn.makefile("rwb")
            f.write(b"220 fake ESMTP\r\n")
            f.flush()
            sender, rcpts, secure = "", [], not self.starttls
            while True:
                line = f.readline()
                if not line:
                    return
                cmd = line.decode().strip()
                up = cmd.upper()
                if up.startswith("EHLO"):
                    extra = "250-STARTTLS\r\n" if (self.starttls and not secure) else ""
                    auth = "250-AUTH PLAIN\r\n" if secure else ""
                    f.write(f"250-fake\r\n{extra}{auth}250 SIZE 1000000\r\n".encode())
                elif up == "STARTTLS" and self.starttls:
                    f.write(b"220 go ahead\r\n")
                    f.flush()
                    conn = self.ctx.wrap_socket(conn, server_side=True)
                    f, secure = conn.makefile("rwb"), True
                    continue
                elif up.startswith("AUTH PLAIN") and secure:
                    _, user, pw = base64.b64decode(cmd.split(" ", 2)[2]).decode().split("\0")
                    self.logins.append((user, pw))
                    f.write(b"235 ok\r\n" if pw == PASSWORD else b"535 5.7.8 bad credentials\r\n")
                elif up.startswith("MAIL FROM"):
                    sender, rcpts = re.search(r"<([^>]*)>", cmd).group(1), []
                    f.write(b"250 ok\r\n")
                elif up.startswith("RCPT TO"):
                    addr = re.search(r"<([^>]*)>", cmd).group(1)
                    if "refuse" in addr:
                        f.write(b"550 no such user\r\n")
                    else:
                        rcpts.append(addr)
                        f.write(b"250 ok\r\n")
                elif up == "DATA":
                    f.write(b"354 end with <CRLF>.<CRLF>\r\n")
                    f.flush()
                    data = b""
                    while not data.endswith(b"\r\n.\r\n"):
                        data += f.readline()
                    self.messages.append({"from": sender, "to": list(rcpts), "data": data.decode()})
                    f.write(b"250 queued\r\n")
                elif up == "QUIT":
                    f.write(b"221 bye\r\n")
                    f.flush()
                    return
                else:
                    f.write(b"250 ok\r\n")
                f.flush()
        except (OSError, ssl.SSLError):
            return


@pytest.fixture(params=["ssl", "starttls"])
def server(request, tmp_path, settings, monkeypatch):
    srv = SmtpServer(tmp_path, starttls=request.param == "starttls")
    srv.mode = request.param
    settings.ORCH_EGRESS_ALLOWLIST = "localhost"
    monkeypatch.setattr(smtp_node, "tls_context", srv.client_context)
    yield srv
    srv.sock.close()


@pytest.fixture
def ws(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="smtppw")
    s.set_value(PASSWORD)
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="courrier",
        label="Courrier",
        kind="smtp",
        secret=s,
        config={
            "host": "localhost",
            "port": str(server.port),
            "username": USER,
            "tls": server.mode,
            "from_address": USER,
            "from_name": "Fluxonata",
        },
    )
    return ws


INPUTS = schema(["objet", "corps"], objet={"type": "string"}, corps={"type": "string"}, dest={"type": "string"})


def send(ws, world, slug, mode="reel", inputs=None, **fields):
    user = world["designer"]
    base = {
        "connection": "courrier",
        "mode": mode,
        "to": "{{ inputs.dest }}",
        "subject": "{{ inputs.objet }}",
        "body": "{{ inputs.corps }}",
    }
    v = publish(ws, slug, [node("m", "smtp", **{**base, **fields})], [], inputs=INPUTS, user=user)
    values = {"objet": "Panne", "corps": "Le portail est en panne.", "dest": "lea@exemple.test", **(inputs or {})}
    run = drain(launch(ws, v, user, values), max_rounds=1500)
    return run, run.steps.get(node_id="m")


def test_a_real_send_reaches_the_server_with_tls_and_authentication(ws, world, server):
    run, sr = send(ws, world, "a", cc="chef@exemple.test", inputs={"objet": "Panne urgente"})
    assert run.status == "succeeded", run.error
    assert sr.output["sent"] is True and sr.output["recipients"] == 2 and sr.output["message_id"].startswith("<")
    assert server.logins == [(USER, PASSWORD)]
    (mail,) = server.messages
    assert mail["from"] == USER and mail["to"] == ["lea@exemple.test", "chef@exemple.test"]
    assert "Subject: Panne urgente" in mail["data"] and "Le portail est en panne." in mail["data"]
    assert "From: Fluxonata <robot@exemple.test>" in mail["data"] and "Cc: chef@exemple.test" in mail["data"]
    assert AuditEvent.objects.filter(action="smtp.send").exists()
    assert PASSWORD not in str(sr.output) and PASSWORD not in str(
        list(AuditEvent.objects.values_list("data", flat=True))
    )


def test_test_mode_sends_nothing(ws, world, server):
    run, sr = send(ws, world, "b", mode="test")
    assert run.status == "succeeded" and sr.output["simulated"] is True and sr.output["sent"] is False
    assert server.messages == [] and server.logins == []


def test_header_injection_bad_addresses_and_limits_are_refused_before_any_connection(ws, world, server):
    run, _ = send(ws, world, "c", inputs={"objet": "Bonjour\r\nBcc: pirate@exemple.test"})
    assert run.status == "failed" and run.error["code"] == "SMTP_CONFIG"
    run, _ = send(ws, world, "d", inputs={"dest": "pas-une-adresse"})
    assert run.error["code"] == "SMTP_CONFIG"
    run, _ = send(ws, world, "e", inputs={"dest": "a@exemple.test\r\nBcc: x@exemple.test"})
    assert run.error["code"] == "SMTP_CONFIG"
    run, _ = send(ws, world, "f", inputs={"dest": ",".join(f"u{i}@exemple.test" for i in range(21))})
    assert run.error["code"] == "SMTP_CONFIG" and "20" in run.error["message"]
    run, _ = send(ws, world, "g", inputs={"corps": "   "})
    assert run.error["code"] == "SMTP_CONFIG"
    assert server.messages == [] and server.logins == []


def test_wrong_password_and_refused_recipients_are_explained_without_secrets(ws, world, server):
    s = Secret.objects.get(name="smtppw")
    s.set_value("mauvais-mdp")
    s.save()
    run, _ = send(ws, world, "h")
    assert (
        run.error["code"] == "SMTP_AUTH"
        and "mauvais-mdp" not in str(run.error)
        and "mot de passe" in run.error["next_step"]
    )
    s.set_value(PASSWORD)
    s.save()
    run, sr = send(ws, world, "i", inputs={"dest": "ok@exemple.test, refuse@exemple.test"})
    assert run.status == "succeeded" and sr.output["recipients"] == 1  # one was refused by the server
    run, _ = send(ws, world, "j", inputs={"dest": "refuse@exemple.test"})
    assert run.error["code"] == "SMTP_RECIPIENTS"


def test_untrusted_certificate_and_private_servers_are_refused(ws, world, server, monkeypatch, settings):
    monkeypatch.setattr(smtp_node, "tls_context", ssl.create_default_context)  # the test certificate is not trusted
    run, _ = send(ws, world, "k")
    assert run.error["code"] == "SMTP_TLS" and "certificat" in run.error["message"]
    assert server.logins == []  # nothing (and above all no password) was sent over an unverified channel
    settings.ORCH_EGRESS_ALLOWLIST = ""
    run, _ = send(ws, world, "l")
    assert run.error["code"] == "SMTP_BLOCKED"


def test_connection_and_workflow_validation(ws, world):
    base = {
        "kind": "smtp",
        "label": "x",
        "secret_value": "pw",
        "config": {
            "host": "smtp.exemple.test",
            "port": "465",
            "username": "a@exemple.test",
            "tls": "ssl",
            "from_address": "a@exemple.test",
        },
    }
    c = client_for(world["designer"])
    url = "/api/v1/workspaces/team-a/connections/"
    assert c.post(url, {**base, "name": "ok1"}, format="json").status_code == 201
    for bad in (
        {"tls": "none"},
        {"from_address": "pas une adresse"},
        {"from_address": ""},
        {"from_name": "a\nBcc: x"},
        {"port": "abc"},
    ):
        r = c.post(url, {**base, "name": "bad", "config": {**base["config"], **bad}}, format="json")
        assert r.status_code == 400, bad
    from workflows.validation import validate

    graph = {
        "inputs": INPUTS,
        "nodes": [node("m", "smtp", connection="absente", mode="reel", to="", subject="", body="")],
        "edges": [],
        "outputs": {},
    }
    codes = {i["code"] for i in validate(graph, ws)}
    assert {"bad_smtp", "unknown_connection"} <= codes
