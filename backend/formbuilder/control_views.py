"""The library of custom controls: list, draft, publish, versions. Authorization is decided here, never by the UI."""

import copy

from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core.folders import clean_folder
from core.tags import clean_tags
from tenancy.permissions import EDIT, PUBLISH, VIEW, get_workspace
from triggers import formcontrols
from workflows import access

from . import service
from .models import Control, ControlVersion, Form, empty_spec
from .views import Conflict

MAX_SPEC = 256 * 1024
BUMPS = ("major", "minor", "patch")


def _check_spec(value, *, draft: bool = True):
    if len(str(value)) > MAX_SPEC:
        raise serializers.ValidationError({"spec": "Description trop volumineuse."})
    problems = formcontrols.check_spec(value, draft=draft)
    if problems:
        raise serializers.ValidationError({"spec": problems})
    return value


def _light(v: ControlVersion | None) -> dict | None:
    return (
        {"version": v.version, "created_at": v.created_at, "changelog": v.changelog, "digest": v.digest} if v else None
    )


def card(c: Control, *, spec: bool = False) -> dict:
    latest = c.latest_version()
    draft = c.draft_spec or {}
    out = {
        "slug": c.slug,
        "name": c.name,
        "description": c.description,
        "icon": c.icon,
        "folder": c.folder,
        "tags": c.tags,
        "owner": c.owner.username if c.owner else None,
        "mode": draft.get("mode"),
        "parts": len(draft.get("parts", [])),
        "latest_version": _light(latest),
        "draft_revision": c.draft_revision,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }
    if spec and latest:  # what the form editor offers: the last published version, whole
        out["published"] = {"version": latest.version, "name": latest.name, "icon": latest.icon, "spec": latest.spec}
    return out


def _get(request, workspace, slug, perm):
    ws = get_workspace(request.user, workspace, perm)
    control = Control.objects.filter(workspace=ws, slug=slug).select_related("owner").first()
    if control is None:
        raise Http404()
    access.require(request.user, ws, control.folder, perm)
    return ws, control


def usage_counts(workspace) -> dict[str, int]:
    """How many forms of the library use each control (by its reference), in ONE pass over the forms."""
    counts: dict[str, int] = {}
    for definition in Form.objects.filter(workspace=workspace).values_list("draft_definition", flat=True):
        refs = {
            (f.get("control") or {}).get("ref")
            for section in (definition or {}).get("sections", [])
            for f in section.get("fields", [])
            if isinstance(f, dict) and f.get("type") == "custom"
        }
        for ref in refs - {None}:
            counts[ref] = counts.get(ref, 0) + 1
    return counts


def usage(c: Control) -> list[str]:
    """Forms of the library whose draft uses this control."""
    used = []
    for form in Form.objects.filter(workspace=c.workspace):
        for section in (form.draft_definition or {}).get("sections", []):
            if any(
                isinstance(f, dict) and f.get("type") == "custom" and (f.get("control") or {}).get("ref") == c.slug
                for f in section.get("fields", [])
            ):
                used.append(form.slug)
                break
    return used


class CreateSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    icon = serializers.RegexField(r"^[A-Za-z0-9_]{1,40}$", required=False, default="custom")
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=60), required=False, default=list, max_length=20
    )
    spec = serializers.JSONField(required=False)


class ControlListCreateView(APIView):
    serializer_class = CreateSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = Control.objects.filter(workspace=ws).select_related("owner").prefetch_related("versions")
        published = request.query_params.get("published") == "1"
        counts = usage_counts(ws)
        out = [{**card(c, spec=published), "used_by": counts.get(c.slug, 0)} for c in rows]
        return Response({"results": [c for c in out if c["latest_version"]] if published else out})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        ser = CreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        spec = _check_spec(d["spec"]) if "spec" in d else empty_spec()
        folder = clean_folder(d["folder"])
        access.require(request.user, ws, folder, EDIT)
        try:
            with transaction.atomic():
                control = Control.objects.create(
                    workspace=ws,
                    slug=d["slug"],
                    name=d["name"],
                    description=d["description"],
                    icon=d["icon"],
                    folder=folder,
                    tags=clean_tags(d["tags"]),
                    owner=request.user,
                    draft_spec=spec,
                )
        except IntegrityError:
            raise serializers.ValidationError({"slug": "Cet identifiant existe déjà."}) from None
        record("control.create", actor=request.user, workspace=ws, target=control)
        return Response(card(control), status=201)


class PatchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160, required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    icon = serializers.RegexField(r"^[A-Za-z0-9_]{1,40}$", required=False)
    folder = serializers.CharField(required=False, allow_blank=True, max_length=200)
    tags = serializers.ListField(child=serializers.CharField(max_length=60), required=False, max_length=20)
    spec = serializers.JSONField(required=False)
    revision = serializers.IntegerField(min_value=1, required=False)


class ControlDetailView(APIView):
    serializer_class = PatchSerializer

    def get(self, request, workspace, slug):
        ws, control = _get(request, workspace, slug, VIEW)
        return Response(
            {
                **card(control, spec=True),
                "spec": control.draft_spec,
                "versions": [_light(v) for v in control.versions.all()],
                "usage": usage(control),
            }
        )

    def patch(self, request, workspace, slug):
        ws, control = _get(request, workspace, slug, EDIT)
        ser = PatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        with transaction.atomic():
            control = Control.objects.select_for_update().get(pk=control.pk)
            if "spec" in d:
                if "revision" not in d:
                    raise serializers.ValidationError({"revision": "Indiquez la révision du brouillon."})
                if d["revision"] != control.draft_revision:
                    raise Conflict(
                        {
                            "message": "Le brouillon a été modifié ailleurs. Rechargez pour voir la version la plus récente.",
                            "current_revision": control.draft_revision,
                        }
                    )
                spec = _check_spec(d["spec"])
                kept = (control.draft_spec or {}).get("mode")
                if kept and spec.get("mode") != kept:
                    raise serializers.ValidationError(
                        {"spec": "Le type d'un contrôle ne peut pas être changé : créez un nouveau contrôle."}
                    )
                control.draft_spec = spec
                control.draft_revision += 1
            if "folder" in d:
                folder = clean_folder(d["folder"])
                access.require(request.user, ws, folder, EDIT)
                control.folder = folder
            for key in ("name", "description", "icon"):
                if key in d:
                    setattr(control, key, d[key])
            if "tags" in d:
                control.tags = clean_tags(d["tags"])
            control.save()
        record("control.update", actor=request.user, workspace=ws, target=control, fields=sorted(d))
        return Response({**card(control), "spec": control.draft_spec})

    def delete(self, request, workspace, slug):
        ws, control = _get(request, workspace, slug, EDIT)
        record("control.delete", actor=request.user, workspace=ws, target=control)
        control.delete()  # forms keep their own copy of the version they use
        return Response(status=204)


class PublishSerializer(serializers.Serializer):
    changelog = serializers.CharField(required=False, allow_blank=True, max_length=300, default="")
    bump = serializers.ChoiceField(choices=BUMPS, default="minor")


class ControlPublishView(APIView):
    serializer_class = PublishSerializer

    def post(self, request, workspace, slug):
        ws, control = _get(request, workspace, slug, PUBLISH)
        ser = PublishSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            control = Control.objects.select_for_update().get(pk=control.pk)
            _check_spec(control.draft_spec, draft=False)  # a draft may be empty, a version may not
            latest = control.latest_version()
            version = ControlVersion.objects.create(
                control=control,
                version=service.next_bump(latest.version if latest else None, ser.validated_data["bump"]),
                name=control.name,
                icon=control.icon,
                spec=copy.deepcopy(control.draft_spec),
                digest=service.digest(control.draft_spec),
                changelog=ser.validated_data["changelog"],
                created_by=request.user,
            )
        record("control.publish", actor=request.user, workspace=ws, target=control, version=version.version)
        return Response({"version": version.version, "digest": version.digest}, status=201)


