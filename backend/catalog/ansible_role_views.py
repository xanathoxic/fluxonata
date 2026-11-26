"""API des rôles Ansible (ADR 0037, lot 6). Lecture : droit de voir l'espace ; écriture : droit de publier (un rôle est du code exécuté sur des machines, comme une
action). Les versions sont immuables ; un rôle que des actions utilisent ne peut pas être supprimé. Chaque écriture est inscrite à l'audit."""

from django.db import transaction
from django.http import Http404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core.folders import clean_folder
from tenancy.permissions import PUBLISH, VIEW, get_workspace

from . import ansible_roles as lib
from .models import AnsibleRole, AnsibleRoleVersion

MAX_ROLES = 200


class RoleCreate(serializers.Serializer):
    name = serializers.RegexField(lib.ROLE_NAME, max_length=50)
    description = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    version = serializers.RegexField(lib.SEMVER, required=False, default="1.0.0")
    changelog = serializers.CharField(required=False, allow_blank=True, default="", max_length=300)
    files = serializers.JSONField()
    contract = serializers.JSONField(required=False, default=dict)


class RoleVersionCreate(serializers.Serializer):
    version = serializers.RegexField(lib.SEMVER)
    changelog = serializers.CharField(required=False, allow_blank=True, default="", max_length=300)
    files = serializers.JSONField()
    contract = serializers.JSONField(required=False, default=dict)


class RoleMeta(serializers.Serializer):
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    folder = serializers.CharField(required=False, allow_blank=True, max_length=200)


class RoleVersionSummary(serializers.Serializer):
    version = serializers.CharField()
    digest = serializers.CharField()
    changelog = serializers.CharField()
    files = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class RoleSummary(serializers.Serializer):
    name = serializers.CharField()
    summary = serializers.CharField()
    contract_ok = serializers.BooleanField()
    variables = serializers.IntegerField()
    description = serializers.CharField()
    folder = serializers.CharField()
    latest_version = serializers.CharField(allow_null=True)
    versions = RoleVersionSummary(many=True)
    used_by = serializers.IntegerField()


def _role(ws, name) -> AnsibleRole:
    role = AnsibleRole.objects.filter(workspace=ws, name=name).first()
    if role is None:
        raise Http404()
    return role


def _version_row(v: AnsibleRoleVersion) -> dict:
    return {
        "version": v.version,
        "digest": v.digest,
        "changelog": v.changelog,
        "files": len(v.files),
        "created_at": v.created_at,
    }


def _summary(role: AnsibleRole) -> dict:
    versions = sorted(role.versions.all(), key=lambda v: lib.semver_key(v.version), reverse=True)
    latest_contract = versions[0].contract if versions else {}
    return {
        "name": role.name,
        "summary": (latest_contract.get("summary") or ""),
        "contract_ok": not lib.contract_problems(latest_contract),
        "variables": len(latest_contract.get("variables", [])),
        "description": role.description,
        "folder": role.folder,
        "latest_version": versions[0].version if versions else None,
        "versions": [_version_row(v) for v in versions],
        "used_by": lib.roles_in_use(role),
    }


def _add_version(
    role: AnsibleRole, user, version: str, files: object, changelog: str, contract: object = None
) -> AnsibleRoleVersion:
    clean = lib.check_files(files)
    contract_clean = lib.check_contract(contract)
    known = [lib.semver_key(v) for v in role.versions.values_list("version", flat=True)]
    if known and lib.semver_key(version) <= max(known):
        raise serializers.ValidationError(
            {"version": "La nouvelle version doit être supérieure aux versions existantes."}
        )
    return AnsibleRoleVersion.objects.create(
        role=role, version=version, files=clean, changelog=changelog, contract=contract_clean, created_by=user
    )


