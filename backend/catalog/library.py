"""Library API: action authoring, versions, deprecation, comparison, rollback and reverse dependencies."""

import ast
import difflib
import json
import re

import jsonschema
from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core.folders import clean_folder
from core.tags import clean_tags
from runs.runners import NETWORK_POLICIES, RUNTIMES
from tenancy.permissions import PUBLISH, VIEW, get_workspace, require_human_session
from workflows import access
from workflows.models import Workflow, WorkflowVersion

from . import contract
from .models import Action, ActionTestCase, ActionVersion
from .serializers import ActionSerializer, ActionVersionSerializer

TAG_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,29}")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MAX_SOURCE = 200 * 1024
SECRET_LIKE = re.compile(
    r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})"
)


def semver_key(v: str):
    return tuple(int(x) for x in v.split("."))


def bump(latest: str | None, kind: str) -> str:
    if latest is None:
        return "1.0.0"
    a, b, c = semver_key(latest)
    return {"major": f"{a + 1}.0.0", "minor": f"{a}.{b + 1}.0", "patch": f"{a}.{b}.{c + 1}"}[kind]


class VersionInput(serializers.Serializer):
    version = serializers.CharField(required=False)
    bump = serializers.ChoiceField(choices=["patch", "minor", "major"], default="minor")
    runtime = serializers.ChoiceField(choices=sorted(RUNTIMES), default="python")
    entrypoint = serializers.RegexField(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", default="run")
    source = serializers.CharField(max_length=MAX_SOURCE, trim_whitespace=False)
    input_schema = serializers.JSONField()
    output_schema = serializers.JSONField()
    license = serializers.CharField(max_length=40, default="Apache-2.0")
    side_effects = serializers.ChoiceField(choices=["none", "external"], default="none")
    idempotent = serializers.BooleanField(default=True)
    network_policy = serializers.ChoiceField(choices=sorted(NETWORK_POLICIES), default="none")
    limits = serializers.JSONField(default=dict)
    examples = serializers.JSONField(default=list)
    secrets_required = serializers.JSONField(default=list)
    required_tags = serializers.JSONField(default=list)
    changelog = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")

    def validate_version(self, value):
        if not SEMVER.match(value):
            raise serializers.ValidationError("Version sémantique attendue (ex. 1.2.0).")
        return value

    def validate_source(self, value):
        if SECRET_LIKE.search(value):
            raise serializers.ValidationError("Le code semble contenir un secret : utilisez une connexion.")
        return value

    def validate(self, attrs):
        for key in ("input_schema", "output_schema"):
            schema = attrs[key]
            try:
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise jsonschema.SchemaError("Le schéma doit décrire un objet (type: object).")
                jsonschema.Draft202012Validator.check_schema(schema)
            except jsonschema.SchemaError as exc:
                raise serializers.ValidationError({key: f"Schéma invalide : {exc.message[:150]}"}) from None
        if attrs["runtime"] == "python":
            try:
                ast.parse(attrs["source"])  # syntax check only; the code is never executed here
            except SyntaxError as exc:
                raise serializers.ValidationError(
                    {"source": f"Erreur de syntaxe ligne {exc.lineno} : {exc.msg}"}
                ) from None
        limits = {"timeout_seconds": 30, "memory_mb": 128, **attrs["limits"]}
        if not (1 <= limits["timeout_seconds"] <= 3600 and 16 <= limits["memory_mb"] <= 2048):
            raise serializers.ValidationError({"limits": "Délai : 1 à 3600 s ; mémoire : 16 à 2048 Mo."})
        attrs["limits"] = {k: limits[k] for k in ("timeout_seconds", "memory_mb")}
        for slot in attrs["secrets_required"]:
            if not (
                isinstance(slot, dict)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,30}", str(slot.get("slot", "")))
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,40}", str(slot.get("env", "")))
            ):
                raise serializers.ValidationError(
                    {"secrets_required": "Chaque entrée : {slot: minuscules, env: MAJUSCULES}."}
                )
        tags = attrs["required_tags"]
        if not (
            isinstance(tags, list) and len(tags) <= 5 and all(isinstance(t, str) and TAG_RE.fullmatch(t) for t in tags)
        ):
            raise serializers.ValidationError(
                {"required_tags": "Jusqu'à 5 étiquettes (minuscules, chiffres, - _ .), 30 caractères au plus."}
            )
        attrs["required_tags"] = sorted(set(tags))
        if attrs["side_effects"] == "external" and attrs["idempotent"] and "idempotent" not in self.initial_data:
            attrs["idempotent"] = False  # external effects are non-idempotent unless stated otherwise
        return attrs


