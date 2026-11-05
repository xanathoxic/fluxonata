from email.message import EmailMessage

import pytest

from runs.models import WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema
from triggers import mail, poller
from triggers.models import PollTrigger
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"


def _mail(n, sender="Ada <ada@example.org>", subject="Panne imprimante", attach=False):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"], m["Date"] = (
        sender,
        "support@example.org",
        subject,
        f"<id-{n}@example.org>",
        "Tue, 27 Oct 2026 10:00:00 +0000",
    )
    m.set_content(f"Bonjour, message {n}")
    if attach:
        m.add_attachment(b"x" * 10, maintype="application", subtype="pdf", filename="rapport.pdf")
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, messages):
        self.messages, self.selected, self.closed = messages, None, False

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return ("OK", [b"1"]) if folder.strip('"') == "INBOX" else ("NO", [])

    def search(self, charset, criterion):
        assert criterion == "UNSEEN"
        return "OK", [b" ".join(str(i).encode() for i in range(1, len(self.messages) + 1))]

    def fetch(self, nums, what):
        assert "PEEK" in what  # the mailbox is never modified
        self.fetches = getattr(self, "fetches", []) + [(nums, what)]
        out = []
        for n in str(nums).split(","):
            raw = self.messages[int(n) - 1]
            if "HEADER.FIELDS" in what:
                from email import message_from_bytes

                m = message_from_bytes(raw)
                raw = f"From: {m['From']}\r\nSubject: {m['Subject']}\r\n\r\n".encode()
            out += [(f"{n} (BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        return "OK", out

    def logout(self):
        self.closed = True


@pytest.fixture
def ws(world):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="mailpw")
    s.set_value("motdepasse")
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="boite",
        label="Boîte",
        kind="imap",
        config={"host": "imap.example.org", "port": "993", "username": "support", "folder": "INBOX"},
        secret=s,
    )
    publish(
        ws,
        "courrier",
        [node("e", action="echo", inputs={"value": "{{ inputs.item.subject }}"})],
        [],
        inputs=schema(["item"], item={"type": "object"}),
        user=world["designer"],
    )
    return ws


def _trigger(world, **req):
    body = {
        "name": "Courriels",
        "workflow": "courrier",
        "environment": "test",
        "source": "imap",
        "interval_seconds": 60,
        "id_path": "id",
        "request": {"connection": "boite", **req},
        "fire_existing": True,
    }
    r = client_for(world["designer"]).post(f"{BASE}/poll-triggers/", body, format="json")
    assert r.status_code == 201, r.data
    return PollTrigger.objects.get(pk=r.data["id"])


def test_parsing_a_message_describes_attachments_without_reading_them():
    item = mail.to_item(_mail(1, attach=True), "7")
    assert item["id"] == "<id-1@example.org>" and item["from"] == "ada@example.org" and item["from_name"] == "Ada"
    assert item["subject"] == "Panne imprimante" and "message 1" in item["text"]
    assert item["attachments"] == [{"name": "rapport.pdf", "content_type": "application/pdf", "size": 10}]


def test_unread_mail_starts_one_run_each_filtered_and_never_twice(ws, world, monkeypatch):
    sessions = []

    def fake_connect(config, password):
        assert password == "motdepasse" and config["host"] == "imap.example.org"
        sessions.append(
            FakeIMAP(
                [_mail(1), _mail(2, sender="Bob <bob@example.org>", subject="Facture"), _mail(3, subject="Panne écran")]
            )
        )
        return sessions[-1]

    monkeypatch.setattr(mail, "connect", fake_connect)
    t = _trigger(world, subject_contains="panne")
    poller.poll_once(PollTrigger.objects.get(pk=t.pk))
    runs = WorkflowRun.objects.filter(workspace=ws)
    assert sorted(r.inputs["item"]["subject"] for r in runs) == ["Panne imprimante", "Panne écran"]
    assert sessions[0].selected == ('"INBOX"', True) and sessions[0].closed
    poller.poll_once(PollTrigger.objects.get(pk=t.pk))
    assert WorkflowRun.objects.filter(workspace=ws).count() == 2  # same messages: nothing new


def test_server_errors_are_reported_and_private_servers_refused(ws, world, monkeypatch):
    def refuse(config, password):
        raise mail.MailError("Connexion refusée par le serveur (identifiant ou mot de passe).")

    monkeypatch.setattr(mail, "connect", refuse)
    t = _trigger(world)
    poller.poll_once(PollTrigger.objects.get(pk=t.pk))
    t = PollTrigger.objects.get(pk=t.pk)
    assert t.last_ok is False and "identifiant" in t.last_note and "motdepasse" not in t.last_note
    monkeypatch.undo()
    with pytest.raises(mail.MailError, match="refusé"):
        mail.connect({"host": "127.0.0.1", "port": 993, "username": "x"}, "pw")  # loopback is not allowlisted here


def test_creation_requires_an_imap_connection(ws, world):
    body = {
        "name": "x",
        "workflow": "courrier",
        "source": "imap",
        "interval_seconds": 60,
        "id_path": "id",
        "request": {"connection": "absente"},
    }
    assert client_for(world["designer"]).post(f"{BASE}/poll-triggers/", body, format="json").status_code == 400
