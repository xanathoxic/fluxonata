"""Réglages d'un fournisseur d'identité : validation stricte, secret chiffré, adresses publiques.

Rien de secret n'est renvoyé par l'API : le secret du client OIDC est chiffré par le coffre (`vault.crypto`, usage lié au fournisseur) et seul
un indicateur « présent » en sort. Les certificats SAML sont des certificats PUBLICS (on vérifie une signature avec eux)."""

import re
from urllib.parse import urlparse

from django.conf import settings

from core import sealing

from .models import IdentityProvider

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
RESERVED = {"environment", "config", "providers", "login", "callback", "acs", "metadata"}
DEFAULT_CLAIMS = {"username": "preferred_username", "email": "email", "name": "name", "groups": "groups"}
DEFAULT_SAML_ATTRS = {
    "username": "uid",
    "email": "mail",
    "name": "displayName",
    "groups": "memberOf",
}
NAME_ID_FORMATS = {
    "": "Selon le fournisseur",
    "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified": "Non spécifié",
    "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress": "Adresse e-mail",
    "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent": "Persistant",
    "urn:oasis:names:tc:SAML:2.0:nameid-format:transient": "Transitoire",
}
MAX_CERT = 8000
MAX_DOMAINS = 30
_CLAIM = re.compile(r"^[A-Za-z0-9_.:/@\-\[\]]{1,200}$")


def _url(value, *, secure_only=False) -> bool:
    if not isinstance(value, str) or len(value) > 500:
        return False
    u = urlparse(value)
    return (
        u.scheme in (("https",) if secure_only else ("https", "http"))
        and bool(u.hostname)
        and not u.username
        and not u.fragment
    )


def _text(value, limit=200) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def _mapping(raw, defaults: dict, label: str, problems: dict) -> dict:
    out = dict(defaults)
    if raw is None:
        return out
    if not isinstance(raw, dict) or set(raw) - set(defaults):
        problems[label] = "Correspondance invalide."
        return out
    for key, value in raw.items():
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not _CLAIM.match(value):
            problems[f"{label}.{key}"] = "Nom d'attribut invalide."
        else:
            out[key] = value
    return out


