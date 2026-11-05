"""Reading new e-mails over IMAP (TLS only), for polling triggers. Runs in the worker process.

The server name goes through the same address checks as every outgoing call (`core.netsec`: public addresses only unless
allowlisted by the administrator) and the connection is pinned to the validated address. Messages are fetched with PEEK
(the mailbox is not modified); attachments are described (name, size, type), never read or stored."""

import email
import imaplib
import logging
import re
import ssl
import time
from email import policy
from email.utils import parseaddr

from core import netsec

log = logging.getLogger("fluxonata.mail")
MAX_FETCH = 50
MAX_TEXT = 20000
TIMEOUT = 20


class MailError(Exception):
    pass


class _PinnedIMAP(imaplib.IMAP4_SSL):
    def __init__(self, target, timeout, context=None):
        self._target = target
        super().__init__(
            host=target.host, port=target.port, timeout=timeout, ssl_context=context or ssl.create_default_context()
        )

    def _create_socket(self, timeout):
        raw = netsec.open_connection(
            self._target, timeout
        )  # the validated addresses only, IPv4 first, one after the other
        return self.ssl_context.wrap_socket(raw, server_hostname=self._target.host)


def connect(config: dict, password: str, ssl_context: ssl.SSLContext | None = None):
    """Logged-in IMAP session. Raises MailError (French message, no secret)."""
    host = str(config.get("host", "")).strip()
    port = int(config.get("port") or 993)
    try:
        target = netsec.validate_url(f"https://{host}:{port}/")
    except netsec.UnsafeURL as exc:
        raise MailError(f"Serveur refusé : {exc}") from None
    try:
        conn = _PinnedIMAP(target, TIMEOUT, ssl_context)
        conn.login(config.get("username", ""), password)
    except imaplib.IMAP4.error:
        raise MailError("Connexion refusée par le serveur (identifiant ou mot de passe).") from None
    except ssl.SSLError as exc:
        raise MailError(
            f"Connexion sécurisée refusée (certificat ou protocole) : {exc.reason or type(exc).__name__}."
        ) from None
    except OSError as exc:
        raise MailError(f"Le serveur de messagerie n'a pas répondu ({type(exc).__name__} : réseau ou délai).") from None
    except Exception as exc:  # noqa: BLE001 - a bug in the client must not look like a network problem
        log.exception("Erreur interne du client IMAP")
        raise MailError(
            f"Erreur interne du client de messagerie ({type(exc).__name__}) : voir le journal du worker."
        ) from None
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


HEADER_ITEMS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
BODY_ITEMS = "(BODY.PEEK[]<0.524288>)"  # at most 512 KB of a message: its text, not a huge attachment
SCAN_LIMIT = 200  # unread messages whose headers are looked at when a filter is set
BATCH = 10  # messages per FETCH command
FETCH_DEADLINE = 90.0  # seconds for one poll: well under the poller's lease, the rest is read at the next poll
_FETCH_NUM = re.compile(rb"^\s*(\d+)\s*\(")


def _headers_match(raw: bytes, from_contains: str, subject_contains: str) -> bool:
    msg = email.message_from_bytes(raw, policy=policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    if from_contains and from_contains.lower() not in f"{addr} {name}".lower():
        return False
    return not subject_contains or subject_contains.lower() in str(msg.get("Subject", "")).lower()


def _fetch_many(conn, nums: list[bytes], items: str) -> dict[bytes, bytes]:
    """One FETCH command for several messages (every command costs a round trip, which a loaded server makes slow):
    {message number: the bytes returned}."""
    status, parts = conn.fetch(b",".join(nums).decode(), items)
    if status != "OK":
        return {}
    out = {}
    for p in parts:
        if isinstance(p, tuple):
            m = _FETCH_NUM.match(p[0])
            if m:
                out[m.group(1)] = p[1]
    return out


def fetch_new(
    conn, folder: str, from_contains: str = "", subject_contains: str = "", deadline_seconds: float = FETCH_DEADLINE
) -> list[dict]:
    """Unread messages, oldest first, filtered by sender / subject substring (case-insensitive). Few commands, small
    transfers: the HEADERS of the newest unread messages come in ONE command (with a filter), then the messages that match
    come in batches, each limited to 512 KB; so a mailbox with thousands of unread messages and large attachments cannot make
    a poll last minutes. A poll stops at its deadline: what was not read is read at the next poll (nothing is marked as
    read, and only returned messages are remembered)."""
    status, _ = conn.select(f'"{folder or "INBOX"}"', readonly=True)
    if status != "OK":
        raise MailError(f"Dossier introuvable : {folder}.")
    status, data = conn.search(None, "UNSEEN")
    if status != "OK":
        raise MailError("Recherche impossible dans la boîte.")
    filtered = bool(from_contains or subject_contains)
    nums = (data[0] or b"").split()[-(SCAN_LIMIT if filtered else MAX_FETCH) :]
    stop_at = time.monotonic() + deadline_seconds
    if filtered and nums:
        heads = _fetch_many(conn, nums, HEADER_ITEMS)
        nums = [n for n in nums if n in heads and _headers_match(heads[n], from_contains, subject_contains)]
    out: list[dict] = []
    for i in range(0, min(len(nums), MAX_FETCH), BATCH):
        if time.monotonic() > stop_at:
            break
        chunk = nums[i : min(i + BATCH, MAX_FETCH)]
        raws = _fetch_many(conn, chunk, BODY_ITEMS)
        out += [to_item(raws[n], n.decode()) for n in chunk if n in raws]
    return out
