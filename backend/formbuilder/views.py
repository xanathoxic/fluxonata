"""The form library: list, draft, publish, versions, copies and usage. Authorization is decided here, never by the UI."""

from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core.folders import clean_folder
from core.tags import clean_tags
from tenancy.permissions import EDIT, PUBLISH, VIEW, get_workspace
from triggers import formcss, formengine
from workflows import access

from . import builtin, service
from .models import Form, FormVersion, empty_definition

MAX_DEFINITION = 256 * 1024


class Conflict(APIException):
    status_code = 409
    default_code = "revision_conflict"
    default_detail = "Le brouillon a été modifié ailleurs."

    def __init__(self, detail: dict):
        super().__init__(detail)
        self.detail = detail


def _check_definition(value):
    if len(str(value)) > MAX_DEFINITION:
        raise serializers.ValidationError({"definition": "Définition trop volumineuse."})
    if isinstance(value, dict) and value.get("css") not in (None, ""):
        css, bad = formcss.normalize(value["css"])
        if bad:
            raise serializers.ValidationError({"definition": {"css": f"Ligne {bad[0]['line']} : {bad[0]['message']}"}})
        value = {**value, "css": css}  # what is stored is the canonical text the server wrote, never the author's
    elif isinstance(value, dict) and "css" in value:
        value = {k: v for k, v in value.items() if k != "css"}
    problems = formengine.check_definition(value)
    if problems:
        raise serializers.ValidationError({"definition": problems})
    return value


def _version_light(v: FormVersion | None) -> dict | None:
    return (
        {"version": v.version, "created_at": v.created_at, "changelog": v.changelog, "digest": v.digest} if v else None
    )


def card(form: Form) -> dict:
    latest = form.latest_version()
    fields = sum(len(s.get("fields", [])) for s in (form.draft_definition or {}).get("sections", []))
    return {
        "slug": form.slug,
        "name": form.name,
        "description": form.description,
        "folder": form.folder,
        "tags": form.tags,
        "is_template": form.is_template,
        "owner": form.owner.username if form.owner else None,
        "latest_version": _version_light(latest),
        "draft_revision": form.draft_revision,
        "fields": fields,
        "created_at": form.created_at,
        "updated_at": form.updated_at,
    }


def _get(request, workspace, slug, perm):
    ws = get_workspace(request.user, workspace, VIEW if perm == VIEW else perm)
    form = Form.objects.filter(workspace=ws, slug=slug).select_related("owner").first()
    if form is None:
        raise Http404()
    access.require(request.user, ws, form.folder, perm)
    return ws, form


class CreateSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=60), required=False, default=list, max_length=20
    )
    is_template = serializers.BooleanField(default=False)
    definition = serializers.JSONField(required=False)


class FormListCreateView(APIView):
    serializer_class = CreateSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = access.visible(Form.objects.filter(workspace=ws).select_related("owner"), request.user, ws)
        flag = request.query_params.get("template")
        if flag in ("0", "1"):
            rows = rows.filter(is_template=flag == "1")
        return Response({"results": [card(f) for f in rows.prefetch_related("versions")]})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        ser = CreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        folder = clean_folder(d["folder"])
        access.require(request.user, ws, folder, EDIT)
        definition = _check_definition(d["definition"]) if "definition" in d else empty_definition()
        try:
            with transaction.atomic():
                form = Form.objects.create(
                    workspace=ws,
                    slug=d["slug"],
                    name=d["name"],
                    description=d["description"],
                    folder=folder,
                    tags=clean_tags(d["tags"]),
                    owner=request.user,
                    is_template=d["is_template"],
                    draft_definition=definition,
                )
        except IntegrityError:
            raise serializers.ValidationError({"slug": "Cet identifiant existe déjà."}) from None
        record("form.create", actor=request.user, workspace=ws, target=form, template=form.is_template)
        return Response(card(form), status=201)


class PatchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160, required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    folder = serializers.CharField(required=False, allow_blank=True, max_length=200)
    tags = serializers.ListField(child=serializers.CharField(max_length=60), required=False, max_length=20)
    is_template = serializers.BooleanField(required=False)
    definition = serializers.JSONField(required=False)
    revision = serializers.IntegerField(min_value=1, required=False)


