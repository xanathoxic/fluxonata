"""Qui utilise une action ou un workflow ? Tout AUTRE workflow de l'espace qui l'appelle, dans son brouillon ou dans une version publiée.

Sert à deux choses : savoir si on peut supprimer (jamais tant qu'un workflow l'appelle, brouillon compris : il casserait) et dire à la personne
lesquels l'appellent. Les workflows qu'elle n'a pas le droit de voir (dossier privé) sont comptés (`hidden`) sans être nommés.

Pour une ACTION, seul l'usage COURANT protège : le brouillon et la DERNIÈRE version publiée de chaque workflow. Une version plus ancienne qui l'appelle encore ne
bloque pas la suppression (le workflow a été republié depuis) : elle est listée à part (`older_versions`) et ne pourra plus s'exécuter. Pour un workflow appelé, toutes
les versions continuent de compter.
"""

from rest_framework.exceptions import APIException

from tenancy.permissions import VIEW
from workflows import access
from workflows.models import Workflow, WorkflowVersion

CALLS_WORKFLOW = ("subworkflow", "foreach", "while")


class InUse(APIException):
    """409 : l'élément est appelé par d'autres workflows ; `details.used_by` les liste."""

    status_code = 409
    default_code = "in_use"
    default_detail = "Cet élément est utilisé par d'autres workflows."

    def __init__(self, detail: dict):
        super().__init__(detail)
        self.detail = detail


def _uses_action(node: dict, slug: str) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("type") == "action" and (
        node.get("action") == slug or (node.get("compensation") or {}).get("action") == slug
    ):
        return True
    return node.get("type") == "agent" and slug in (
        node.get("tools") or []
    )  # an agent step may call an action as a tool


def _uses_workflow(node: dict, slug: str) -> bool:
    return isinstance(node, dict) and node.get("type") in CALLS_WORKFLOW and node.get("workflow") == slug


def _version_key(wv) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in wv.version.split("."))
    except ValueError:
        return (0,)


def _scan_versions(ws, kind: str, slug: str, uses, found: dict[str, list[str]]) -> dict[str, list[str]]:
    """Versions publiées qui appellent l'élément : celles qui comptent vont dans `found` ; pour une action, les versions remplacées vont dans le résultat."""
    versions = list(
        WorkflowVersion.objects.filter(workflow__workspace=ws).select_related("workflow").order_by("created_at", "id")
    )
    latest: dict[int, tuple[int, ...]] = {}
    for wv in versions:
        latest[wv.workflow_id] = max(latest.get(wv.workflow_id, (0,)), _version_key(wv))
    older: dict[str, list[str]] = {}
    for wv in versions:
        if kind == "workflow" and wv.workflow.slug == slug:
            continue
        if not any(uses(n, slug) for n in (wv.graph or {}).get("nodes") or []):
            continue
        replaced = kind == "action" and _version_key(wv) != latest[wv.workflow_id]
        (older if replaced else found).setdefault(wv.workflow.slug, []).append(f"v{wv.version}")
    return older


def users_of(ws, user, kind: str, slug: str) -> dict:
    """`kind` : "action" or "workflow". Returns {"used_by": [{slug, name, where: [...]}], "hidden": int, "total": int, "older_versions": [{slug, name, versions}]}."""
    uses = _uses_action if kind == "action" else _uses_workflow
    rules = access.rules(user, ws)
    found: dict[str, list[str]] = {}
    workflows = {w.pk: w for w in Workflow.objects.filter(workspace=ws)}
    for w in workflows.values():
        if kind == "workflow" and w.slug == slug:
            continue  # a workflow never "uses" itself for deletion purposes
        nodes = (w.draft_graph or {}).get("nodes") or []
        where = ["brouillon"] if any(uses(n, slug) for n in nodes) else []
        if kind == "workflow" and slug in (w.error_workflow, w.success_workflow):
            where.append("workflow d'erreur" if w.error_workflow == slug else "workflow de succès")
        if where:
            found[w.slug] = where
    older = _scan_versions(ws, kind, slug, uses, found)
    by_slug = {w.slug: w for w in workflows.values()}
    older_versions = [
        {"slug": s, "name": by_slug[s].name, "versions": v}
        for s, v in sorted(older.items())
        if rules.allows(by_slug[s].folder, VIEW)
    ]
    used_by, hidden = [], 0
    for wslug in sorted(found):
        w = by_slug[wslug]
        if rules.allows(w.folder, VIEW):
            used_by.append({"slug": wslug, "name": w.name, "where": found[wslug]})
        else:
            hidden += 1
    return {"used_by": used_by, "hidden": hidden, "total": len(used_by) + hidden, "older_versions": older_versions}


def refuse_if_used(ws, user, kind: str, slug: str, noun: str) -> None:
    usage = users_of(ws, user, kind, slug)
    if usage["total"]:
        n = usage["total"]
        raise InUse(
            {
                "message": f"Suppression refusée : {noun} est utilisé par {n} workflow{'s' if n > 1 else ''}.",
                "used_by": usage["used_by"],
                "hidden": usage["hidden"],
            }
        )
