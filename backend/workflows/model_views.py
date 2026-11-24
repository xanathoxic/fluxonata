"""API des modèles de document (ADR 0036) : images de l'espace et aperçu Markdown de l'éditeur. Droits contrôlés côté serveur."""

import uuid

from django.db import transaction
from django.db.models.functions import Length
from django.http import Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.branding import BrandingError
from tenancy.permissions import EDIT, VIEW, get_workspace

from . import model_images, model_md
from .models import DocumentImage, Workflow, WorkflowVersion


def _url(ws, image_id) -> str:
    return f"/api/v1/workspaces/{ws.slug}/model-images/{image_id}/"


def _row(ws, img) -> dict:
    return {
        "id": str(img.pk),
        "name": img.name,
        "content_type": img.content_type,
        "width": img.width,
        "height": img.height,
        "bytes": getattr(img, "size", None),
        "url": _url(ws, img.pk),
        "reference": f"image:{img.pk}",
    }


def _safe_name(raw: str, fallback: str) -> str:
    name = " ".join(str(raw or "").replace("\\", "/").split("/")[-1].split())[:120]
    return "".join(c for c in name if ord(c) >= 32) or fallback


class ModelImagesView(APIView):
    parser_classes = [MultiPartParser]

    @extend_schema(operation_id="model_images_list", responses=OpenApiTypes.OBJECT)
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = DocumentImage.objects.filter(workspace=ws).defer("data").annotate(size=Length("data"))
        return Response([_row(ws, i) for i in rows])

    @extend_schema(
        operation_id="model_images_upload",
        request={
            "multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}
        },
        responses=OpenApiTypes.OBJECT,
    )
    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        upload = request.FILES.get("file")
        if upload is None:
            raise serializers.ValidationError({"file": "Choisissez une image."})
        if upload.size > model_images.MAX_BYTES:
            raise serializers.ValidationError({"file": f"L'image dépasse {model_images.MAX_BYTES // 1024} Ko."})
        try:
            img = model_images.inspect(upload.read(model_images.MAX_BYTES + 1))
        except BrandingError as exc:
            raise serializers.ValidationError({"file": str(exc)}) from None
        existing = DocumentImage.objects.filter(workspace=ws, sha256=img.sha256).first()
        if existing is not None:  # la même image n'est gardée qu'une fois
            return Response(_row(ws, existing))
        if DocumentImage.objects.filter(workspace=ws).count() >= model_images.MAX_PER_WORKSPACE:
            raise serializers.ValidationError(
                {"file": f"{model_images.MAX_PER_WORKSPACE} images au plus par espace : supprimez-en."}
            )
        row = DocumentImage.objects.create(
            workspace=ws,
            name=_safe_name(request.data.get("name") or upload.name, "image"),
            content_type=img.content_type,
            width=img.width,
            height=img.height,
            sha256=img.sha256,
            data=img.data,
            created_by=request.user,
        )
        record(
            "model.image_add",
            actor=request.user,
            workspace=ws,
            target=row,
            content_type=img.content_type,
            bytes=len(img.data),
        )
        return Response(_row(ws, row), status=201)


class ModelImageView(APIView):
    @extend_schema(operation_id="model_image_retrieve", responses={200: OpenApiTypes.BINARY})
    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        img = DocumentImage.objects.filter(workspace=ws, pk=pk).first()
        if img is None:
            raise Http404()
        res = HttpResponse(bytes(img.data), content_type=img.content_type)
        res["X-Content-Type-Options"] = "nosniff"
        res["Content-Security-Policy"] = "default-src 'none'; sandbox"
        res["Cache-Control"] = "private, max-age=3600"
        res["ETag"] = f'"{img.sha256[:16]}"'
        return res

    @extend_schema(operation_id="model_image_delete", responses={204: None})
    @transaction.atomic
    def delete(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, EDIT)
        img = DocumentImage.objects.filter(workspace=ws, pk=pk).first()
        if img is None:
            raise Http404()
        needle = str(img.pk)
        used = (
            Workflow.objects.filter(workspace=ws, draft_graph__icontains=needle).values_list("name", flat=True).first()
            or WorkflowVersion.objects.filter(workflow__workspace=ws, graph__icontains=needle)
            .values_list("workflow__name", flat=True)
            .first()
        )
        if used:
            return Response(
                {
                    "error": {
                        "code": "image_in_use",
                        "message": f"Cette image est utilisée par le workflow « {used} » (brouillon ou version publiée).",
                        "details": None,
                    }
                },
                status=409,
            )
        img.delete()
        record("model.image_remove", actor=request.user, workspace=ws, target=ws, image=needle)
        return Response(status=204)


class ModelPreviewView(APIView):
    """Aperçu HTML de l'éditeur : le même moteur que le fichier et l'e-mail ; les `{{ expressions }}` restent des pastilles."""

    @extend_schema(operation_id="model_preview", request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        source = request.data.get("markdown", "")
        if not isinstance(source, str):
            raise serializers.ValidationError({"markdown": "Du texte est attendu."})
        if len(source) > model_md.MAX_MARKDOWN:
            raise serializers.ValidationError(
                {"markdown": f"Le modèle dépasse {model_md.MAX_MARKDOWN // 1000} 000 caractères."}
            )
        ids = model_md.image_ids(source)
        known = {
            str(i)
            for i in DocumentImage.objects.filter(workspace=ws, pk__in=[uuid.UUID(i) for i in ids]).values_list(
                "pk", flat=True
            )
        }
        html = model_md.preview_html(source, lambda i: _url(ws, i) if i in known else None)
        return Response({"html": html, "images": ids, "missing_images": [i for i in ids if i not in known]})