def require_contract(description: str, data: dict) -> None:
    """A version is published only with a complete contract: what the action does, and every input and output described
    (or said to be absent)."""
    missing = contract.problems(description, data["input_schema"], data["output_schema"])
    if missing:
        raise serializers.ValidationError({"contract": missing})


def _create_version(action, data, user) -> ActionVersion:
    latest = action.latest_version()
    version = data.get("version") or bump(latest.version if latest else None, data["bump"])
    if latest and semver_key(version) <= max(semver_key(v) for v in action.versions.values_list("version", flat=True)):
        raise serializers.ValidationError(
            {"version": "La nouvelle version doit être supérieure aux versions existantes."}
        )
    fields = {
        k: data[k]
        for k in (
            "runtime",
            "entrypoint",
            "source",
            "input_schema",
            "output_schema",
            "license",
            "side_effects",
            "idempotent",
            "network_policy",
            "limits",
            "examples",
            "secrets_required",
            "required_tags",
            "changelog",
        )
    }
    return ActionVersion.objects.create(action=action, version=version, created_by=user, **fields)


class ActionCreateSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    domain = serializers.CharField(required=False, allow_blank=True, default="", max_length=80)
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=30), required=False, default=list, max_length=10
    )
    version = VersionInput()
    ai_generated = serializers.BooleanField(required=False, default=False)
    reviewed = serializers.BooleanField(required=False, default=False)


class ActionCreateView(APIView):
    serializer_class = ActionCreateSerializer

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = ActionCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if Action.objects.filter(workspace=ws, slug=d["slug"]).exists():
            raise serializers.ValidationError({"slug": "Cet identifiant existe déjà dans l'espace."})
        if d["ai_generated"] and not d["reviewed"]:
            raise serializers.ValidationError(
                {"reviewed": "Relisez le code proposé par l'assistant, puis confirmez-le."}
            )
        require_contract(d["description"], d["version"])
        action = Action.objects.create(
            workspace=ws,
            owner=request.user,
            slug=d["slug"],
            name=d["name"],
            description=d["description"],
            domain=d["domain"],
            folder=clean_folder(d["folder"]),
            tags=clean_tags(d["tags"]),
        )
        v = _create_version(action, {**d["version"], "version": d["version"].get("version", "1.0.0")}, request.user)
        record(
            "action.create",
            actor=request.user,
            workspace=ws,
            target=action,
            version=v.version,
            ai_generated=d["ai_generated"],
        )
        return Response(ActionSerializer(action).data, status=201)


def _action(ws, slug) -> Action:
    a = Action.objects.filter(workspace=ws, slug=slug).first()
    if a is None:
        raise Http404()
    return a


class VersionDetailSerializer(ActionVersionSerializer):
    class Meta(ActionVersionSerializer.Meta):
        fields = ActionVersionSerializer.Meta.fields + [
            "source",
            "entrypoint",
            "changelog",
            "secrets_required",
            "required_tags",
        ]


