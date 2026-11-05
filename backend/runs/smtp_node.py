"""The « Envoyer un e-mail » step: sends one text message through an SMTP connection.

Safety, all server-side: the server address is checked like every outgoing call (`core.netsec`) and the connection is pinned to
the validated addresses (IPv4 first, one after the other); TLS is mandatory (`ssl` on port 465, or `starttls` on 587) and the
certificate is verified; the sender is the connection's `from_address`, never a value of the step; every address and header is
checked against line breaks (header injection), a message has 20 recipients at most and a text body of 100 KB at most; the
password is read from the vault by the worker at send time and never stored on the step. Sending is an effect that cannot be
undone: the step is never retried automatically, and `mode: test` (the default) sends nothing."""

import contextlib
import logging
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from audit.models import record
from core import netsec
from vault.external import ExternalVaultError

log = logging.getLogger("fluxonata.smtp")
MAX_RECIPIENTS = 20
MAX_BODY = 100 * 1024
MAX_SUBJECT = 200
ADDRESS = re.compile(
    r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)
TLS_MODES = ("ssl", "starttls")


class SmtpStepError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez la configuration de l'étape."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def tls_context() -> ssl.SSLContext:
    """The trust store used to verify the server (a test replaces it with its own certificate)."""
    return ssl.create_default_context()


def clean_address(raw, what: str = "adresse") -> str:
    a = str(raw or "").strip()
    if not a or len(a) > 254 or any(c in a for c in '\r\n\x00<>,;" ') or not ADDRESS.match(a):
        raise SmtpStepError("SMTP_CONFIG", f"Adresse e-mail invalide ({what}) : « {a[:60]} ».")
    return a


def check_connection_config(config: dict) -> None:
    if str(config.get("tls") or "ssl") not in TLS_MODES:
        raise SmtpStepError("SMTP_CONFIG", "TLS : « ssl » (port 465) ou « starttls » (port 587).")
    clean_address(config.get("from_address"), "expéditeur")
    name = str(config.get("from_name") or "")
    if len(name) > 100 or any(c in name for c in "\r\n\x00"):
        raise SmtpStepError("SMTP_CONFIG", "Nom de l'expéditeur invalide (100 caractères, sans retour à la ligne).")
    port = str(config.get("port") or "").strip()
    if port and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise SmtpStepError("SMTP_CONFIG", "Port invalide.")


def find_connection(workspace_id, environment_id, name):
    from django.db.models import Q

    from vault.models import Connection

    return (
        Connection.objects.select_related("secret")
        .filter(workspace_id=workspace_id, name=name, kind="smtp")
        .filter(Q(environment_id=environment_id) | Q(environment__isnull=True))
        .order_by("-environment_id")
        .first()
    )


def _recipients(raw, what: str) -> list[str]:
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, list) else re.split(r"[;,\n]+", str(raw))
    return [clean_address(i, what) for i in items if str(i).strip()]


def prepare(node: dict, resolve, ctx: dict, workspace_id, environment_id) -> dict:
    conn = find_connection(workspace_id, environment_id, node.get("connection", ""))
    if conn is None:
        raise SmtpStepError(
            "CONNECTION_MISSING",
            f"Connexion « {node.get('connection', '')} » introuvable pour cet environnement (type « Serveur SMTP »).",
            "Créez la connexion dans Connexions.",
        )
    to = _recipients(resolve(node.get("to", ""), ctx), "destinataire")
    cc = _recipients(resolve(node.get("cc", ""), ctx), "copie")
    if not to:
        raise SmtpStepError("SMTP_CONFIG", "Indiquez au moins un destinataire.")
    if len(to) + len(cc) > MAX_RECIPIENTS:
        raise SmtpStepError("SMTP_CONFIG", f"{MAX_RECIPIENTS} destinataires au plus par message.")
    subject = str(resolve(node.get("subject", ""), ctx) or "").strip()
    if not subject or len(subject) > MAX_SUBJECT or any(c in subject for c in "\r\n\x00"):
        raise SmtpStepError("SMTP_CONFIG", f"Objet requis ({MAX_SUBJECT} caractères au plus, sur une seule ligne).")
    body = resolve(node.get("body", ""), ctx)
    body = body if isinstance(body, str) else ("" if body is None else str(body))
    if not body.strip() or len(body.encode()) > MAX_BODY:
        raise SmtpStepError("SMTP_CONFIG", "Corps du message requis (100 Ko au plus).")
    return {
        "mode": "reel" if node.get("mode") == "reel" else "test",
        "connection": conn.name,
        "to": to,
        "cc": cc,
        "subject": subject,
        "body": body,
        "timeout": 20,
    }


def simulated_output(spec: dict) -> dict:
    return {"sent": False, "simulated": True, "recipients": len(spec["to"]) + len(spec["cc"]), "message_id": None}


