"""Contrat du nœud « Cache » (ADR 0032) partagé par la validation (workflows) et l'exécution (runs.step_cache)."""

import re

OPERATIONS = ("read", "write", "delete")
MAX_TTL_SECONDS = 366 * 86400
ON_MISSING = ("empty", "fail", "rebuild")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")

STATIC_OUTPUTS = {
    "read": {
        "found": {"type": "boolean"},
        "value": {},
        "version": {"type": "integer"},
        "age_seconds": {"type": "integer"},
        "key": {"type": "string"},
        "rebuilt": {"type": "boolean"},
    },
    "write": {
        "written": {"type": "boolean"},
        "key": {"type": "string"},
        "version": {"type": "integer"},
        "size_bytes": {"type": "integer"},
        "expires_at": {"type": "string"},
    },
    "delete": {"deleted": {"type": "boolean"}, "key": {"type": "string"}},
}
