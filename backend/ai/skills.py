"""AI skills library: reusable, versioned AI capabilities that a workflow's "AI step" can select.

A skill never calls a model by itself: it is configuration (instructions, typed inputs, expected output). The call
happens when a workflow runs an AI step, through the governed AI service (policy, provider, budget, untrusted output)."""

import re

import jsonschema
from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from catalog.library import SECRET_LIKE, SEMVER, bump
from core.folders import clean_folder
from core.tags import clean_tags
from tenancy.permissions import PUBLISH, VIEW, get_workspace
from workflows import access
from workflows.expressions import ExpressionError, parse, template_expressions
from workflows.models import WorkflowVersion

from .models import AiProvider, AiSkill, AiSkillVersion

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TYPES = {"string", "integer", "number", "boolean"}
MAX_INSTRUCTIONS = 20_000
MAX_FIELDS = 20


def check_fields_schema(schema, *, what: str, min_fields: int) -> dict:
    """An object schema of flat, typed fields (what a form or a model answer can carry)."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise serializers.ValidationError(f"{what} : un objet est attendu.")
    props = schema.get("properties") or {}
    if not isinstance(props, dict) or len(props) < min_fields or len(props) > MAX_FIELDS:
        raise serializers.ValidationError(f"{what} : entre {min_fields} et {MAX_FIELDS} champ(s).")
    for name, spec in props.items():
        if not NAME_RE.match(name):
            raise serializers.ValidationError(f"{what} : nom de champ invalide « {name} » (minuscules, chiffres, _).")
        if not isinstance(spec, dict) or spec.get("type") not in TYPES:
            raise serializers.ValidationError(f"{what} : le champ « {name} » doit avoir un type parmi {sorted(TYPES)}.")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(r not in props for r in required):
        raise serializers.ValidationError(f"{what} : champ obligatoire inconnu.")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise serializers.ValidationError(f"{what} : schéma invalide ({exc.message[:120]}).") from None
    return schema


def check_instructions(text: str, declared: set[str]) -> str:
    if not text.strip():
        raise serializers.ValidationError("Les instructions sont obligatoires.")
    if SECRET_LIKE.search(text):
        raise serializers.ValidationError(
            "Les instructions semblent contenir un secret : ne les placez pas dans un skill."
        )
    for src in template_expressions(text):
        try:
            tree = parse(src)
        except ExpressionError:
            raise serializers.ValidationError(f"Expression invalide dans les instructions : {{{{ {src} }}}}.") from None
        path = tree[1] if tree[0] == "path" else None
        if not path or path[0] != "inputs" or len(path) != 2 or path[1] not in declared:
            raise serializers.ValidationError(
                f"Variable inconnue dans les instructions : {{{{ {src} }}}}. Utilisez {{{{ inputs.<nom> }}}} avec une entrée déclarée."
            )
    return text


class VersionInput(serializers.Serializer):
    version = serializers.CharField(required=False)
    bump = serializers.ChoiceField(choices=["patch", "minor", "major"], default="minor")
    instructions = serializers.CharField(max_length=MAX_INSTRUCTIONS, trim_whitespace=False)
    input_schema = serializers.JSONField(default=lambda: {"type": "object", "properties": {}})
    output_schema = serializers.JSONField()
    default_provider = serializers.SlugField(required=False, allow_blank=True, default="")
    changelog = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")

    def validate_version(self, v):
        if not SEMVER.match(v):
            raise serializers.ValidationError("Version sémantique attendue (ex. 1.2.0).")
        return v

    def validate(self, attrs):
        try:
            inputs = check_fields_schema(attrs["input_schema"], what="Entrées", min_fields=0)
            check_fields_schema(attrs["output_schema"], what="Sortie", min_fields=1)
            check_instructions(attrs["instructions"], set(inputs.get("properties", {})))
        except serializers.ValidationError as exc:
            msg = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
            field = (
                "instructions"
                if "nstruction" in str(msg) or "Variable" in str(msg)
                else ("output_schema" if str(msg).startswith("Sortie") else "input_schema")
            )
            raise serializers.ValidationError({field: str(msg)}) from None
        return attrs


class VersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiSkillVersion
        fields = [
            "version",
            "instructions",
            "input_schema",
            "output_schema",
            "default_provider",
            "changelog",
            "digest",
            "created_at",
        ]


class SkillSerializer(serializers.ModelSerializer):
    owner = serializers.SlugRelatedField(slug_field="username", read_only=True)
    latest_version = serializers.SerializerMethodField()

    class Meta:
        model = AiSkill
        fields = [
            "slug",
            "name",
            "description",
            "folder",
            "tags",
            "owner",
            "status",
            "deprecation_reason",
            "latest_version",
        ]

    def get_latest_version(self, obj) -> dict | None:
        v = obj.latest_version()
        return VersionSerializer(v).data if v else None


class SkillCreateSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=60), required=False, default=list, max_length=20
    )
    version = VersionInput()


def _skill(ws, slug) -> AiSkill:
    s = AiSkill.objects.filter(workspace=ws, slug=slug).first()
    if s is None:
        raise Http404()
    return s


def _check_provider(ws, name: str):
    if name and not AiProvider.objects.filter(workspace=ws, name=name).exists():
        raise serializers.ValidationError({"default_provider": "Ce fournisseur d'IA n'existe pas dans l'espace."})


def _create_version(skill: AiSkill, data: dict, user) -> AiSkillVersion:
    latest = skill.latest_version()
    version = data.get("version") or bump(latest.version if latest else None, data.get("bump", "minor"))
    if skill.versions.filter(version=version).exists():
        raise serializers.ValidationError({"version": "Cette version existe déjà."})
    if latest and tuple(map(int, version.split("."))) <= tuple(map(int, latest.version.split("."))):
        raise serializers.ValidationError({"version": f"La version doit être supérieure à {latest.version}."})
    _check_provider(skill.workspace, data.get("default_provider", ""))
    return AiSkillVersion.objects.create(
        skill=skill,
        version=version,
        instructions=data["instructions"],
        input_schema=data["input_schema"],
        output_schema=data["output_schema"],
        default_provider=data.get("default_provider", ""),
        changelog=data.get("changelog", ""),
        created_by=user,
    )


class SkillsView(APIView):
    serializer_class = SkillCreateSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = AiSkill.objects.filter(workspace=ws).prefetch_related("versions").select_related("owner")
        return Response({"results": SkillSerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = SkillCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if AiSkill.objects.filter(workspace=ws, slug=d["slug"]).exists():
            raise serializers.ValidationError({"slug": "Cet identifiant existe déjà dans l'espace."})
        skill = AiSkill.objects.create(
            workspace=ws,
            owner=request.user,
            slug=d["slug"],
            name=d["name"],
            description=d["description"],
            folder=clean_folder(d["folder"]),
            tags=clean_tags(d["tags"]),
        )
        v = _create_version(skill, {**d["version"], "version": d["version"].get("version", "1.0.0")}, request.user)
        record("ai_skill.create", actor=request.user, workspace=ws, target=skill, version=v.version)
        return Response(SkillSerializer(skill).data, status=201)


class SkillDetailView(APIView):
    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        s = _skill(ws, slug)
        versions = sorted(s.versions.all(), key=lambda v: tuple(map(int, v.version.split("."))), reverse=True)
        return Response({**SkillSerializer(s).data, "versions": VersionSerializer(versions, many=True).data})

    def patch(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        s = _skill(ws, slug)
        d = request.data
        if "name" in d:
            name = str(d["name"]).strip()
            if not name or len(name) > 160:
                raise serializers.ValidationError({"name": "Nom requis (160 caractères maximum)."})
            s.name = name
        if "description" in d:
            s.description = str(d["description"])[:2000]
        if "folder" in d:
            s.folder = clean_folder(d["folder"])
        if "tags" in d:
            s.tags = clean_tags(d["tags"])
        s.save()
        record(
            "ai_skill.update",
            actor=request.user,
            workspace=ws,
            target=s,
            fields=sorted(set(d) & {"name", "description", "folder", "tags"}),
        )
        return Response(SkillSerializer(s).data)


class SkillVersionsView(APIView):
    serializer_class = VersionInput

    @transaction.atomic
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        s = AiSkill.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if s is None:
            raise Http404()
        ser = VersionInput(data=request.data)
        ser.is_valid(raise_exception=True)
        v = _create_version(s, ser.validated_data, request.user)
        record("ai_skill.publish", actor=request.user, workspace=ws, target=s, version=v.version, digest=v.digest)
        return Response(VersionSerializer(v).data, status=201)


class SkillDeprecateView(APIView):
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        s = _skill(ws, slug)
        reinstate = bool(request.data.get("reinstate"))
        s.status = AiSkill.Status.ACTIVE if reinstate else AiSkill.Status.DEPRECATED
        s.deprecation_reason = "" if reinstate else str(request.data.get("reason", ""))[:300]
        s.save()
        record("ai_skill.reinstate" if reinstate else "ai_skill.deprecate", actor=request.user, workspace=ws, target=s)
        return Response(SkillSerializer(s).data)


class SkillDependentsView(APIView):
    """Which published workflow versions use this skill (and which exact version)."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        _skill(ws, slug)
        out = []
        visible = access.visible(
            WorkflowVersion.objects.filter(workflow__workspace=ws), request.user, ws, "workflow__folder"
        )
        for wv in visible.select_related("workflow"):
            used = sorted(
                {n.get("skill_version", "") for n in wv.graph["nodes"] if n["type"] == "ai" and n.get("skill") == slug}
            )
            if used:
                out.append({"workflow": wv.workflow.slug, "workflow_version": wv.version, "skill_versions": used})
        return Response({"results": out})