def build_message(cfg: dict, spec: dict) -> EmailMessage:
    msg = EmailMessage()
    sender = clean_address(cfg.get("from_address"), "expéditeur")
    msg["From"] = formataddr((str(cfg.get("from_name") or ""), sender)) if cfg.get("from_name") else sender
    msg["To"] = ", ".join(spec["to"])
    if spec["cc"]:
        msg["Cc"] = ", ".join(spec["cc"])
    msg["Subject"] = spec["subject"]
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
    msg.set_content(spec["body"])
    return msg


class _PinnedSMTP(smtplib.SMTP):
    """Connects to the validated addresses only (the name is never resolved a second time)."""

    def __init__(self, target, timeout):
        self._pin = target
        super().__init__(target.host, target.port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        return netsec.open_connection(self._pin, timeout)


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, target, timeout, context):
        self._pin = target
        super().__init__(target.host, target.port, timeout=timeout, context=context)

    def _get_socket(self, host, port, timeout):
        raw = netsec.open_connection(self._pin, timeout)
        return self.context.wrap_socket(raw, server_hostname=self._pin.host)


def execute(sr) -> tuple[dict | None, dict | None]:
    return execute_spec(sr.http_job["spec"], sr.run.workspace, sr.run.environment_id, sr.run)


def execute_spec(spec: dict, workspace, environment_id, audit_target) -> tuple[dict | None, dict | None]:
    conn = find_connection(workspace.pk, environment_id, spec["connection"])
    if conn is None or conn.secret is None:
        return None, _err("CONNECTION_MISSING", "Connexion introuvable ou sans secret.", "Vérifiez la connexion.")
    cfg = conn.config or {}
    tls = str(cfg.get("tls") or "ssl")
    try:
        port = int(cfg.get("port") or (465 if tls == "ssl" else 587))
        target = netsec.validate_url(f"https://{cfg.get('host', '')}:{port}/")
        message = build_message(cfg, spec)
    except netsec.UnsafeURL as exc:
        return None, _err("SMTP_BLOCKED", f"Serveur refusé : {exc}", "Vérifiez la connexion.")
    except SmtpStepError as exc:
        return None, _err(exc.code, exc.message, exc.next_step)
    try:
        user, secret = conn.login()
    except ExternalVaultError as exc:
        return None, _err(
            exc.code,
            exc.message,
            "Vérifiez le coffre externe (Plate-forme → Coffre externe) et le secret de la connexion.",
        )
    started = time.monotonic()
    server = None
    try:
        context = tls_context()
        if tls == "ssl":
            server = _PinnedSMTPSSL(target, spec["timeout"], context)
        else:
            server = _PinnedSMTP(target, spec["timeout"])
            server.ehlo()
            server.starttls(context=context)
        server.ehlo()
        server.login(user, secret)
        refused = server.send_message(message, from_addr=cfg["from_address"], to_addrs=spec["to"] + spec["cc"])
        server.quit()
        server = None
    except smtplib.SMTPAuthenticationError:
        return None, _err(
            "SMTP_AUTH",
            "Authentification refusée par le serveur SMTP.",
            "Vérifiez l'identifiant et le secret (pour Gmail : mot de passe d'application).",
        )
    except smtplib.SMTPRecipientsRefused as exc:
        return None, _err(
            "SMTP_RECIPIENTS",
            f"Tous les destinataires ont été refusés ({len(exc.recipients)}).",
            "Vérifiez les adresses.",
        )
    except smtplib.SMTPSenderRefused:
        return None, _err(
            "SMTP_SENDER",
            "L'expéditeur a été refusé par le serveur.",
            "L'adresse d'expéditeur de la connexion doit être celle du compte ou un alias autorisé.",
        )
    except smtplib.SMTPNotSupportedError:
        return None, _err(
            "SMTP_TLS",
            "Le serveur ne propose pas la sécurisation (TLS) ou l'authentification demandée.",
            "Vérifiez le port et le mode TLS de la connexion (ssl : 465, starttls : 587).",
        )
    except ssl.SSLError as exc:
        return None, _err(
            "SMTP_TLS",
            f"Connexion sécurisée refusée (certificat ou protocole) : {exc.reason or type(exc).__name__}.",
            "Vérifiez le port et le mode TLS de la connexion.",
        )
    except (smtplib.SMTPException, OSError) as exc:
        return None, _err(
            "SMTP_ERROR",
            f"Le serveur SMTP n'a pas répondu correctement ({type(exc).__name__}).",
            "Vérifiez que le serveur est joignable.",
        )
    finally:
        if server is not None:
            with contextlib.suppress(Exception):
                server.close()
    record(
        "smtp.send",
        workspace=workspace,
        target=audit_target,
        host=target.host,
        recipients=len(spec["to"]) + len(spec["cc"]),
        refused=len(refused),
        ms=int((time.monotonic() - started) * 1000),
    )
    return {
        "sent": True,
        "simulated": False,
        "recipients": len(spec["to"]) + len(spec["cc"]) - len(refused),
        "message_id": message["Message-ID"],
    }, None


def _err(code: str, message: str, next_step: str) -> dict:
    return {"code": code, "message": message, "next_step": next_step}