def _domains(raw, problems: dict) -> list[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_DOMAINS:
        problems["allowed_domains"] = f"Une liste de {MAX_DOMAINS} domaines au plus."
        return []
    out = []
    for d in raw:
        d = str(d).strip().lower()
        if not re.match(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$", d) or "." not in d:
            problems["allowed_domains"] = f"Domaine invalide : « {d} »."
            return []
        out.append(d)
    return out


def _clean_oidc(data: dict, problems: dict) -> dict:
    issuer = data.get("issuer", "")
    if not _url(issuer):
        problems["issuer"] = (
            "Adresse de l'émetteur (issuer) attendue, en https:// (http:// accepté pour un essai local)."
        )
    if not _text(data.get("client_id"), 200):
        problems["client_id"] = "Identifiant du client (client_id) obligatoire."
    scopes = str(data.get("scopes") or "openid profile email").strip()
    if "openid" not in scopes.split() or len(scopes) > 300 or not re.match(r"^[A-Za-z0-9_.:/\- ]+$", scopes):
        problems["scopes"] = "Les scopes doivent contenir « openid »."
    auth = data.get("token_auth") or "basic"
    if auth not in ("basic", "post"):
        problems["token_auth"] = "Méthode d'authentification : « basic » ou « post »."  # noqa: S105 - texte d'erreur, pas un secret
    return {
        "issuer": str(issuer).rstrip("/"),
        "client_id": str(data.get("client_id", "")).strip(),
        "scopes": scopes,
        "token_auth": auth,
        "pkce": bool(data.get("pkce", True)),
        "claims": _mapping(data.get("claims"), DEFAULT_CLAIMS, "claims", problems),
        "allowed_domains": _domains(data.get("allowed_domains"), problems),
    }


def _pem_ok(pem) -> bool:
    return isinstance(pem, str) and len(pem) <= MAX_CERT and "BEGIN CERTIFICATE" in pem and "PRIVATE" not in pem


def _clean_saml(data: dict, problems: dict) -> dict:
    if not _text(data.get("idp_entity_id"), 500):
        problems["idp_entity_id"] = "Identifiant du fournisseur (entityID) obligatoire."
    if not _url(data.get("idp_sso_url")):
        problems["idp_sso_url"] = "Adresse de connexion (SSO URL, liaison HTTP-Redirect) attendue."
    certs = data.get("idp_certs")
    if not isinstance(certs, list) or not 1 <= len(certs) <= 4 or not all(_pem_ok(c) for c in certs):
        problems["idp_certs"] = (
            "Un à quatre certificats de signature du fournisseur (PEM, certificats publics) sont obligatoires."
        )
    sp = data.get("sp_entity_id") or ""
    if sp and not _text(sp, 500):
        problems["sp_entity_id"] = "Identifiant de ce service invalide."
    fmt = data.get("name_id_format") or ""
    if fmt not in NAME_ID_FORMATS:
        problems["name_id_format"] = "Format d'identifiant inconnu."
    skew = data.get("clock_skew", 60)
    if not isinstance(skew, int) or isinstance(skew, bool) or not 0 <= skew <= 300:
        problems["clock_skew"] = "Tolérance d'horloge : 0 à 300 secondes."
    return {
        "idp_entity_id": str(data.get("idp_entity_id", "")).strip(),
        "idp_sso_url": str(data.get("idp_sso_url", "")).strip(),
        "idp_certs": [c.strip() for c in certs]
        if isinstance(certs, list) and all(isinstance(c, str) for c in certs)
        else [],
        "sp_entity_id": sp.strip(),
        "name_id_format": fmt,
        "force_authn": bool(data.get("force_authn", False)),
        "clock_skew": skew if isinstance(skew, int) and not isinstance(skew, bool) else 60,
        "attributes": _mapping(data.get("attributes"), DEFAULT_SAML_ATTRS, "attributes", problems),
        "allowed_domains": _domains(data.get("allowed_domains"), problems),
    }


def clean(kind: str, data) -> tuple[dict, dict[str, str]]:
    """(réglages normalisés, {champ: message}) ; les réglages sont inutilisables tant qu'il y a un problème."""
    problems: dict[str, str] = {}
    if not isinstance(data, dict):
        return {}, {"config": "Un objet est attendu."}
    cfg = _clean_oidc(data, problems) if kind == IdentityProvider.Kind.OIDC else _clean_saml(data, problems)
    return cfg, problems


def check_slug(slug: str, current: str | None = None) -> str:
    if not SLUG_RE.match(slug or "") or slug in RESERVED:
        return "Identifiant de 3 à 40 caractères : minuscules, chiffres et tirets."
    if slug != current and IdentityProvider.objects.filter(slug=slug).exists():
        return "Cet identifiant est déjà pris."
    return ""


# ---- secret -----------------------------------------------------------------------------------------------------------------
def _purpose(provider: IdentityProvider) -> str:
    return f"sso:{provider.slug}"


def set_secret(provider: IdentityProvider, value: str) -> None:
    # le secret est chiffré par le coffre (par `core.sealing`), comme tout secret de la plate-forme
    provider.secret_key_id, provider.secret_token = sealing.seal(value, _purpose(provider)) if value else ("", "")


def get_secret(provider: IdentityProvider) -> str:
    return sealing.unseal(provider.secret_token, _purpose(provider)) if provider.secret_token else ""


# ---- adresses publiques -----------------------------------------------------------------------------------------------------
def base_url(request=None) -> str:
    if settings.ORCH_PUBLIC_URL:
        return settings.ORCH_PUBLIC_URL.rstrip("/")
    return request.build_absolute_uri("/").rstrip("/") if request is not None else ""


def endpoints(provider: IdentityProvider, request=None) -> dict[str, str]:
    base = f"{base_url(request)}/api/v1/auth/sso/{provider.slug}"
    out = {"login": f"{base}/login/"}
    if provider.kind == IdentityProvider.Kind.OIDC:
        out["callback"] = f"{base}/callback/"
    else:
        out.update(
            {
                "acs": f"{base}/acs/",
                "metadata": f"{base}/metadata/",
                "entity_id": (provider.config or {}).get("sp_entity_id") or f"{base}/metadata/",
            }
        )
    return out
