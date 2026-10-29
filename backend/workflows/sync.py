"""Workflows as documents: the shared core of « export / import all » and of the Git synchronisation.

Export: the drafts the person can see. Import: creates or replaces DRAFTS only (nothing is published or run), refuses what
the person may not edit (private folders), and checks the graph's structure first."""

import copy

from django.utils.text import slugify
from rest_framework import serializers

from audit.models import record
from core.folders import clean_folder
from core.tags import clean_tags
from tenancy.permissions import EDIT

from . import access
from .models import Workflow
from .validation import validate_graph


def _tags(raw) -> list[str]:
    """Tags of an imported file: kept when valid, ignored otherwise (a bad tag must not refuse a whole workflow)."""
    try:
        return clean_tags(raw)
    except serializers.ValidationError:
        return []


def export_documents(ws, user) -> list[dict]:
    out = []
    for wf in access.visible(Workflow.objects.filter(workspace=ws), user, ws).order_by("slug"):
        graph = copy.deepcopy(wf.draft_graph)
        graph.pop("pins", None)  # recorded outputs are data, not part of a definition
        latest = wf.latest_version()
        out.append(
            {
                "slug": wf.slug,
                "name": wf.name,
                "description": wf.description,
                "folder": wf.folder,
                "tags": wf.tags,
                "graph": graph,
                "published_version": latest.version if latest else None,
            }
        )
    return out


def import_documents(ws, user, items: list, overwrite: bool, dry: bool, mode: str = "sync") -> list[dict]:
    results = []
    rules = access.rules(user, ws)
    for it in items:
        slug = str(it.get("slug", "")) if isinstance(it, dict) else ""
        graph = it.get("graph") if isinstance(it, dict) else None
        if not slugify(slug) == slug or not slug or not isinstance(graph, dict):
            results.append({"slug": slug, "status": "refused", "reason": "Identifiant ou graphe invalide."})
            continue
        graph.pop("pins", None)
        bad = [
            i["message"]
            for i in validate_graph(graph, ws, slug)
            if i["code"] in ("graph_malformed", "bad_type", "too_many_nodes", "duplicate_id")
        ]
        if bad:
            results.append({"slug": slug, "status": "refused", "reason": bad[0]})
            continue
        wf = Workflow.objects.filter(workspace=ws, slug=slug).first()
        existed = wf is not None
        target_folder = clean_folder(it.get("folder", "")) if it.get("folder") else (wf.folder if wf else "")
        if not rules.allows(target_folder, EDIT) or (wf is not None and not rules.allows(wf.folder, EDIT)):
            results.append(
                {"slug": slug, "status": "refused", "reason": "Dossier privé : accès en modification requis."}
            )
            continue
        if existed and not overwrite:
            results.append({"slug": slug, "status": "skipped", "reason": "Existe déjà (utilisez overwrite)."})
            continue
        if not dry:
            fields = {
                "name": str(it.get("name") or slug)[:160],
                "description": str(it.get("description") or "")[:2000],
                "folder": clean_folder(it.get("folder", "")) if it.get("folder") else "",
                "tags": _tags(it.get("tags")),
                "draft_graph": graph,
            }
            if wf is None:
                wf = Workflow.objects.create(workspace=ws, slug=slug, owner=user, **fields)
            else:
                for k, v in fields.items():
                    setattr(wf, k, v)
                wf.draft_revision += 1
                wf.save()
            record("workflow.import", actor=user, workspace=ws, target=wf, mode=mode)
        verb = "update" if existed else "create"
        results.append({"slug": slug, "status": f"would_{verb}" if dry else f"{verb}d"})
    return results
