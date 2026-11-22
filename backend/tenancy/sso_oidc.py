"""OpenID Connect (code d'autorisation + PKCE) pour un fournisseur réglé dans l'interface.

Même durcissement que `oidc.py` (réglé par l'environnement) : tous les appels passent par `core.netsec`, la signature, l'émetteur, l'audience,
l'expiration et le nonce du jeton d'identité sont vérifiés, `state` et PKCE protègent la redirection. Les noms des revendications (identifiant,
e-mail, nom, groupes) sont ceux réglés pour ce fournisseur."""

import base64
import json
import secrets
import time
from typing import cast
from urllib.parse import urlencode

from django.core.cache import cache
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, KeySetSerialization

from core import netsec

from . import sso_config
from .models import IdentityProvider
from .oidc import STATE_TTL, OidcError, _get_json, _pkce
from .sso_engine import Identity, clean_groups


def discovery(provider: IdentityProvider, *, fresh: bool = False) -> dict:
    key = f"sso:discovery:{provider.slug}:{provider.updated_at.timestamp()}"
    if not fresh and (cached := cache.get(key)):
        return cached
    issuer = provider.config["issuer"].rstrip("/")
    doc = _get_json(f"{issuer}/.well-known/openid-configuration")
    if doc.get("issuer", "").rstrip("/") != issuer:
        raise OidcError("L'émetteur annoncé ne correspond pas à la configuration.")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if k not in doc:
            raise OidcError(f"Document de découverte incomplet ({k}).")
    cache.set(key, doc, 600)
    return doc


def begin(request, provider: IdentityProvider) -> str:
    cfg = provider.config
    doc = discovery(provider)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    request.session["sso"] = {
        "slug": provider.slug,
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "at": time.time(),
    }
    q = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": sso_config.endpoints(provider, request)["callback"],
        "scope": cfg["scopes"],
        "state": state,
        "nonce": nonce,
    }
    if cfg.get("pkce", True):
        q |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{doc['authorization_endpoint']}?{urlencode(q)}"


def _exchange(request, provider: IdentityProvider, doc: dict, code: str, verifier: str) -> dict:
    cfg = provider.config
    secret = sso_config.get_secret(provider)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": sso_config.endpoints(provider, request)["callback"],
    }
    if cfg.get("pkce", True):
        form["code_verifier"] = verifier
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if cfg.get("token_auth", "basic") == "basic" and secret:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{cfg['client_id']}:{secret}".encode()).decode()
    else:
        form["client_id"] = cfg["client_id"]
        if secret:
            form["client_secret"] = secret
    try:
        status, _, resp = netsec.safe_request(
            "POST", doc["token_endpoint"], body=urlencode(form).encode(), timeout=8, max_bytes=262144, headers=headers
        )
        data = json.loads(resp)
    except (netsec.UnsafeURL, OSError, ValueError):
        raise OidcError("Échange du code impossible auprès du fournisseur d'identité.") from None
    if status != 200 or "id_token" not in data:
        raise OidcError("Le fournisseur d'identité a refusé l'échange du code.")
    return data


def _verify(provider: IdentityProvider, doc: dict, id_token: str, nonce: str) -> dict:
    jwks = _get_json(doc["jwks_uri"])
    try:
        token = jwt.decode(
            id_token, KeySet.import_key_set(cast(KeySetSerialization, jwks)), algorithms=["RS256", "ES256", "PS256"]
        )
        registry = jwt.JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": provider.config["issuer"].rstrip("/")},
            aud={"essential": True, "value": provider.config["client_id"]},
            exp={"essential": True},
            sub={"essential": True},
        )
        registry.validate(token.claims)
    except (JoseError, ValueError, KeyError) as exc:
        raise OidcError(f"Jeton d'identité invalide ({type(exc).__name__}).") from None
    if token.claims.get("nonce") != nonce:
        raise OidcError("Jeton d'identité rejoué ou invalide (nonce).")
    return token.claims


def identity_from_claims(provider: IdentityProvider, claims: dict) -> Identity:
    names = {**sso_config.DEFAULT_CLAIMS, **(provider.config.get("claims") or {})}
    return Identity(
        provider=provider,
        issuer=provider.config["issuer"].rstrip("/"),
        subject=str(claims["sub"]),
        username=str(claims.get(names["username"]) or ""),
        email=str(claims.get(names["email"]) or ""),
        name=str(claims.get(names["name"]) or ""),
        groups=clean_groups(claims.get(names["groups"])),
    )


def complete(request, provider: IdentityProvider) -> Identity:
    sess = request.session.pop("sso", None)
    if not sess or sess.get("slug") != provider.slug or time.time() - sess["at"] > STATE_TTL:
        raise OidcError("Session de connexion expirée, recommencez.")
    if request.GET.get("error"):
        raise OidcError("Le fournisseur d'identité a refusé la connexion.")
    if not secrets.compare_digest(request.GET.get("state", ""), sess["state"]):
        raise OidcError("Paramètre state invalide (possible tentative de falsification).")
    code = request.GET.get("code")
    if not code:
        raise OidcError("Code d'autorisation manquant.")
    doc = discovery(provider)
    claims = _verify(
        provider, doc, _exchange(request, provider, doc, code, sess["verifier"])["id_token"], sess["nonce"]
    )
    return identity_from_claims(provider, claims)


def test(provider: IdentityProvider) -> dict:
    """Essai sans connexion : le document de découverte, les clés de signature et les points d'accès annoncés."""
    doc = discovery(provider, fresh=True)
    jwks = _get_json(doc["jwks_uri"])
    keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
    return {
        "issuer": doc["issuer"],
        "authorization_endpoint": doc["authorization_endpoint"],
        "token_endpoint": doc["token_endpoint"],
        "signing_keys": len(keys),
        "pkce_supported": "S256" in (doc.get("code_challenge_methods_supported") or ["S256"]),
        "scopes_supported": doc.get("scopes_supported") or [],
        "claims_supported": doc.get("claims_supported") or [],
    }
