"""The « Annuaire LDAP » step: READ-ONLY searches in an LDAP / Active Directory directory, through a connection.

A search cannot modify anything (no add/modify/delete is ever sent). Values you put in the filter go through `{nom}`
placeholders and are ESCAPED (no filter injection); results are capped; the server address is checked like every outgoing
call (`core.netsec`) and the connection is pinned to the validated address; the bind password is read from the vault by the
worker at send time. Use a service account that can only read what the workflow needs. `mode: test` sends nothing."""

import base64
import logging
import re
import time

from audit.models import record
from core import netsec
from vault.external import ExternalVaultError

log = logging.getLogger("fluxonata.ldap")
MAX_ROWS = 1000
SCOPES = {"sub": "SUBTREE", "one": "LEVEL", "base": "BASE"}
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class LdapStepError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez la configuration de l'étape."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def find_connection(workspace_id, environment_id, name):
    from django.db.models import Q

    from vault.models import Connection

    return (
        Connection.objects.select_related("secret")
        .filter(workspace_id=workspace_id, name=name, kind="ldap")
        .filter(Q(environment_id=environment_id) | Q(environment__isnull=True))
        .order_by("-environment_id")
        .first()
    )


def build_filter(template: str, params: dict) -> str:
    from ldap3.utils.conv import escape_filter_chars

    t = str(template or "").strip()
    if not (t.startswith("(") and t.endswith(")")) or t.count("(") != t.count(")") or len(t) > 1000:
        raise LdapStepError(
            "LDAP_CONFIG",
            "Le filtre doit être une expression LDAP entre parenthèses, ex. (&(objectClass=person)(mail={mail})).",
        )
    missing = [n for n in PLACEHOLDER.findall(t) if n not in params]
    if missing:
        raise LdapStepError("LDAP_CONFIG", f"Paramètre du filtre manquant : {', '.join(missing)}.")
    return PLACEHOLDER.sub(lambda m: escape_filter_chars(str(params[m.group(1)])), t)


