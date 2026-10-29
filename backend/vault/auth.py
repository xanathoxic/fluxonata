"""How a connection authenticates a call to a web service. Server-side only: the worker asks `credentials(conn)` right
before sending, the result is used for that one request and never stored on a step.

Kinds: bearer token, API key (header or query parameter), basic (identifier in the settings, password in the vault),
OAuth2 client credentials (token fetched and cached, encrypted), OAuth2 authorization code (a person authorizes once; the
refresh token keeps the connection alive)."""

import base64
import json
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlencode

from django.utils import timezone

from core import netsec

from . import external
from .models import Connection, OAuthToken

HTTP_KINDS = (
    Connection.Kind.HTTP_BEARER,
    Connection.Kind.API_KEY,
    Connection.Kind.BASIC,
    Connection.Kind.OAUTH2_CLIENT,
    Connection.Kind.OAUTH2_CODE,
    Connection.Kind.AWS_SIGV4,
)
OAUTH_KINDS = (Connection.Kind.OAUTH2_CLIENT, Connection.Kind.OAUTH2_CODE)
EXPIRY_MARGIN = timedelta(seconds=45)


class AuthError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Credentials:
    headers: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    secrets: list = field(default_factory=list)  # values to mask in whatever comes back
    signer: object = None  # callable(method, url, headers, body) -> headers, for request-signing schemes (SigV4)


def _secret(conn) -> str:
    if conn.secret is None:
        raise AuthError("CONNECTION_NO_SECRET", "Aucun secret n'est associé à cette connexion.")
    try:
        return conn.secret.reveal()
    except external.ExternalVaultError as exc:
        raise AuthError(exc.code, exc.message) from None
    except Exception:  # noqa: BLE001
        raise AuthError("CONNECTION_SECRET_UNREADABLE", "Le secret de la connexion est illisible.") from None


def _token_request(conn, form: dict) -> dict:
    cfg = conn.config or {}
    try:
        status, _, raw = netsec.safe_request(
            "POST",
            cfg["token_url"],
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            body=urlencode(form).encode(),
            timeout=10,
            max_bytes=65536,
        )
    except netsec.UnsafeURL as exc:
        raise AuthError("OAUTH_BLOCKED", f"Adresse d'authentification refusée : {exc}") from None
    except OSError:
        raise AuthError("OAUTH_UNREACHABLE", "Le serveur d'authentification n'a pas répondu.") from None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        data = {}
    if not 200 <= status < 300 or not isinstance(data, dict) or not data.get("access_token"):
        reason = (
            data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), str) else f"HTTP {status}"
        )
        raise AuthError("OAUTH_REFUSED", f"Le serveur d'authentification a refusé la demande ({reason[:80]}).")
    return data


def _store(conn, data: dict, user=None) -> OAuthToken:
    tok, _ = OAuthToken.objects.get_or_create(connection=conn)
    previous_refresh = tok.refresh() if tok.refresh_ciphertext else None
    tok.set_tokens(
        str(data["access_token"]), str(data["refresh_token"]) if data.get("refresh_token") else previous_refresh
    )
    seconds = data.get("expires_in")
    tok.expires_at = timezone.now() + timedelta(seconds=int(seconds)) if isinstance(seconds, (int, float)) else None
    tok.scope = str(data.get("scope") or (conn.config or {}).get("scope", ""))[:300]
    if user is not None:
        tok.authorized_by = user
    tok.save()
    return tok


def oauth_access_token(conn) -> str:
    """A valid access token, refreshed (or requested) when it is missing or about to expire."""
    tok = OAuthToken.objects.filter(connection=conn).first()
    if tok and tok.access_ciphertext and (tok.expires_at is None or tok.expires_at > timezone.now() + EXPIRY_MARGIN):
        return tok.access()
    cfg = conn.config or {}
    form = {"client_id": cfg.get("client_id", ""), "client_secret": _secret(conn)}
    if conn.kind == Connection.Kind.OAUTH2_CLIENT:
        form["grant_type"] = "client_credentials"
        if cfg.get("scope"):
            form["scope"] = cfg["scope"]
    else:
        refresh = tok.refresh() if tok else ""
        if not refresh:
            raise AuthError(
                "OAUTH_NOT_AUTHORIZED",
                "Cette connexion n'a pas encore été autorisée : lancez « Autoriser » dans Connexions.",
            )
        form.update(grant_type="refresh_token", refresh_token=refresh)
    return _store(conn, _token_request(conn, form)).access()


def exchange_code(conn, code: str, redirect_uri: str, user) -> None:
    cfg = conn.config or {}
    data = _token_request(
        conn,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": cfg.get("client_id", ""),
            "client_secret": _secret(conn),
        },
    )
    _store(conn, data, user)


def credentials(conn) -> Credentials:
    cfg = conn.config or {}
    kind = conn.kind
    if kind == Connection.Kind.HTTP_BEARER:
        s = _secret(conn)
        return Credentials({"Authorization": f"Bearer {s}"}, {}, [s])
    if kind == Connection.Kind.API_KEY:
        s, name = _secret(conn), cfg.get("name") or "X-API-Key"
        if cfg.get("in") == "query":
            return Credentials({}, {name: s}, [s])
        value = f"{cfg.get('prefix', '')}{s}"
        return Credentials({name: value}, {}, [s, value])
    if kind == Connection.Kind.BASIC:
        s = _secret(conn)
        user = conn.login()[0]  # a dynamic Vault credential carries its own user name
        raw = base64.b64encode(f"{user}:{s}".encode()).decode()
        return Credentials({"Authorization": f"Basic {raw}"}, {}, [s, raw])
    if kind == Connection.Kind.AWS_SIGV4:
        from . import sigv4

        s = _secret(conn)
        service = cfg.get("service") or "s3"

        def signer(method, url, headers, body):
            return sigv4.sign(
                method,
                url,
                headers,
                body,
                access_key=cfg.get("access_key_id", ""),
                secret_key=s,
                region=cfg.get("region") or "us-east-1",
                service=service,
                content_sha_header=service == "s3",
            )

        return Credentials({}, {}, [s], signer)
    if kind in OAUTH_KINDS:
        t = oauth_access_token(conn)
        return Credentials({"Authorization": f"Bearer {t}"}, {}, [t])
    raise AuthError("CONNECTION_KIND", "Ce type de connexion ne peut pas appeler un service HTTP.")
