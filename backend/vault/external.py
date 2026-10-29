"""External secrets manager: HashiCorp Vault (KV v2, dynamic secrets), read AND write, one server for the whole platform.

How it fits in: a `Secret` row is either *internal* (encrypted in our database, as always) or *external*: a reference
(mount, path, key) to something held by Vault. Every consumer keeps calling `secret.reveal()`; for an external secret that
call comes here, right before use, server-side. The value is never stored by us (except short-lived encrypted dynamic
credentials, see `VaultLease`), never returned by the API and never written to a log or the audit trail.

Authentication is AppRole: the platform stores the `role_id` and the (encrypted) `secret_id`, logs in, keeps the client
token in memory and logs in again when it expires. Every call goes through `core.netsec` (no private addresses unless the
operator allowed the host, no redirects, pinned address).

Isolation between workspaces (they share one Vault): a workspace can only reach (1) its own area `<base_prefix>/<workspace>/`
of the KV engine, read and write, and (2) the path patterns a platform administrator granted it (`VaultGrant`).
"""

import json
import re
import secrets as pysecrets
import ssl
import threading
import time
from datetime import timedelta

from django.utils import timezone

from core import netsec

from . import crypto
from .models import Secret, VaultConfig, VaultGrant, VaultLease

TIMEOUT = 10
MAX_BYTES = 262144
KV_TTL = 10  # seconds a static value read from Vault may be reused in this process (SQL loops, retries...)
LEASE_MARGIN = timedelta(seconds=30)
LEASE_SHARE = 0.8  # keep dynamic credentials for 80 % of their lease: they must still be valid when used
SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PATTERN = re.compile(r"^[A-Za-z0-9._/*-]+$")


