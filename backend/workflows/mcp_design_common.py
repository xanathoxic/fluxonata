"""Aides communes des outils MCP de conception : une FEUILLE (elle n'importe aucun outil, tous peuvent l'importer).

Réponses d'outil (`_ok`, `_err`), bornes et filtres de liste, description d'un outil (`_spec`), et ce que partagent les outils d'ÉCRITURE : la marque
« assistant-externe », l'audit de ce qui est touché, le droit de modifier et le conflit de révision."""

import json

from rest_framework.exceptions import PermissionDenied

from audit.models import record
from core.tags import clean_tags
from tenancy.permissions import EDIT, has_perm

DEFAULT_LIMIT, MAX_LIMIT = 25, 50
SLUG = {"type": "string", "description": "Identifiant (slug) de la ressource."}


def _ok(data) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
        "isError": False,
    }


def _err(message: str) -> dict:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def _limit(args) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(args.get("limit", DEFAULT_LIMIT))))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _text(args, key="query") -> str:
    v = args.get(key, "")
    return v.strip().lower()[:100] if isinstance(v, str) else ""


def _match(q: str, *parts) -> bool:
    return not q or q in " ".join(str(p or "") for p in parts).lower()


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def _spec(name, title, description, props=None, required=None):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {"type": "object", "properties": props or {}, **({"required": required} if required else {})},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    }


QUERY = {"query": {"type": "string", "description": "Texte recherché (nom, identifiant, description, étiquettes)."}}
LIMIT = {
    "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_LIMIT,
        "description": f"Nombre maximum de résultats (défaut {DEFAULT_LIMIT}).",
    }
}


ASSISTANT_TAG = "assistant-externe"


def _marked(tags) -> list[str]:
    """Les étiquettes normalisées + la marque « produit par un assistant externe » (filtrable dans l'interface)."""
    out = clean_tags(tags or [])
    return out if ASSISTANT_TAG in out else [*out, ASSISTANT_TAG][:20]


def _touched(request, ws, tool: str, target, **data):
    record(
        "mcp.design.write", actor=request.user, workspace=ws, target=target, tool=tool, external_assistant=True, **data
    )


def _need_edit(request, ws) -> None:
    if not has_perm(request.user, ws, EDIT):
        raise PermissionDenied("Droit de modifier requis (portée « edit » du jeton).")


def _conflict(current: int):
    from .views import Conflict

    return Conflict(
        {
            "message": "Le brouillon a été modifié ailleurs. Relisez-le (get_*) puis recommencez.",
            "current_revision": current,
        }
    )
