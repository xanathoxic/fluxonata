"""Temporary secret injection into an authorized action, plus output masking.

A secret is injected only if the action version declares a matching slot AND the execution maps that slot to a
connection of the same workspace usable in the execution's environment. Each injection is audited. Masking is a
complementary defence (it cannot stop malicious code from exfiltrating through other channels): the egress
policy and least privilege remain the real controls.
"""

import base64
import json
from urllib.parse import quote, quote_plus

from django.db.models import F, Q

from audit.models import record

from .external import ExternalVaultError
from .models import Connection


class InjectionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def resolve_env(execution, version, audit: bool = True) -> tuple[dict[str, str], list[str]]:
    """Returns ({ENV_VAR: value}, [values to mask]). Raises InjectionError. `audit=False` only to recompute the values to
    mask when the outcome comes back (the injection itself was audited when the job was handed out)."""
    env: dict[str, str] = {}
    for slot in version.secrets_required or []:
        name = (execution.connections or {}).get(slot["slot"])
        if not name:
            raise InjectionError("CONNECTION_MISSING", f"Connexion non fournie pour « {slot['slot']} ».")
        conn = (
            Connection.objects.select_related("secret")
            .filter(workspace_id=execution.workspace_id, name=name)
            .filter(Q(environment_id=execution.environment_id) | Q(environment__isnull=True))
            .order_by(F("environment_id").desc(nulls_last=True))
            .first()
        )
        if conn is None or conn.secret is None:
            raise InjectionError(
                "CONNECTION_MISSING", f"Connexion « {name} » introuvable ou sans secret pour cet environnement."
            )
        try:
            env[slot["env"]] = conn.secret.reveal()
        except ExternalVaultError as exc:
            raise InjectionError(exc.code, exc.message) from None
        if audit:
            record(
                "secret.inject",
                workspace=execution.workspace,
                target=conn,
                execution=str(execution.id),
                slot=slot["slot"],
            )
    return env, [v for v in env.values() if len(v) >= 4]


MIN_DERIVED = (
    6  # a derived form shorter than this would match ordinary text (the base64 of a 4-byte secret is 8 characters)
)


def secret_forms(secret: str) -> list[str]:
    """The secret and its common representations, longest first. Supported, and nothing else (no decoding, no recursion):
    standard and URL-safe base64 (with and without padding) of the UTF-8 bytes; percent-encoding (`%20`, and `+` for a space);
    the JSON-escaped string (ASCII-escaped and with accents kept). A form equal to the secret, or shorter than MIN_DERIVED,
    is dropped. Not covered: base64 of a longer text that contains the secret at a non-aligned offset, hexadecimal, other
    cases of percent-encoding (`%2f`), reversed or split values. Masking stays a complementary defence."""
    raw = secret.encode()
    b64, u64 = base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode()
    derived = {
        b64,
        b64.rstrip("="),
        u64,
        u64.rstrip("="),
        quote(secret, safe=""),
        quote_plus(secret, safe=""),
        json.dumps(secret)[1:-1],
        json.dumps(secret, ensure_ascii=False)[1:-1],
    }
    forms = {secret} | {f for f in derived if f != secret and len(f) >= MIN_DERIVED}
    return sorted(forms, key=len, reverse=True)


def all_forms(secrets: list[str]) -> list[str]:
    """Forms of every non-empty secret, without duplicates, longest first: when one form contains another, masking the short one
    first would leave the rest of the long one in clear."""
    return sorted({f for s in secrets if s for f in secret_forms(s)}, key=len, reverse=True)


def mask_text(text: str, secrets: list[str]) -> str:
    """Replaces every occurrence of each secret and of its supported forms (see `secret_forms`) by `***`. Empty values are ignored."""
    for form in all_forms(secrets):
        text = text.replace(form, "***")
    return text


def mask_json(value, secrets: list[str]):
    """Same on a JSON value. Works on the serialised text (escapes included); if the replacement ever broke the syntax, falls
    back to masking each string of the structure."""
    if not secrets or value is None:
        return value
    text = json.dumps(value)
    for form in all_forms(secrets):
        text = text.replace(json.dumps(form)[1:-1], "***")
    try:
        return json.loads(text)
    except ValueError:
        return _mask_leaves(value, secrets)


def _mask_leaves(value, secrets: list[str]):
    if isinstance(value, str):
        return mask_text(value, secrets)
    if isinstance(value, dict):
        return {mask_text(str(k), secrets): _mask_leaves(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_leaves(v, secrets) for v in value]
    return value
