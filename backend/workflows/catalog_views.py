"""« Catalogue d'automatisations » : the finished workflows a workspace chooses to offer. A workflow is in the catalog
only if someone allowed to publish said so, and only while it has a published version (that is what people run)."""

from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.permissions import CATALOG, PUBLISH, RUN, VIEW, get_workspace, has_perm

from . import access
from .models import Workflow


def catalog_workspace(user, slug):
    """The workspace for someone who may use the catalog: any member with `view`, or the « Catalogue » role (which has nothing else)."""
    try:
        return get_workspace(user, slug, VIEW)
    except PermissionDenied:
        return get_workspace(user, slug, CATALOG)


def catalog_only(user, ws) -> bool:
    """True for the « Catalogue » role: it sees and launches only what its groups are granted, and never leaves the catalog."""
    return not has_perm(user, ws, VIEW)


def catalog_workflows(ws, user):
    qs = Workflow.objects.filter(workspace=ws, in_catalog=True, deprecated=False)
    if catalog_only(user, ws):
        return (
            qs.filter(catalog_grants__group__members=user, catalog_grants__group__workspace=ws)
            .distinct()
            .prefetch_related("versions")
        )
    return access.visible(qs, user, ws).prefetch_related("versions")


def can_launch(user, ws) -> bool:
    return catalog_only(user, ws) or has_perm(user, ws, RUN)


def _card(wf: Workflow, version) -> dict:
    props = (version.input_schema or {}).get("properties") or {}
    return {
        "slug": wf.slug,
        "name": wf.name,
        "description": wf.description,
        "folder": wf.folder,
        "tags": wf.tags,
        "version": version.version,
        "changelog": version.changelog,
        "published_at": version.created_at,
        "inputs": len(props),
    }


class CatalogListView(APIView):
    def get(self, request, workspace):
        ws = catalog_workspace(request.user, workspace)
        q = request.query_params.get("q", "").strip().lower()[:100]
        rows = []
        for wf in catalog_workflows(ws, request.user):
            v = wf.latest_version()
            if v is None or (q and q not in f"{wf.name} {wf.description} {wf.folder} {' '.join(wf.tags)}".lower()):
                continue
            rows.append(_card(wf, v))
        rows.sort(key=lambda r: r["name"].lower())
        return Response(
            {
                "results": rows,
                "can_run": can_launch(request.user, ws),
                "can_publish": has_perm(request.user, ws, PUBLISH),
            }
        )


class CatalogDetailView(APIView):
    def get(self, request, workspace, slug):
        ws = catalog_workspace(request.user, workspace)
        wf = catalog_workflows(ws, request.user).filter(slug=slug).first()
        v = wf.latest_version() if wf else None
        if v is None:
            raise Http404()
        return Response(
            {
                **_card(wf, v),
                "input_schema": v.input_schema,
                "has_form": bool((v.graph or {}).get("form")),
                "form": (v.graph or {}).get("form"),
                "input_form": (v.graph or {}).get("input_form"),
                "can_run": can_launch(request.user, ws),
            }
        )


class CatalogPublishView(APIView):
    """POST: offer this workflow in the catalog. DELETE: withdraw it. Runs in progress are not affected."""

    def _wf(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        return ws, access.workflow_or_404(request.user, ws, slug, PUBLISH)

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