class FormDetailView(APIView):
    serializer_class = PatchSerializer

    def get(self, request, workspace, slug):
        ws, form = _get(request, workspace, slug, VIEW)
        return Response(
            {
                **card(form),
                "definition": form.draft_definition,
                "versions": [_version_light(v) for v in form.versions.all()],
                "usage": service.usage(form),
            }
        )

    def patch(self, request, workspace, slug):
        ws, form = _get(request, workspace, slug, EDIT)
        ser = PatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        with transaction.atomic():
            form = Form.objects.select_for_update().get(pk=form.pk)
            if "definition" in d:
                if "revision" not in d:
                    raise serializers.ValidationError({"revision": "Indiquez la révision du brouillon."})
                if d["revision"] != form.draft_revision:
                    raise Conflict(
                        {
                            "message": "Le brouillon a été modifié ailleurs. Rechargez pour voir la version la plus récente.",
                            "current_revision": form.draft_revision,
                        }
                    )
                form.draft_definition = _check_definition(d["definition"])
                form.draft_revision += 1
            if "folder" in d:
                folder = clean_folder(d["folder"])
                access.require(request.user, ws, folder, EDIT)
                form.folder = folder
            for key in ("name", "description", "is_template"):
                if key in d:
                    setattr(form, key, d[key])
            if "tags" in d:
                form.tags = clean_tags(d["tags"])
            form.save()
        record("form.update", actor=request.user, workspace=ws, target=form, fields=sorted(d))
        return Response({**card(form), "definition": form.draft_definition})

    def delete(self, request, workspace, slug):
        ws, form = _get(request, workspace, slug, EDIT)
        used = service.usage(form)
        if used["workflows"] or used["public_forms"]:
            raise serializers.ValidationError(
                {"usage": "Ce formulaire est rattaché à un workflow ou à un formulaire public : détachez-le d'abord."}
            )
        record("form.delete", actor=request.user, workspace=ws, target=form)
        form.delete()
        return Response(status=204)


class PublishSerializer(serializers.Serializer):
    changelog = serializers.CharField(required=False, allow_blank=True, max_length=300, default="")
    bump = serializers.ChoiceField(choices=["major", "minor", "patch"], default="minor")


class FormPublishView(APIView):
    serializer_class = PublishSerializer

    def post(self, request, workspace, slug):
        ws, form = _get(request, workspace, slug, PUBLISH)
        if form.is_template:
            raise serializers.ValidationError({"is_template": "Un modèle ne se publie pas : copiez-le en formulaire."})
        ser = PublishSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        version = service.publish(
            form, request.user, changelog=ser.validated_data["changelog"], bump=ser.validated_data["bump"]
        )
        return Response({"version": version.version, "digest": version.digest}, status=201)


class FormVersionView(APIView):
    def get(self, request, workspace, slug, version):
        ws, form = _get(request, workspace, slug, VIEW)
        v = form.versions.filter(version=version).first()
        if v is None:
            raise Http404()
        return Response({**_version_light(v), "definition": v.definition})


class DuplicateSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    folder = serializers.CharField(required=False, allow_blank=True, max_length=200)
    as_template = serializers.BooleanField(default=False)
    from_version = serializers.CharField(required=False, allow_blank=True, max_length=20)


class FormDuplicateView(APIView):
    """A copy of the draft (or of one published version): becomes a form, or a template, of its own."""

    serializer_class = DuplicateSerializer

    def post(self, request, workspace, slug):
        ws, form = _get(request, workspace, slug, VIEW)
        ser = DuplicateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        folder = clean_folder(d["folder"]) if "folder" in d else form.folder
        ws = get_workspace(request.user, workspace, EDIT)
        access.require(request.user, ws, folder, EDIT)
        source = form.draft_definition
        if d.get("from_version"):
            v = form.versions.filter(version=d["from_version"]).first()
            if v is None:
                raise Http404()
            source = v.definition
        try:
            with transaction.atomic():
                copy = Form.objects.create(
                    workspace=ws,
                    slug=d["slug"],
                    name=d["name"],
                    description=form.description,
                    folder=folder,
                    tags=form.tags,
                    owner=request.user,
                    is_template=d["as_template"],
                    draft_definition=source,
                )
        except IntegrityError:
            raise serializers.ValidationError({"slug": "Cet identifiant existe déjà."}) from None
        record(
            "form.duplicate", actor=request.user, workspace=ws, target=copy, source=form.slug, template=copy.is_template
        )
        return Response(card(copy), status=201)


class BuiltinTemplatesView(APIView):
    def get(self, request, workspace):
        get_workspace(request.user, workspace, VIEW)
        return Response({"results": builtin.TEMPLATES})


class CheckSerializer(serializers.Serializer):
    css = serializers.CharField(required=False, allow_blank=True, max_length=60_000)
    definition = serializers.JSONField(required=False)


class FormCheckView(APIView):
    """Live feedback while designing: the canonical form of a custom CSS (or its problems, line by line) and the problems of a
    whole definition. Nothing is stored."""

    serializer_class = CheckSerializer

    def post(self, request, workspace):
        get_workspace(request.user, workspace, EDIT)
        ser = CheckSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        out: dict = {}
        if "css" in d:
            css, problems = formcss.normalize(d["css"])
            out["css"] = {"css": css, "problems": problems}
        if "definition" in d:
            value = d["definition"]
            if isinstance(value, dict) and value.get("css"):
                css, bad = formcss.normalize(value["css"])
                value = {**value, "css": css} if not bad else {k: v for k, v in value.items() if k != "css"}
            out["definition"] = (
                formengine.check_definition(value)
                if len(str(value)) <= MAX_DEFINITION
                else {"_": "Définition trop volumineuse."}
            )
        return Response(out)
