"""Reading new e-mails over IMAP (TLS only), for polling triggers. Runs in the worker process.

The server name goes through the same address checks as every outgoing call (`core.netsec`: public addresses only unless
allowlisted by the administrator) and the connection is pinned to the validated address. Messages are fetched with PEEK
(the mailbox is not modified); attachments are described (name, size, type), never read or stored."""

import email
import imaplib
import socket
import ssl
from email import policy
from email.utils import parseaddr

from core import netsec

MAX_FETCH = 50
MAX_TEXT = 20000
TIMEOUT = 20


class MailError(Exception):
    pass


class _PinnedIMAP(imaplib.IMAP4_SSL):
    def __init__(self, target, timeout):
        self._target = target
        super().__init__(host=target.host, port=target.port, timeout=timeout, ssl_context=ssl.create_default_context())

    def _create_socket(self, timeout):
        raw = socket.create_connection((self._target.ip, self._target.port), timeout)
        return self._ssl_context.wrap_socket(raw, server_hostname=self._target.host)


def connect(config: dict, password: str):
    """Logged-in IMAP session. Raises MailError (French message, no secret)."""
    host = str(config.get("host", "")).strip()
    port = int(config.get("port") or 993)
    try:
        target = netsec.validate_url(f"https://{host}:{port}/")
    except netsec.UnsafeURL as exc:
        raise MailError(f"Serveur refusé : {exc}") from None
    try:
        conn = _PinnedIMAP(target, TIMEOUT)
        conn.login(config.get("username", ""), password)
    except imaplib.IMAP4.error:
        raise MailError("Connexion refusée par le serveur (identifiant ou mot de passe).") from None
    except (OSError, ssl.SSLError):
        raise MailError("Le serveur de messagerie n'a pas répondu (réseau, délai ou certificat refusé).") from None
    return conn


def _text_of(msg) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    try:
        text = body.get_content()
    except (LookupError, UnicodeDecodeError):
        return ""
    return text[:MAX_TEXT]


def to_item(raw: bytes, uid: str) -> dict:
    msg = email.message_from_bytes(raw, policy=policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    attachments = [
        {
            "name": p.get_filename() or "",
            "content_type": p.get_content_type(),
            "size": len(p.get_payload(decode=True) or b""),
        }
        for p in msg.iter_attachments()
    ]
    mid = str(msg.get("Message-ID", "")).strip() or f"uid-{uid}"
    return {
        "id": mid,
        "uid": uid,
        "from": addr,
        "from_name": name,
        "to": str(msg.get("To", ""))[:500],
        "subject": str(msg.get("Subject", ""))[:500],
        "date": str(msg.get("Date", "")),
        "text": _text_of(msg),
        "attachments": attachments,
    }


def fetch_new(conn, folder: str, from_contains: str = "", subject_contains: str = "") -> list[dict]:
    """Unread messages (newest `MAX_FETCH`), oldest first, filtered by sender / subject substring (case-insensitive)."""
    status, _ = conn.select(f'"{folder or "INBOX"}"', readonly=True)
    if status != "OK":
        raise MailError(f"Dossier introuvable : {folder}.")
    status, data = conn.search(None, "UNSEEN")
    if status != "OK":
        raise MailError("Recherche impossible dans la boîte.")
    uids = (data[0] or b"").split()[-MAX_FETCH:]
    out = []
    for uid in uids:
        status, parts = conn.fetch(uid, "(BODY.PEEK[])")
        raw = next((p[1] for p in parts if isinstance(p, tuple)), None) if status == "OK" else None
        if raw is None:
            continue
        item = to_item(raw, uid.decode())
        if from_contains and from_contains.lower() not in f"{item['from']} {item['from_name']}".lower():
            continue
        if subject_contains and subject_contains.lower() not in item["subject"].lower():
            continue
        out.append(item)
    return out
