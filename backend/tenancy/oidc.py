"""OpenID Connect (authorization code + PKCE) with any standards-compliant free IdP (Keycloak, Authentik...).

Configuration comes from the environment (the client secret never lives in the repo or the DB). All IdP calls go
through core.netsec (an internal IdP must be listed in ORCH_EGRESS_ALLOWLIST). The id_token signature, issuer,
audience, expiry and nonce are verified; state and PKCE protect the redirect. Users are linked by (issuer, sub),
never by e-mail. Workspace roles are synchronised from the configured group claim.
"""

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from audit.models import record
from core import netsec

from .models import Membership, OidcIdentity, Role, Workspace

User = get_user_model()
ROLE_ORDER = [Role.ADMIN, Role.SPACE_MANAGER, Role.DESIGNER, Role.OPERATOR, Role.APPROVER, Role.VIEWER, Role.CATALOG]
STATE_TTL = 600


class OidcError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def enabled() -> bool:
    return bool(settings.ORCH_OIDC_ISSUER and settings.ORCH_OIDC_CLIENT_ID)


def _get_json(url: str) -> dict:
    try:
        status, _, body = netsec.safe_request(
            "GET", url, headers={"Accept": "application/json"}, timeout=5, max_bytes=262144
        )
        if status != 200:
            raise OidcError("Le fournisseur d'identité a répondu avec une erreur.")
        return json.loads(body)
    except (netsec.UnsafeURL, OSError, ValueError) as exc:
        raise OidcError(f"Fournisseur d'identité injoignable ou adresse refusée ({type(exc).__name__}).") from None


def discovery() -> dict:
    cached = cache.get("oidc:discovery")
    if cached:
        return cached
    issuer = settings.ORCH_OIDC_ISSUER.rstrip("/")
    doc = _get_json(f"{issuer}/.well-known/openid-configuration")
    if doc.get("issuer", "").rstrip("/") != issuer:
        raise OidcError("L'émetteur annoncé ne correspond pas à la configuration.")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if key not in doc:
            raise OidcError(f"Document de découverte incomplet ({key}).")
    cache.set("oidc:discovery", doc, 600)
    return doc


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def begin(request) -> str:
    doc = discovery()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    request.session["oidc"] = {"state": state, "nonce": nonce, "verifier": verifier, "at": time.time()}
    q = {
        "response_type": "code",
        "client_id": settings.ORCH_OIDC_CLIENT_ID,
        "redirect_uri": settings.ORCH_OIDC_REDIRECT_URI,
        "scope": settings.ORCH_OIDC_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{doc['authorization_endpoint']}?{urlencode(q)}"


def _exchange(doc: dict, code: str, verifier: str) -> dict:
    basic = base64.b64encode(f"{settings.ORCH_OIDC_CLIENT_ID}:{settings.ORCH_OIDC_CLIENT_SECRET}".encode()).decode()
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.ORCH_OIDC_REDIRECT_URI,
            "code_verifier": verifier,
        }
    ).encode()
    try:
        status, _, resp = netsec.safe_request(
            "POST",
            doc["token_endpoint"],
            body=body,
            timeout=8,
            max_bytes=262144,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        data = json.loads(resp)
    except (netsec.UnsafeURL, OSError, ValueError):
        raise OidcError("Échange du code impossible auprès du fournisseur d'identité.") from None
    if status != 200 or "id_token" not in data:
        raise OidcError("Le fournisseur d'identité a refusé l'échange du code.")
    return data


def _verify(doc: dict, id_token: str, nonce: str) -> dict:
    jwks = _get_json(doc["jwks_uri"])
    try:
        token = jwt.decode(id_token, KeySet.import_key_set(jwks), algorithms=["RS256", "ES256", "PS256"])
        registry = jwt.JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": settings.ORCH_OIDC_ISSUER.rstrip("/")},
            aud={"essential": True, "value": settings.ORCH_OIDC_CLIENT_ID},
            exp={"essential": True},
            sub={"essential": True},
        )
        registry.validate(token.claims)
    except (JoseError, ValueError, KeyError) as exc:
        raise OidcError(f"Jeton d'identité invalide ({type(exc).__name__}).") from None
    if token.claims.get("nonce") != nonce:
        raise OidcError("Jeton d'identité rejoué ou invalide (nonce).")
    return token.claims


