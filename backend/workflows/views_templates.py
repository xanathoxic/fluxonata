import copy
import json

from django.http import Http404
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core.tags import clean_tags
from tenancy.permissions import EDIT, VIEW, get_workspace

from . import access, sync, templates
from .models import Workflow, WorkflowTemplate
from .validation import validate_graph

MAX_IMPORT = 600 * 1024


def _free_slug(ws, base: str) -> str:
    base = (slugify(base) or "workflow")[:40]
    slug, i = base, 2
    while Workflow.objects.filter(workspace=ws, slug=slug).exists():
        slug, i = f"{base}-{i}", i + 1
    return slug


def _create(ws, user, name: str, graph: dict, description: str = "") -> Workflow:
    wf = Workflow.objects.create(
        workspace=ws,
        slug=_free_slug(ws, name),
        name=name[:160],
        description=description[:2000],
        owner=user,
        draft_graph=graph,
    )
    record("workflow.create", actor=user, workspace=ws, target=wf, source="template")
    return wf


class TemplateListView(APIView):
    """Built-in starter workflows and the workspace's own templates."""

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        own = [
            {
                "slug": f"ws:{t.pk}",
                "name": t.name,
                "category": "Mes modèles",
                "description": t.description,
                "requires": [],
                "steps": len(t.graph.get("nodes", [])),
            }
            for t in WorkflowTemplate.objects.filter(workspace=ws).order_by("name")
        ]
        return Response({"results": [*(templates.summary(t) for t in templates.TEMPLATES), *own]})


class TemplateUseView(APIView):
    """Creates a new DRAFT workflow from a template (nothing is published or run)."""

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        if slug.startswith("ws:"):
            t = WorkflowTemplate.objects.filter(workspace=ws, pk=slug[3:] if slug[3:].isdigit() else 0).first()
            if t is None:
                raise Http404()
            graph, name, desc = copy.deepcopy(t.graph), t.name, t.description
            tags: list[str] = []
        else:
            t = templates.INDEX.get(slug)
            if t is None:
                raise Http404()
            graph, name, desc = copy.deepcopy(t["graph"]), t["name"], t["description"]
            tags = [str(t.get("category", "")).lower(), *t.get("requires", [])]  # what a built-in template is about
        name = str(request.data.get("name") or name).strip()[:160]
        wf = _create(ws, request.user, name, graph, desc)
        if not slug.startswith("ws:"):
            wf.tags = clean_tags([x for x in tags if x])
            wf.save(update_fields=["tags"])
        return Response({"slug": wf.slug, "name": wf.name}, status=201)


class SaveAsTemplateView(APIView):
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        wf = access.workflow_or_404(request.user, ws, slug, EDIT)
        name = str(request.data.get("name") or wf.name).strip()[:160]
        graph = copy.deepcopy(wf.draft_graph)
        graph.pop("pins", None)  # recorded outputs are data, not part of a model
        t = WorkflowTemplate.objects.create(
            workspace=ws, name=name, description=wf.description, graph=graph, created_by=request.user
        )
        record("workflow_template.create", actor=request.user, workspace=ws, target=t, workflow=wf.slug)
        return Response({"slug": f"ws:{t.pk}", "name": t.name}, status=201)


class TemplateDeleteView(APIView):
    def delete(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, EDIT)
        t = WorkflowTemplate.objects.filter(workspace=ws, pk=pk).first()
        if t is None:
            raise Http404()
        record("workflow_template.delete", actor=request.user, workspace=ws, target=t)
        t.delete()
        return Response(status=204)


class WorkflowExportView(APIView):
    """The draft as a JSON document (format `fluxonata-workflow/1`), to move a workflow between instances."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = access.workflow_or_404(request.user, ws, slug)
        graph = copy.deepcopy(wf.draft_graph)
        graph.pop("pins", None)
        return Response(
            {"format": "fluxonata-workflow/1", "name": wf.name, "description": wf.description, "graph": graph}
        )


class WorkflowImportView(APIView):
    """Creates a DRAFT from an exported document. The graph is checked like any draft; nothing is published or run."""

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        doc = request.data
        if (
            not isinstance(doc, dict)
            or doc.get("format") != "fluxonata-workflow/1"
            or not isinstance(doc.get("graph"), dict)
        ):
            raise serializers.ValidationError({"detail": "Document inconnu : format « fluxonata-workflow/1 » attendu."})
        if len(json.dumps(doc)) > MAX_IMPORT:
            raise serializers.ValidationError({"detail": "Document trop volumineux."})
        graph = doc["graph"]
        graph.pop("pins", None)
        problems = [
            i
            for i in validate_graph(graph, ws, "import")
            if i["code"] in ("graph_malformed", "bad_type", "too_many_nodes", "duplicate_id")
        ]
        if problems:
            raise serializers.ValidationError({"graph": [p["message"] for p in problems][:5]})
        wf = _create(
            ws, request.user, str(doc.get("name") or "Workflow importé"), graph, str(doc.get("description") or "")
        )
        record("workflow.import", actor=request.user, workspace=ws, target=wf)
        return Response({"slug": wf.slug, "name": wf.name}, status=201)


class ExportAllView(APIView):
    """Every workflow of the workspace as one document (drafts, folder, description): « workflows as code »."""

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        out = sync.export_documents(ws, request.user)
        return Response({"format": "fluxonata-workflows/1", "workflows": out})


class ImportAllView(APIView):
    """Creates the workflows of an exported document (as DRAFTS). An existing slug is replaced only with
    `overwrite: true` (its draft revision moves on, so an open editor sees a conflict instead of silently losing work).
    Nothing is published or run. `dry_run: true` reports without writing."""

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        doc = request.data
        items = doc.get("workflows") if isinstance(doc, dict) and doc.get("format") == "fluxonata-workflows/1" else None
        if not isinstance(items, list) or len(items) > 200 or len(json.dumps(doc)) > 8 * MAX_IMPORT:
            raise serializers.ValidationError(
                {
                    "detail": "Document inconnu ou trop volumineux (format « fluxonata-workflows/1 », 200 workflows au plus)."
                }
            )
        overwrite, dry = bool(doc.get("overwrite")), bool(doc.get("dry_run"))
        results = sync.import_documents(ws, request.user, items, overwrite, dry)
        return Response({"results": results, "dry_run": dry})