class ActionVersionsView(APIView):
    serializer_class = VersionInput

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = _action(ws, slug).versions.order_by("-created_at", "-id")
        return Response({"results": VersionDetailSerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        action = Action.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if action is None:
            raise Http404()
        ser = VersionInput(data=request.data)
        ser.is_valid(raise_exception=True)
        require_contract(action.description, ser.validated_data)
        v = _create_version(action, ser.validated_data, request.user)
        record(
            "action.publish", actor=request.user, workspace=ws, target=action, version=v.version, digest=v.source_digest
        )
        return Response(VersionDetailSerializer(v).data, status=201)


class ActionMetaView(APIView):
    """Move an action to another folder, rename it or change its description (published versions are untouched)."""

    def patch(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        a = _action(ws, slug)
        d = request.data
        if "name" in d:
            name = str(d["name"]).strip()
            if not name or len(name) > 160:
                raise serializers.ValidationError({"name": "Nom requis (160 caractères maximum)."})
            a.name = name
        if "description" in d:
            a.description = str(d["description"])[:2000]
        if "folder" in d:
            a.folder = clean_folder(d["folder"])
        if "tags" in d:
            a.tags = clean_tags(d["tags"])
        a.save()
        record(
            "action.update",
            actor=request.user,
            workspace=ws,
            target=a,
            fields=sorted(set(d) & {"name", "description", "folder", "tags"}),
        )
        return Response(ActionSerializer(a).data)


class ActionDeprecateView(APIView):
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        a = _action(ws, slug)
        reinstate = bool(request.data.get("reinstate"))
        if reinstate and a.status == Action.Status.PROPOSED:
            raise serializers.ValidationError(
                {"reinstate": "Une proposition d'assistant s'active après relecture (« Activer »)."}
            )
        a.status = Action.Status.ACTIVE if reinstate else Action.Status.DEPRECATED
        a.deprecation_reason = "" if reinstate else str(request.data.get("reason", ""))[:300]
        a.save()
        record("action.reinstate" if reinstate else "action.deprecate", actor=request.user, workspace=ws, target=a)
        return Response(ActionSerializer(a).data)


class ActionActivateView(APIView):
    """Relecture humaine d'une proposition d'assistant : elle devient une action ordinaire (ADR 0025). Jamais par jeton."""

    @transaction.atomic
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        require_human_session(request)
        a = Action.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if a is None:
            raise Http404()
        if a.status != Action.Status.PROPOSED:
            raise serializers.ValidationError({"status": "Cette action n'est pas une proposition d'assistant."})
        if a.latest_version() is None:
            raise serializers.ValidationError({"status": "Aucune version à activer."})
        a.status = Action.Status.ACTIVE
        a.save(update_fields=["status"])
        record("action.activate", actor=request.user, workspace=ws, target=a)
        return Response(ActionSerializer(a).data)


class ActionRollbackView(APIView):
    """Back to an earlier version = a NEW version copying it (history is never rewritten)."""

    @transaction.atomic
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        action = Action.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if action is None:
            raise Http404()
        old = action.versions.filter(version=request.data.get("to_version")).first()
        if old is None:
            raise serializers.ValidationError({"to_version": "Version inconnue."})
        latest = action.latest_version()
        fields = {
            k: getattr(old, k)
            for k in (
                "runtime",
                "entrypoint",
                "source",
                "input_schema",
                "output_schema",
                "license",
                "side_effects",
                "idempotent",
                "network_policy",
                "limits",
                "examples",
                "secrets_required",
                "required_tags",
            )
        }
        top = max(action.versions.values_list("version", flat=True), key=semver_key)
        v = ActionVersion.objects.create(
            action=action,
            version=bump(top, "patch"),
            created_by=request.user,
            changelog=f"Retour à la version {old.version}",
            **fields,
        )
        record(
            "action.rollback",
            actor=request.user,
            workspace=ws,
            target=action,
            to=old.version,
            new=v.version,
            latest=latest.version,
        )
        return Response(VersionDetailSerializer(v).data, status=201)


class ActionCompareView(APIView):
    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        action = _action(ws, slug)
        a = action.versions.filter(version=request.query_params.get("a")).first()
        b = action.versions.filter(version=request.query_params.get("b")).first()
        if a is None or b is None:
            raise Http404()
        changed = [
            f
            for f in (
                "runtime",
                "entrypoint",
                "input_schema",
                "output_schema",
                "side_effects",
                "idempotent",
                "network_policy",
                "limits",
                "secrets_required",
                "license",
            )
            if getattr(a, f) != getattr(b, f)
        ]
        diff = "".join(
            difflib.unified_diff(a.source.splitlines(True), b.source.splitlines(True), f"v{a.version}", f"v{b.version}")
        )
        return Response(
            {
                "a": a.version,
                "b": b.version,
                "changed_fields": changed,
                "source_diff": diff,
                "breaking": "input_schema" in changed or "output_schema" in changed,
            }
        )


class ActionDependentsView(APIView):
    """Reverse dependencies: which published workflow versions use this action (and which exact version)."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        _action(ws, slug)
        out = []
        visible = access.visible(
            WorkflowVersion.objects.filter(workflow__workspace=ws), request.user, ws, "workflow__folder"
        )
        for wv in visible.select_related("workflow"):
            used = sorted(
                {n["version"] for n in wv.graph["nodes"] if n["type"] == "action" and n["action"] == slug}
                | {
                    n["compensation"]["version"]
                    for n in wv.graph["nodes"]
                    if n["type"] == "action" and n.get("compensation", {}).get("action") == slug
                }
            )
            if used:
                out.append({"workflow": wv.workflow.slug, "workflow_version": wv.version, "action_versions": used})
        return Response({"results": out})


# ------------------------------------------------------------------ workflow library helpers
class WorkflowRollbackView(APIView):
    @transaction.atomic
    def post(self, request, workspace, slug):
        from workflows.views import _next_version

        ws = get_workspace(request.user, workspace, PUBLISH)
        wf = Workflow.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if wf is None:
            raise Http404()
        old = wf.versions.filter(version=request.data.get("to_version")).first()
        if old is None:
            raise serializers.ValidationError({"to_version": "Version inconnue."})
        top = max(wf.versions.values_list("version", flat=True), key=semver_key)
        v = WorkflowVersion.objects.create(
            workflow=wf,
            version=_next_version(top, "patch"),
            graph=old.graph,
            input_schema=old.input_schema,
            changelog=f"Retour à la version {old.version}",
            created_by=request.user,
        )
        record("workflow.rollback", actor=request.user, workspace=ws, target=wf, to=old.version, new=v.version)
        return Response({"version": v.version, "digest": v.digest}, status=201)


class WorkflowCompareView(APIView):
    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = Workflow.objects.filter(workspace=ws, slug=slug).first()
        if wf is None:
            raise Http404()
        a = wf.versions.filter(version=request.query_params.get("a")).first()
        b = wf.versions.filter(version=request.query_params.get("b")).first()
        if a is None or b is None:
            raise Http404()
        na, nb = {n["id"]: n for n in a.graph["nodes"]}, {n["id"]: n for n in b.graph["nodes"]}
        ea = {(e["from"], e["to"], e.get("label")) for e in a.graph["edges"]}
        eb = {(e["from"], e["to"], e.get("label")) for e in b.graph["edges"]}
        strip = lambda n: {k: v for k, v in n.items() if k != "ui"}  # noqa: E731 - layout is not a functional change
        return Response(
            {
                "a": a.version,
                "b": b.version,
                "nodes_added": sorted(set(nb) - set(na)),
                "nodes_removed": sorted(set(na) - set(nb)),
                "nodes_changed": sorted(i for i in set(na) & set(nb) if strip(na[i]) != strip(nb[i])),
                "edges_added": [list(e) for e in sorted(eb - ea, key=str)],
                "edges_removed": [list(e) for e in sorted(ea - eb, key=str)],
                "inputs_changed": a.input_schema != b.input_schema,
            }
        )


class WorkflowDependentsView(APIView):
    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        out = []
        visible = access.visible(
            WorkflowVersion.objects.filter(workflow__workspace=ws), request.user, ws, "workflow__folder"
        )
        for wv in visible.select_related("workflow"):
            used = sorted(
                {
                    n["version"]
                    for n in wv.graph["nodes"]
                    if n["type"] in ("subworkflow", "foreach") and n.get("workflow") == slug
                }
            )
            if used:
                out.append({"workflow": wv.workflow.slug, "workflow_version": wv.version, "uses_versions": used})
        return Response({"results": out})


class WorkflowDeprecateView(APIView):
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        wf = access.workflow_or_404(request.user, ws, slug, PUBLISH)
        reinstate = bool(request.data.get("reinstate"))
        wf.deprecated, wf.deprecation_reason = (
            (not reinstate),
            ("" if reinstate else str(request.data.get("reason", ""))[:300]),
        )
        wf.save()
        record("workflow.reinstate" if reinstate else "workflow.deprecate", actor=request.user, workspace=ws, target=wf)
        return Response({"deprecated": wf.deprecated, "reason": wf.deprecation_reason})


MAX_TEST_CASES = 50
MAX_TEST_BYTES = 20 * 1024


def _case_row(case: ActionTestCase, schema: dict | None) -> dict:
    ok = True
    if schema is not None:
        try:
            jsonschema.validate(case.inputs, schema)
        except jsonschema.ValidationError:
            ok = False  # the contract changed since: the set is kept but flagged
    return {
        "id": case.pk,
        "name": case.name,
        "inputs": case.inputs,
        "valid": ok,
        "created_by": getattr(case.created_by, "username", None),
        "updated_at": case.updated_at,
    }


class TestCaseInput(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    inputs = serializers.JSONField()

    def validate_name(self, v):
        v = v.strip()
        if not v:
            raise serializers.ValidationError("Nom requis.")
        return v

    def validate_inputs(self, v):
        if not isinstance(v, dict):
            raise serializers.ValidationError("Les entrées doivent être un objet.")
        raw = json.dumps(v)
        if len(raw) > MAX_TEST_BYTES:
            raise serializers.ValidationError("Jeu de test trop volumineux (20 Ko maximum).")
        if SECRET_LIKE.search(raw):
            raise serializers.ValidationError(
                "Cela ressemble à un secret : utilisez une connexion plutôt qu'une valeur saisie."
            )
        return v


def _validated(action: Action, data, partial=False) -> dict:
    s = TestCaseInput(data=data, partial=partial)
    s.is_valid(raise_exception=True)
    out = s.validated_data
    version = action.latest_version()
    if "inputs" in out and version is not None:
        try:
            jsonschema.validate(out["inputs"], version.input_schema)
        except jsonschema.ValidationError as exc:
            raise serializers.ValidationError(
                {"inputs": f"Ne respecte pas le contrat de l'action : {exc.message[:200]}"}
            ) from None
    return out


class ActionTestCasesView(APIView):
    """Saved test sets of an action: everyone who can see the action reads them, designers manage them."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        a = _action(ws, slug)
        v = a.latest_version()
        schema = v.input_schema if v else None
        return Response({"results": [_case_row(c, schema) for c in a.test_cases.select_related("created_by")]})

    @transaction.atomic
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        a = Action.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if a is None:
            raise Http404()
        data = _validated(a, request.data)
        if a.test_cases.count() >= MAX_TEST_CASES:
            raise serializers.ValidationError({"name": f"{MAX_TEST_CASES} jeux de test au maximum par action."})
        if a.test_cases.filter(name=data["name"]).exists():
            raise serializers.ValidationError({"name": "Un jeu de test porte déjà ce nom."})
        case = ActionTestCase.objects.create(action=a, created_by=request.user, **data)
        record("action.testcase_create", actor=request.user, workspace=ws, target=a, name=case.name)
        v = a.latest_version()
        return Response(_case_row(case, v.input_schema if v else None), status=201)


class ActionTestCaseView(APIView):
    def _get(self, request, workspace, slug, pk):
        ws = get_workspace(request.user, workspace, PUBLISH)
        a = _action(ws, slug)
        case = a.test_cases.filter(pk=pk).first()
        if case is None:
            raise Http404()
        return ws, a, case

    def patch(self, request, workspace, slug, pk):
        ws, a, case = self._get(request, workspace, slug, pk)
        data = _validated(a, {"name": case.name, "inputs": case.inputs, **request.data})
        if a.test_cases.filter(name=data["name"]).exclude(pk=case.pk).exists():
            raise serializers.ValidationError({"name": "Un jeu de test porte déjà ce nom."})
        case.name, case.inputs = data["name"], data["inputs"]
        case.save()
        record("action.testcase_update", actor=request.user, workspace=ws, target=a, name=case.name)
        v = a.latest_version()
        return Response(_case_row(case, v.input_schema if v else None))

    def delete(self, request, workspace, slug, pk):
        ws, a, case = self._get(request, workspace, slug, pk)
        name = case.name
        case.delete()
        record("action.testcase_delete", actor=request.user, workspace=ws, target=a, name=name)
        return Response(status=204)
