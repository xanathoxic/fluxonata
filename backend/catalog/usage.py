"""Qui utilise une action ou un workflow ? Tout AUTRE workflow de l'espace qui l'appelle, dans son brouillon ou dans une version publiée.

Sert à deux choses : savoir si on peut supprimer (jamais tant qu'un workflow l'appelle, brouillon compris : il casserait) et dire à la personne
lesquels l'appellent. Les workflows qu'elle n'a pas le droit de voir (dossier privé) sont comptés (`hidden`) sans être nommés.
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


def users_of(ws, user, kind: str, slug: str) -> dict:
    """`kind` : "action" or "workflow". Returns {"used_by": [{slug, name, where: [...]}], "hidden": int, "total": int}."""
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
    for wv in (
        WorkflowVersion.objects.filter(workflow__workspace=ws).select_related("workflow").order_by("created_at", "id")
    ):
        if kind == "workflow" and wv.workflow.slug == slug:
            continue
        if any(uses(n, slug) for n in (wv.graph or {}).get("nodes") or []):
            found.setdefault(wv.workflow.slug, []).append(f"v{wv.version}")
    by_slug = {w.slug: w for w in workflows.values()}
    used_by, hidden = [], 0
    for wslug in sorted(found):
        w = by_slug[wslug]
        if rules.allows(w.folder, VIEW):
            used_by.append({"slug": wslug, "name": w.name, "where": found[wslug]})
        else:
            hidden += 1
    return {"used_by": used_by, "hidden": hidden, "total": len(used_by) + hidden}


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