def prepare(node: dict, resolve, ctx: dict, workspace_id, environment_id) -> dict:
    conn = find_connection(workspace_id, environment_id, node.get("connection", ""))
    if conn is None:
        raise LdapStepError(
            "CONNECTION_MISSING",
            f"Connexion « {node.get('connection', '')} » introuvable pour cet environnement (type « Annuaire LDAP »).",
            "Créez la connexion dans Connexions.",
        )
    params = resolve(node.get("params") or {}, ctx)
    if not isinstance(params, dict) or len(params) > 20 or any(isinstance(v, (dict, list)) for v in params.values()):
        raise LdapStepError("LDAP_CONFIG", "Les paramètres doivent former un objet de 20 valeurs simples au plus.")
    attrs = node.get("attributes")
    attrs = (
        [a for a in attrs if isinstance(a, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9;-]{0,60}", a)][:30]
        if isinstance(attrs, list)
        else []
    )
    max_rows = node.get("max_rows")
    return {
        "mode": "reel" if node.get("mode") == "reel" else "test",
        "connection": conn.name,
        "filter": build_filter(node.get("filter", ""), params),
        "base": str(node.get("base") or "")[:500],
        "scope": node.get("scope") if node.get("scope") in SCOPES else "sub",
        "attributes": attrs or ["*"],
        "max_rows": max_rows if isinstance(max_rows, int) and 1 <= max_rows <= MAX_ROWS else 100,
        "timeout": node.get("timeout_seconds")
        if isinstance(node.get("timeout_seconds"), int) and 1 <= node["timeout_seconds"] <= 60
        else 15,
    }


def simulated_output(spec: dict) -> dict:
    return {"rows": [], "count": 0, "truncated": False, "simulated": True}


def _plain(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return "base64:" + base64.b64encode(value).decode()[:200]
    if isinstance(value, (list, tuple)):
        items = [_plain(v) for v in value]
        return items[0] if len(items) == 1 else items
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def execute(sr) -> tuple[dict | None, dict | None]:
    import ssl

    from ldap3 import NONE, Connection, Server, Tls
    from ldap3.core.exceptions import LDAPException

    spec = sr.http_job["spec"]
    conn = find_connection(sr.run.workspace_id, sr.run.environment_id, spec["connection"])
    if conn is None or conn.secret is None:
        return None, {
            "code": "CONNECTION_MISSING",
            "message": "Connexion introuvable ou sans mot de passe.",
            "next_step": "Vérifiez la connexion.",
        }
    cfg = conn.config
    tls_mode = cfg.get("tls") or "starttls"
    try:
        target = netsec.validate_url(
            f"https://{cfg.get('host', '')}:{int(cfg.get('port') or (636 if tls_mode == 'ldaps' else 389))}/"
        )
    except netsec.UnsafeURL as exc:
        return None, {
            "code": "LDAP_BLOCKED",
            "message": f"Serveur refusé : {exc}",
            "next_step": "Vérifiez la connexion.",
        }
    started = time.monotonic()
    try:
        server = Server(
            target.ip,
            port=target.port,
            use_ssl=tls_mode == "ldaps",
            get_info=NONE,
            connect_timeout=10,
            tls=Tls(validate=ssl.CERT_NONE)
            if tls_mode != "none"
            else None,  # encrypted; the address was validated and is pinned
        )
        try:
            bind = conn.login("bind_dn")
        except ExternalVaultError as exc:
            return None, {
                "code": exc.code,
                "message": exc.message,
                "next_step": "Vérifiez le coffre externe (Plate-forme → Coffre externe) et le secret de la connexion.",
            }
        ldap = Connection(
            server,
            user=bind[0],
            password=bind[1],
            receive_timeout=spec["timeout"],
            raise_exceptions=True,
        )
        if tls_mode == "starttls":
            ldap.open()
            ldap.start_tls()
        ldap.bind()
        base = spec["base"] or cfg.get("base_dn", "")
        ldap.search(
            base,
            spec["filter"],
            search_scope=SCOPES[spec["scope"]],
            attributes=spec["attributes"],
            size_limit=spec["max_rows"] + 1,
            time_limit=spec["timeout"],
        )
        entries = [e for e in ldap.response if e.get("type") == "searchResEntry"]
        ldap.unbind()
    except LDAPException as exc:
        name = type(exc).__name__
        if "InvalidCredentials" in name or "BindError" in name:
            return None, {
                "code": "LDAP_AUTH",
                "message": "Authentification refusée par l'annuaire (compte de lecture ou mot de passe).",
                "next_step": "Vérifiez la connexion.",
            }
        if "SizeLimit" in name:
            entries = []
        elif "NoSuchObject" in name:
            return None, {
                "code": "LDAP_BASE",
                "message": "Racine de recherche introuvable dans l'annuaire.",
                "next_step": "Vérifiez la base (base_dn).",
            }
        else:
            return None, {
                "code": "LDAP_ERROR",
                "message": f"L'annuaire a refusé la recherche ({name}).",
                "next_step": "Vérifiez la connexion, le filtre et les droits.",
            }
    except OSError:
        return None, {
            "code": "LDAP_ERROR",
            "message": "L'annuaire n'a pas répondu (réseau, délai ou TLS).",
            "next_step": "Vérifiez que le serveur est joignable.",
        }
    truncated = len(entries) > spec["max_rows"]
    rows = [
        {"dn": e["dn"], **{k: _plain(v) for k, v in (e.get("attributes") or {}).items()}}
        for e in entries[: spec["max_rows"]]
    ]
    record(
        "ldap.search",
        workspace=sr.run.workspace,
        target=sr.run,
        host=target.host,
        rows=len(rows),
        ms=int((time.monotonic() - started) * 1000),
    )
    return {"rows": rows, "count": len(rows), "truncated": truncated, "simulated": False}, None
