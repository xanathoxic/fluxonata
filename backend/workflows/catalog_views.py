"""« Catalogue d'automatisations » : the finished workflows a workspace chooses to offer. A workflow is in the catalog
only if someone allowed to publish said so, and only while it has a published version (that is what people run)."""

from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.permissions import PUBLISH, RUN, VIEW, get_workspace, has_perm

from .models import Workflow


def catalog_workflows(ws):
    return Workflow.objects.filter(workspace=ws, in_catalog=True, deprecated=False).prefetch_related("versions")


def _card(wf: Workflow, version) -> dict:
    props = (version.input_schema or {}).get("properties") or {}
    return {
        "slug": wf.slug,
        "name": wf.name,
        "description": wf.description,
        "folder": wf.folder,
        "version": version.version,
        "changelog": version.changelog,
        "published_at": version.created_at,
        "inputs": len(props),
    }


class CatalogListView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        q = request.query_params.get("q", "").strip().lower()[:100]
        rows = []
        for wf in catalog_workflows(ws):
            v = wf.latest_version()
            if v is None or (q and q not in f"{wf.name} {wf.description} {wf.folder}".lower()):
                continue
            rows.append(_card(wf, v))
        rows.sort(key=lambda r: r["name"].lower())
        return Response(
            {
                "results": rows,
                "can_run": has_perm(request.user, ws, RUN),
                "can_publish": has_perm(request.user, ws, PUBLISH),
            }
        )


class CatalogDetailView(APIView):
    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = catalog_workflows(ws).filter(slug=slug).first()
        v = wf.latest_version() if wf else None
        if v is None:
            raise Http404()
        return Response(
            {
                **_card(wf, v),
                "input_schema": v.input_schema,
                "has_form": bool((v.graph or {}).get("form")),
                "can_run": has_perm(request.user, ws, RUN),
            }
        )


class CatalogPublishView(APIView):
    """POST: offer this workflow in the catalog. DELETE: withdraw it. Runs in progress are not affected."""

    def _wf(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        wf = Workflow.objects.filter(workspace=ws, slug=slug).first()
        if wf is None:
            raise Http404()
        return ws, wf

    def post(self, request, workspace, slug):
        ws, wf = self._wf(request, workspace, slug)
        if wf.latest_version() is None:
            raise serializers.ValidationError(
                {
                    "workflow": "Publiez d'abord une version du workflow : c'est elle que l'on lancera depuis le catalogue."
                }
            )
        if wf.deprecated:
            raise serializers.ValidationError(
                {"workflow": "Un workflow déprécié ne peut pas être proposé au catalogue."}
            )
        if not wf.in_catalog:
            wf.in_catalog = True
            wf.save(update_fields=["in_catalog"])
            record("workflow.catalog_publish", actor=request.user, workspace=ws, target=wf)
        return Response({"slug": wf.slug, "in_catalog": True})

    def delete(self, request, workspace, slug):
        ws, wf = self._wf(request, workspace, slug)
        if wf.in_catalog:
            wf.in_catalog = False
            wf.save(update_fields=["in_catalog"])
            record("workflow.catalog_withdraw", actor=request.user, workspace=ws, target=wf)
        return Response({"slug": wf.slug, "in_catalog": False})