class RoleListView(APIView):
    serializer_class = RoleCreate

    @extend_schema(operation_id="workspaces_ansible_roles_list", responses={200: RoleSummary(many=True)})
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        return Response([_summary(r) for r in AnsibleRole.objects.filter(workspace=ws).prefetch_related("versions")])

    @extend_schema(responses={201: RoleSummary})
    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = RoleCreate(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if AnsibleRole.objects.filter(workspace=ws, name=d["name"]).exists():
            raise serializers.ValidationError({"name": "Ce nom existe déjà dans l'espace."})
        if AnsibleRole.objects.filter(workspace=ws).count() >= MAX_ROLES:
            raise serializers.ValidationError({"name": f"{MAX_ROLES} rôles au plus par espace."})
        role = AnsibleRole.objects.create(
            workspace=ws,
            name=d["name"],
            description=d["description"],
            folder=clean_folder(d["folder"]),
            owner=request.user,
        )
        v = _add_version(role, request.user, d["version"], d["files"], d["changelog"], d["contract"])
        record(
            "ansible_role.create", actor=request.user, workspace=ws, target=role, version=v.version, files=len(v.files)
        )
        return Response(_summary(role), status=201)


class RoleDetailView(APIView):
    serializer_class = RoleMeta

    @extend_schema(responses={200: RoleSummary})
    def get(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, VIEW)
        return Response(_summary(_role(ws, name)))

    @extend_schema(responses={200: RoleSummary})
    @transaction.atomic
    def patch(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, PUBLISH)
        role = _role(ws, name)
        ser = RoleMeta(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        changed = [k for k in ("description", "folder") if k in d]
        if "description" in d:
            role.description = d["description"]
        if "folder" in d:
            role.folder = clean_folder(d["folder"])
        role.save(update_fields=changed or ["description"])
        record("ansible_role.update", actor=request.user, workspace=ws, target=role, fields=changed)
        return Response(_summary(role))

    @transaction.atomic
    def delete(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, PUBLISH)
        role = _role(ws, name)
        used = lib.roles_in_use(role)
        if used:
            raise serializers.ValidationError(
                {
                    "name": f"{used} version(s) d'action ou de workflow utilisent ce rôle : elles le protègent (supprimez-les ou publiez-les sans lui d'abord)."
                }
            )
        record("ansible_role.delete", actor=request.user, workspace=ws, target=role)
        role.delete()  # ses versions suivent (cascade) : le blocage d'immuabilité ne vaut que pour une suppression directe d'une version
        return Response(status=204)


class RoleVersionsView(APIView):
    serializer_class = RoleVersionCreate

    @extend_schema(responses={201: RoleSummary})
    @transaction.atomic
    def post(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, PUBLISH)
        role = _role(ws, name)
        ser = RoleVersionCreate(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        v = _add_version(role, request.user, d["version"], d["files"], d["changelog"], d["contract"])
        record(
            "ansible_role.version", actor=request.user, workspace=ws, target=role, version=v.version, files=len(v.files)
        )
        return Response(_summary(role), status=201)


class RoleVersionDetailView(APIView):
    @extend_schema(
        responses={
            200: inline_serializer(
                "AnsibleRoleVersionFiles",
                {
                    "version": serializers.CharField(),
                    "digest": serializers.CharField(),
                    "changelog": serializers.CharField(),
                    "files": serializers.DictField(child=serializers.CharField()),
                    "contract": serializers.DictField(),
                    "contract_problems": serializers.ListField(child=serializers.CharField()),
                },
            )
        }
    )
    def get(self, request, workspace, name, version):
        ws = get_workspace(request.user, workspace, VIEW)
        v = AnsibleRoleVersion.objects.filter(role__workspace=ws, role__name=name, version=version).first()
        if v is None:
            raise Http404()
        return Response(
            {
                "version": v.version,
                "digest": v.digest,
                "changelog": v.changelog,
                "files": v.files,
                "contract": v.contract,
                "contract_problems": lib.contract_problems(v.contract),
            }
        )


class RoleContractDraftView(APIView):
    """Une ébauche de contrat tirée des fichiers (variables de `defaults/main.yml`) : rien n'est enregistré."""

    serializer_class = inline_serializer(
        "RoleContractDraftIn", {"files": serializers.DictField(child=serializers.CharField())}
    )

    @extend_schema(
        responses={
            200: inline_serializer(
                "RoleContractDraft", {"variables": serializers.ListField(child=serializers.DictField())}
            )
        }
    )
    def post(self, request, workspace):
        get_workspace(request.user, workspace, PUBLISH)
        files = lib.check_files(request.data.get("files"))
        return Response({"variables": lib.draft_contract(files).get("variables", [])})