class ControlVersionView(APIView):
    def get(self, request, workspace, slug, version):
        ws, control = _get(request, workspace, slug, VIEW)
        v = control.versions.filter(version=version).first()
        if v is None:
            raise Http404()
        return Response({**_light(v), "name": v.name, "icon": v.icon, "spec": v.spec})


class CheckSerializer(serializers.Serializer):
    spec = serializers.JSONField()


class ControlCheckView(APIView):
    """Live feedback while designing: the problems of a spec, nothing stored."""

    serializer_class = CheckSerializer

    def post(self, request, workspace):
        get_workspace(request.user, workspace, EDIT)
        ser = CheckSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        spec = ser.validated_data["spec"]
        return Response(
            {"problems": formcontrols.check_spec(spec) if len(str(spec)) <= MAX_SPEC else {"spec": "Trop volumineux."}}
        )


class SourcePreviewSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["options", "prefill"])
    spec = serializers.JSONField()
    config = serializers.JSONField()  # the reading of the service, as a FORM field holds it (the control carries none)
    sample = serializers.JSONField()
    params = serializers.DictField(
        child=serializers.CharField(max_length=200), required=False, default=dict
    )  # values of the `{champ}` placeholders


class ControlSourcePreviewView(APIView):
    """Tries a form field's reading of a web service on a sample reply pasted by the designer: which options it would give, or what
    value it would pre-fill into the control (and why not). Pure computation, no call is made."""

    serializer_class = SourcePreviewSerializer

    def post(self, request, workspace):
        from runs import http_node
        from triggers import lookups, shaping

        get_workspace(request.user, workspace, EDIT)
        ser = SourcePreviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        spec, cfg, sample, kind = (ser.validated_data[k] for k in ("spec", "config", "sample", "kind"))
        if max(len(str(spec)), len(str(sample))) > MAX_SPEC:
            raise serializers.ValidationError({"sample": "Trop volumineux."})
        problems = formcontrols.check_spec(spec, draft=True)
        if not problems:
            field = {"key": "essai", "type": "custom", "control": {"ref": "essai", "version": "0.0.0", "spec": spec}}
            keys = formcontrols.SOURCE_KEYS if kind == "options" else formcontrols.PREFILL_KEYS
            if kind == "options" and not spec.get("dynamic"):
                problems = {"spec": "Ce contrôle ne reçoit pas ses options d'un service."}
            else:
                problems = formcontrols._check_service(cfg, keys, "config")
        if problems:
            return Response({"problems": problems})
        try:
            if kind == "options":
                params = lookups.clean_params(ser.validated_data["params"], cfg.get("params", []))
                try:
                    shaping.items(sample, cfg, params)
                except ValueError:
                    entries = cfg.get("items_as") == "entries"
                    return Response(
                        {
                            "problem": "Le chemin des éléments ne mène pas à "
                            + ("un objet" if entries else "une liste")
                            + " dans cette réponse"
                            + (
                                " (renseignez une valeur d'essai pour chaque {champ} du chemin)."
                                if "{" in cfg.get("items_path", "")
                                else "."
                            )
                        }
                    )
                return Response(
                    {"options": lookups.to_options(sample, cfg, keys=lookups.detail_keys(field), params=params)}
                )
            value = http_node._dig(sample, cfg["value_path"]) if cfg.get("value_path") else sample
            if value is None:
                return Response({"problem": "Le chemin de la valeur ne mène à rien dans cette réponse."})
            if spec.get("dynamic"):  # options from a service are taken as they come: only their shape is checked
                field["source"] = {"connection": "essai", "path": "/"}
            kept, problem = formcontrols.process(field, value)
            return Response({"value": kept, "problem": problem})
        except (KeyError, ValueError):
            return Response({"problem": "Cette réponse ne peut pas être lue avec ces réglages."})