class ExternalVaultError(Exception):
    """`code` is stable (shown to people, matched by tests); `message` never contains a token or a secret value."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# --------------------------------------------------------------------------------------------------- configuration


def config() -> VaultConfig | None:
    return VaultConfig.objects.filter(pk=1).first()


def require_enabled() -> VaultConfig:
    cfg = config()
    if cfg is None or not cfg.address or not cfg.role_id or not cfg.secret_id_ciphertext:
        raise ExternalVaultError("VAULT_NOT_CONFIGURED", "Le coffre externe n'est pas configuré sur cette plate-forme.")
    if not cfg.enabled:
        raise ExternalVaultError(
            "VAULT_DISABLED", "Le coffre externe est désactivé par l'administrateur de la plate-forme."
        )
    return cfg


def available() -> bool:
    cfg = config()
    return bool(cfg and cfg.enabled and cfg.address and cfg.role_id and cfg.secret_id_ciphertext)


# ----------------------------------------------------------------------------------------------------- path policy


def clean_path(raw, field: str = "chemin") -> str:
    """Normalised path without leading/trailing slash. Refuses `..`, empty segments and anything but plain characters."""
    path = str(raw or "").strip().strip("/")
    if not path or len(path) > 200:
        raise ExternalVaultError("VAULT_PATH_REFUSED", f"Le {field} est requis (200 caractères au plus).")
    if not all(SEGMENT.match(seg) for seg in path.split("/")):
        raise ExternalVaultError(
            "VAULT_PATH_REFUSED",
            f"Le {field} ne peut contenir que lettres, chiffres, . _ - et / (pas de « .. » ni de segment vide).",
        )
    return path


def own_prefix(cfg: VaultConfig, workspace) -> str:
    return f"{cfg.base_prefix.strip('/')}/{workspace.slug}/"


def pattern_regex(pattern: str) -> re.Pattern:
    return re.compile("^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$")


def check_pattern(pattern: str) -> str:
    """Validates a grant pattern `mount/path*`: plain characters, a fixed mount, at least one fixed path segment."""
    p = str(pattern or "").strip().strip("/")
    if not p or len(p) > 200 or not PATTERN.match(p) or ".." in p or "//" in p:
        raise ExternalVaultError(
            "VAULT_PATTERN_REFUSED", "Motif invalide (lettres, chiffres, . _ - / et * ; pas de « .. »)."
        )
    parts = p.split("/")
    if len(parts) < 2 or "*" in parts[0] or p.startswith("*"):
        raise ExternalVaultError(
            "VAULT_PATTERN_REFUSED",
            "Le motif commence par le point de montage (sans *), puis un chemin : « kv/equipe-a/* ».",
        )
    return p


def allowed(cfg: VaultConfig, workspace, mount: str, path: str, write: bool = False) -> bool:
    """May this workspace read (or write) `mount/path`? Its own KV area is always read/write; the rest needs a grant."""
    if mount == cfg.kv_mount and path.startswith(own_prefix(cfg, workspace)):
        return True
    full = f"{mount}/{path}"
    for g in VaultGrant.objects.filter(workspace=workspace):
        if pattern_regex(g.pattern).match(full) and (g.writable or not write):
            return True
    return False


def require_allowed(cfg: VaultConfig, workspace, mount: str, path: str, write: bool = False) -> None:
    if not allowed(cfg, workspace, mount, path, write):
        what = "écrire dans" if write else "lire"
        raise ExternalVaultError(
            "VAULT_PATH_REFUSED",
            f"Cet espace n'a pas le droit de {what} « {mount}/{path} » : demandez à un administrateur de la plate-forme "
            f"de l'autoriser (Plate-forme → Coffre externe).",
        )


# --------------------------------------------------------------------------------------------------------- client

_lock = threading.Lock()
_token: dict = {"value": "", "until": 0.0, "fp": ""}
_kv_cache: dict = {}


def forget() -> None:
    """Drops the cached client token and values (configuration changed, secret written)."""
    with _lock:
        _token.update(value="", until=0.0, fp="")
        _kv_cache.clear()


def _fingerprint(cfg: VaultConfig) -> str:
    return f"{cfg.address}|{cfg.namespace}|{cfg.auth_mount}|{cfg.role_id}|{cfg.updated_at.isoformat() if cfg.updated_at else ''}"


def _context(cfg: VaultConfig) -> ssl.SSLContext | None:
    if not cfg.address.lower().startswith("https://"):
        return None
    try:
        ctx = ssl.create_default_context(cadata=cfg.ca_pem or None)
    except ssl.SSLError:
        raise ExternalVaultError(
            "VAULT_BAD_CA", "L'autorité de certification fournie est illisible (PEM attendu)."
        ) from None
    if not cfg.verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http(cfg: VaultConfig, method: str, path: str, body=None, token: str = "") -> tuple[int, dict]:
    url = f"{cfg.address.rstrip('/')}/v1/{path.lstrip('/')}"
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if cfg.namespace:
        headers["X-Vault-Namespace"] = cfg.namespace
    if token:
        headers["X-Vault-Token"] = token
    try:
        status, _, raw = netsec.safe_request(
            method,
            url,
            headers=headers,
            body=json.dumps(body).encode() if body is not None else None,
            timeout=TIMEOUT,
            max_bytes=MAX_BYTES,
            ssl_context=_context(cfg),
        )
    except netsec.UnsafeURL as exc:
        raise ExternalVaultError(
            "VAULT_BLOCKED",
            f"Adresse du coffre refusée ({exc}) ; pour un coffre sur le réseau interne, l'opérateur doit l'autoriser "
            f"(ORCH_EGRESS_ALLOWLIST).",
        ) from None
    except ssl.SSLError:
        raise ExternalVaultError(
            "VAULT_TLS",
            "Certificat du coffre refusé : fournissez son autorité de certification (Plate-forme → Coffre externe).",
        ) from None
    except OSError:
        raise ExternalVaultError("VAULT_UNREACHABLE", "Le coffre externe n'a pas répondu.") from None
    try:
        data = json.loads(raw.decode("utf-8", "replace")) if raw else {}
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def _errors(data: dict) -> str:
    errs = data.get("errors")
    return "; ".join(str(e) for e in errs)[:160] if isinstance(errs, list) and errs else ""


def _login(cfg: VaultConfig) -> str:
    status, data = _http(
        cfg, "POST", f"auth/{cfg.auth_mount.strip('/')}/login", {"role_id": cfg.role_id, "secret_id": cfg.secret_id()}
    )
    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    if status != 200 or not auth.get("client_token"):
        if status in (400, 403):
            raise ExternalVaultError(
                "VAULT_AUTH_FAILED",
                "Connexion AppRole refusée : vérifiez le role_id et le secret_id (ils ont peut-être expiré).",
            )
        raise ExternalVaultError("VAULT_UNAVAILABLE", f"Le coffre a répondu HTTP {status} à la connexion AppRole.")
    ttl = auth.get("lease_duration") or 0
    with _lock:
        _token.update(
            value=auth["client_token"],
            until=time.monotonic() + (max(int(ttl) - 30, 10) if ttl else 3600),
            fp=_fingerprint(cfg),
        )
    return auth["client_token"]


def _client_token(cfg: VaultConfig) -> str:
    with _lock:
        if _token["value"] and _token["fp"] == _fingerprint(cfg) and time.monotonic() < _token["until"]:
            return _token["value"]
    return _login(cfg)


def request(cfg: VaultConfig, method: str, path: str, body=None) -> tuple[int, dict]:
    """An authenticated call; logs in again once if the token was refused. Raises ExternalVaultError for any failure."""
    status, data = _http(cfg, method, path, body, _client_token(cfg))
    if status == 403 and "permission denied" in _errors(data).lower():
        # either the token expired early or the AppRole policy lacks this path: try a fresh login once
        forget()
        status, data = _http(cfg, method, path, body, _login(cfg))
    if status == 403:
        raise ExternalVaultError(
            "VAULT_FORBIDDEN",
            f"Vault refuse l'accès à « {path.split('?')[0]} » : la politique de l'AppRole ne l'autorise pas.",
        )
    if status == 404:
        raise ExternalVaultError("VAULT_NOT_FOUND", f"« {path.split('?')[0]} » n'existe pas dans Vault.")
    if status >= 500 or status == 429:
        raise ExternalVaultError("VAULT_UNAVAILABLE", f"Le coffre est indisponible (HTTP {status}).")
    if not 200 <= status < 300:
        raise ExternalVaultError(
            "VAULT_BAD_RESPONSE", f"Réponse inattendue du coffre (HTTP {status}) {_errors(data)}".strip()
        )
    return status, data


def kv_read(cfg: VaultConfig, mount: str, path: str, version: int | None = None) -> dict:
    key = (mount, path, version)
    with _lock:
        hit = _kv_cache.get(key)
        if hit and hit[0] > time.monotonic():
            return dict(hit[1])
    suffix = f"?version={int(version)}" if version else ""
    _, data = request(cfg, "GET", f"{mount}/data/{path}{suffix}")
    inner = (data.get("data") or {}).get("data")
    if not isinstance(inner, dict):
        raise ExternalVaultError("VAULT_NOT_FOUND", f"« {mount}/{path} » n'a pas de valeur courante dans Vault.")
    with _lock:
        if len(_kv_cache) > 256:
            _kv_cache.clear()
        _kv_cache[key] = (time.monotonic() + KV_TTL, dict(inner))
    return inner


def kv_write(cfg: VaultConfig, mount: str, path: str, values: dict) -> int:
    _, data = request(cfg, "POST", f"{mount}/data/{path}", {"data": values})
    with _lock:
        _kv_cache.clear()
    return int(((data.get("data") or {}).get("version")) or 0)


def kv_destroy(cfg: VaultConfig, mount: str, path: str) -> None:
    """Deletes the secret and ALL its versions (KV v2 metadata)."""
    try:
        request(cfg, "DELETE", f"{mount}/metadata/{path}")
    except ExternalVaultError as exc:
        if exc.code != "VAULT_NOT_FOUND":
            raise
    with _lock:
        _kv_cache.clear()


def read_dynamic(cfg: VaultConfig, path: str) -> tuple[dict, str, int]:
    _, data = request(cfg, "GET", path)
    values = data.get("data")
    if not isinstance(values, dict) or not values:
        raise ExternalVaultError("VAULT_BAD_RESPONSE", f"« {path} » n'a pas renvoyé d'identifiants.")
    return values, str(data.get("lease_id") or ""), int(data.get("lease_duration") or 0)


def revoke_lease(cfg: VaultConfig, lease_id: str) -> None:
    if lease_id:
        try:
            request(cfg, "PUT", "sys/leases/revoke", {"lease_id": lease_id})
        except ExternalVaultError:
            pass  # best effort: the lease ends by itself


# ------------------------------------------------------------------------------------------------------- secrets


def configure_managed(secret: Secret, cfg: VaultConfig) -> None:
    """Points a new secret at its own place in Vault: `<mount>/<base_prefix>/<workspace>/<name>`, key `value`."""
    secret.provider = Secret.Provider.VAULT
    secret.vault_mode = Secret.VaultMode.MANAGED
    secret.vault_mount = cfg.kv_mount
    secret.vault_path = f"{own_prefix(cfg, secret.workspace)}{secret.name}"
    secret.vault_key = "value"
    secret.vault_version = None


def configure_reference(
    secret: Secret, cfg: VaultConfig, mode: str, mount: str, path: str, key: str, version=None
) -> None:
    """Points a secret at something that already exists in Vault (static KV value or dynamic credentials)."""
    if mode not in (Secret.VaultMode.REFERENCE, Secret.VaultMode.DYNAMIC):
        raise ExternalVaultError("VAULT_BAD_MODE", "Mode inconnu (reference ou dynamic).")
    mount = clean_path(mount, "point de montage")
    path = clean_path(path)
    require_allowed(cfg, secret.workspace, mount, path)
    if version is not None and (not isinstance(version, int) or version < 1):
        raise ExternalVaultError("VAULT_BAD_MODE", "La version doit être un entier positif.")
    secret.provider = Secret.Provider.VAULT
    secret.vault_mode = mode
    secret.vault_mount, secret.vault_path = mount, path
    secret.vault_key = (key or ("password" if mode == Secret.VaultMode.DYNAMIC else "value")).strip()[:80]
    secret.vault_version = version if mode == Secret.VaultMode.REFERENCE else None


def _state(secret: Secret) -> VaultConfig:
    cfg = require_enabled()
    require_allowed(cfg, secret.workspace, secret.vault_mount, secret.vault_path)  # a revoked grant stops the reads
    return cfg


def write_secret(secret: Secret, value: str) -> None:
    cfg = require_enabled()
    mode = secret.vault_mode
    if mode == Secret.VaultMode.DYNAMIC:
        raise ExternalVaultError(
            "VAULT_READ_ONLY", "Des identifiants dynamiques sont fournis par Vault : on ne les écrit pas."
        )
    require_allowed(cfg, secret.workspace, secret.vault_mount, secret.vault_path, write=True)
    key = secret.vault_key or "value"
    values: dict = {}
    if mode == Secret.VaultMode.REFERENCE:  # keep the other keys of an existing secret
        try:
            values = dict(kv_read(cfg, secret.vault_mount, secret.vault_path))
        except ExternalVaultError as exc:
            if exc.code != "VAULT_NOT_FOUND":
                raise
    values[key] = value
    kv_write(cfg, secret.vault_mount, secret.vault_path, values)


def _lease_fields(cfg: VaultConfig, secret: Secret) -> dict[str, str]:
    now = timezone.now()
    VaultLease.objects.filter(secret=secret, expires_at__lte=now).delete()
    lease = VaultLease.objects.filter(secret=secret, expires_at__gt=now + LEASE_MARGIN).order_by("-expires_at").first()
    if lease is not None:
        return json.loads(crypto.decrypt(lease.ciphertext, f"{VaultLease.PURPOSE}:{secret.workspace_id}"))
    values, lease_id, seconds = read_dynamic(cfg, f"{secret.vault_mount}/{secret.vault_path}")
    fields = {str(k): str(v) for k, v in values.items()}
    if seconds > 0:
        key_id, token = crypto.encrypt(json.dumps(fields), f"{VaultLease.PURPOSE}:{secret.workspace_id}")
        VaultLease.objects.create(
            secret=secret,
            lease_id=lease_id,
            ciphertext=token,
            key_id=key_id,
            expires_at=now + timedelta(seconds=max(int(seconds * LEASE_SHARE), 1)),
        )
    return fields


def fields(secret: Secret) -> dict[str, str]:
    """All fields of an external secret (values as text). `value` is always present: the field named by `vault_key`."""
    cfg = _state(secret)
    if secret.vault_mode == Secret.VaultMode.DYNAMIC:
        data = _lease_fields(cfg, secret)
    else:
        data = {
            str(k): str(v) for k, v in kv_read(cfg, secret.vault_mount, secret.vault_path, secret.vault_version).items()
        }
    wanted = secret.vault_key or ("password" if secret.vault_mode == Secret.VaultMode.DYNAMIC else "value")
    if wanted not in data:
        raise ExternalVaultError(
            "VAULT_KEY_MISSING", f"La clé « {wanted} » est absente de « {secret.vault_mount}/{secret.vault_path} »."
        )
    return {**data, "value": data[wanted]}


def reveal(secret: Secret) -> str:
    return fields(secret)["value"]


def check(secret: Secret) -> dict:
    """Is the secret readable? Returns the NAMES of the fields it carries, never a value."""
    data = fields(secret)
    names = sorted(k for k in data if k != "value")
    return {"ok": True, "fields": names, "mode": secret.vault_mode}


def destroy(secret: Secret, purge: bool) -> None:
    """Before a secret row is deleted: end its dynamic leases; with `purge`, delete what Fluxonata wrote in Vault."""
    if not secret.is_external:
        return
    cfg = config()
    if cfg is None or not cfg.enabled:
        return
    for lease in secret.leases.all():
        revoke_lease(cfg, lease.lease_id)
    if purge and secret.vault_mode == Secret.VaultMode.MANAGED:
        kv_destroy(cfg, secret.vault_mount, secret.vault_path)


# ------------------------------------------------------------------------------------------------ platform checks


def test(cfg: VaultConfig) -> dict:
    """Walks through what Fluxonata needs and reports each step; nothing secret appears in the result."""
    steps: list[dict] = []

    def step(sid: str, label: str, fn):
        try:
            detail = fn()
            steps.append({"id": sid, "label": label, "ok": True, "message": detail or "ok"})
            return True
        except ExternalVaultError as exc:
            steps.append({"id": sid, "label": label, "ok": False, "message": exc.message, "code": exc.code})
            return False

    def health():
        status, data = _http(cfg, "GET", "sys/health?standbyok=true&sealedcode=200&uninitcode=200&perfstandbyok=true")
        if status != 200:
            raise ExternalVaultError("VAULT_UNAVAILABLE", f"Le coffre a répondu HTTP {status}.")
        if data.get("sealed"):
            raise ExternalVaultError("VAULT_SEALED", "Le coffre est scellé : un opérateur doit le desceller.")
        return f"Vault {data.get('version', '?')} joignable"

    forget()
    ok = step("reach", "Joindre le serveur", health)
    ok = ok and step("login", "Connexion AppRole", lambda: (_login(cfg), "jeton obtenu")[1])
    if ok:
        probe = f"{cfg.base_prefix.strip('/')}/_fluxonata_check/{pysecrets.token_hex(6)}"
        marker = pysecrets.token_hex(8)

        def write():
            kv_write(cfg, cfg.kv_mount, probe, {"value": marker})
            return f"écriture dans {cfg.kv_mount}/{cfg.base_prefix.strip('/')}/"

        def read():
            if kv_read(cfg, cfg.kv_mount, probe).get("value") != marker:
                raise ExternalVaultError("VAULT_BAD_RESPONSE", "La valeur relue ne correspond pas à celle écrite.")
            return "lecture"

        def cleanup():
            kv_destroy(cfg, cfg.kv_mount, probe)
            return "suppression"

        w = step("write", "Écrire un secret d'essai (KV v2)", write)
        r = w and step("read", "Relire ce secret", read)
        if w:
            step("delete", "Supprimer le secret d'essai", cleanup)
        ok = bool(w and r)
    forget()
    return {"ok": ok and all(s["ok"] for s in steps), "steps": steps}
