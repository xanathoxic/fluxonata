"""Temporary secret injection into an authorized action, plus output masking.

A secret is injected only if the action version declares a matching slot AND the execution maps that slot to a
connection of the same workspace usable in the execution's environment. Each injection is audited. Masking is a
complementary defence (it cannot stop malicious code from exfiltrating through other channels): the egress
policy and least privilege remain the real controls.
"""

import json

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


def mask_text(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "***")
    return text


def mask_json(value, secrets: list[str]):
    if not secrets or value is None:
        return value
    return json.loads(mask_text(json.dumps(value), [json.dumps(s)[1:-1] for s in secrets]))