def complete(request) -> User:
    sess = request.session.pop("oidc", None)
    if not sess or time.time() - sess["at"] > STATE_TTL:
        raise OidcError("Session de connexion expirée, recommencez.")
    if request.GET.get("error"):
        raise OidcError("Le fournisseur d'identité a refusé la connexion.")
    if not secrets.compare_digest(request.GET.get("state", ""), sess["state"]):
        raise OidcError("Paramètre state invalide (possible tentative de falsification).")
    code = request.GET.get("code")
    if not code:
        raise OidcError("Code d'autorisation manquant.")
    doc = discovery()
    claims = _verify(doc, _exchange(doc, code, sess["verifier"])["id_token"], sess["nonce"])
    return _sync_user(claims)


def _username(claims: dict) -> str:
    base = (
        re.sub(
            r"[^A-Za-z0-9._-]",
            "",
            str(claims.get("preferred_username") or str(claims.get("email", "")).split("@")[0] or "sso"),
        )[:30]
        or "sso"
    )
    name, i = f"sso.{base}", 1
    while User.objects.filter(username=name).exists():
        i += 1
        name = f"sso.{base}{i}"
    return name


@transaction.atomic
def _sync_user(claims: dict) -> User:
    issuer, sub = settings.ORCH_OIDC_ISSUER.rstrip("/"), str(claims["sub"])
    ident = OidcIdentity.objects.select_related("user").filter(issuer=issuer, subject=sub).first()
    if ident is None:
        if not settings.ORCH_OIDC_AUTO_CREATE:
            raise OidcError("Aucun compte local n'est associé à cette identité. Contactez un administrateur.")
        user = User(username=_username(claims), email=str(claims.get("email", ""))[:254])
        user.set_unusable_password()
        user.save()
        ident = OidcIdentity.objects.create(issuer=issuer, subject=sub, user=user)
        record("auth.sso_create", actor=user, issuer=issuer)
    user = ident.user
    if not user.is_active:
        raise OidcError("Ce compte est désactivé.")
    ident.last_login_at = timezone.now()
    ident.save(update_fields=["last_login_at"])
    _sync_roles(user, claims)
    return user


def _sync_roles(user, claims: dict) -> None:
    try:
        mapping = json.loads(settings.ORCH_OIDC_GROUP_ROLES or "{}")
    except ValueError:
        mapping = {}
    if not mapping:
        return
    groups = claims.get(settings.ORCH_OIDC_GROUPS_CLAIM) or []
    groups = [groups] if isinstance(groups, str) else list(groups)
    desired: dict[str, str] = {}
    managed = {m["workspace"] for m in mapping.values()}
    for g in groups:
        rule = mapping.get(g)
        if rule and rule.get("role") in Role.values:
            cur = desired.get(rule["workspace"])
            if cur is None or ROLE_ORDER.index(rule["role"]) < ROLE_ORDER.index(cur):
                desired[rule["workspace"]] = rule["role"]
    for slug in managed:
        ws = Workspace.objects.filter(slug=slug).first()
        if ws is None:
            continue
        role = desired.get(slug)
        existing = Membership.objects.filter(user=user, workspace=ws).first()
        if role is None:
            if existing:
                existing.delete()
                record("auth.sso_revoke", actor=user, workspace=ws)
        elif existing is None:
            Membership.objects.create(user=user, workspace=ws, role=role)
            record("auth.sso_grant", actor=user, workspace=ws, role=role)
        elif existing.role != role:
            existing.role = role
            existing.save()
            record("auth.sso_role", actor=user, workspace=ws, role=role)
