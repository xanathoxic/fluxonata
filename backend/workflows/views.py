import json

from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core.tags import clean_tags
from tenancy.permissions import EDIT, PUBLISH, VIEW, get_workspace

from . import access
from .models import Workflow, WorkflowVersion, empty_graph
from .validation import pin_graph, validate

MAX_GRAPH_BYTES = 512 * 1024


class Conflict(APIException):
    status_code = 409
    default_code = "revision_conflict"
    default_detail = "Le brouillon a été modifié ailleurs."

    def __init__(self, detail: dict):
        super().__init__(detail)
        self.detail = detail  # keep raw JSON types (current_revision stays an integer)


class WorkflowVersionSerializer(serializers.ModelSerializer):
    form = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowVersion
        fields = ["version", "digest", "changelog", "created_at", "input_schema", "form"]

    def get_form(self, obj) -> dict | None:
        return obj.graph.get("form")


class WorkflowSerializer(serializers.ModelSerializer):
    latest_version = serializers.SerializerMethodField()
    folder_level = serializers.SerializerMethodField()
    owner = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Workflow
        fields = [
            "slug",
            "name",
            "folder",
            "description",
            "tags",
            "owner",
            "draft_revision",
            "updated_at",
            "deprecated",
            "deprecation_reason",
            "in_catalog",
            "error_workflow",
            "success_workflow",
            "mcp_exposed",
            "latest_version",
            "folder_level",
        ]

    def get_folder_level(self, obj) -> str | None:
        """The person's level in a PRIVATE folder (view, run, edit); null when the folder is not private to them."""
        request = self.context.get("request")
        if request is None:
            return None
        rules = self.context.get("_folder_rules")
        if rules is None:  # read once for the whole list, not once per workflow
            rules = self.context["_folder_rules"] = access.rules(request.user, obj.workspace)
        level = rules.level(obj.folder)
        return level if level in ("view", "run", "edit") else None

    def get_latest_version(self, obj) -> dict | None:
        v = obj.latest_version()
        return WorkflowVersionSerializer(v).data if v else None


class WorkflowDetailSerializer(WorkflowSerializer):
    class Meta(WorkflowSerializer.Meta):
        fields = WorkflowSerializer.Meta.fields + ["draft_graph"]


class CreateSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    folder = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    tags = serializers.ListField(
        child=serializers.CharField(max_length=60), required=False, default=list, max_length=20
    )


class DraftSerializer(serializers.Serializer):
    graph = serializers.JSONField()
    revision = serializers.IntegerField(min_value=1)


class PublishSerializer(serializers.Serializer):
    bump = serializers.ChoiceField(choices=["patch", "minor", "major"], default="minor")
    changelog = serializers.CharField(required=False, allow_blank=True, default="", max_length=300)


def _workflow_or_404(user, ws, slug, perm=VIEW):
    return access.workflow_or_404(user, ws, slug, perm)


class WorkflowListCreateView(ListAPIView):
    serializer_class = WorkflowSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = access.visible(Workflow.objects.filter(workspace=ws), self.request.user, ws).select_related("owner")
        if q := self.request.query_params.get("q"):
            qs = qs.filter(name__icontains=q)
        return qs

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        ser = CreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        access.require(request.user, ws, d["folder"], EDIT)
        d["tags"] = clean_tags(d["tags"])
        if Workflow.objects.filter(workspace=ws, slug=d["slug"]).exists():
            raise serializers.ValidationError({"slug": "Cet identifiant existe déjà dans l'espace."})
        wf = Workflow.objects.create(workspace=ws, owner=request.user, **d)
        record("workflow.create", actor=request.user, workspace=ws, target=wf)
        return Response(WorkflowDetailSerializer(wf).data, status=201)


class WorkflowDetailView(RetrieveAPIView):
    serializer_class = WorkflowDetailSerializer

    def get_object(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        return _workflow_or_404(self.request.user, ws, self.kwargs["slug"])


class DraftView(APIView):
    serializer_class = DraftSerializer

    def put(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        ser = DraftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        graph = ser.validated_data["graph"]
        if len(json.dumps(graph)) > MAX_GRAPH_BYTES or not isinstance(graph, dict):
            raise serializers.ValidationError({"graph": "Graphe trop volumineux ou invalide."})
        with transaction.atomic():
            wf = Workflow.objects.select_for_update().filter(workspace=ws, slug=slug).first()
            if wf is None:
                raise Http404()
            access.require(request.user, ws, wf.folder, EDIT)
            if wf.draft_revision != ser.validated_data["revision"]:
                raise Conflict(
                    {
                        "message": "Le brouillon a été modifié ailleurs. Rechargez pour voir la version la plus récente.",
                        "current_revision": wf.draft_revision,
                    }
                )
            wf.draft_graph = graph
            wf.draft_revision += 1
            wf.save()
        issues = validate(graph, ws, slug)
        return Response({"revision": wf.draft_revision, "issues": issues})


class ValidateView(APIView):
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = _workflow_or_404(request.user, ws, slug)
        graph = request.data.get("graph") or wf.draft_graph
        return Response({"issues": validate(graph, ws, slug)})


def _next_version(latest: str | None, bump: str) -> str:
    if latest is None:
        return "1.0.0"
    major, minor, patch = (int(x) for x in latest.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


class PublishView(APIView):
    serializer_class = PublishSerializer

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = PublishSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            wf = Workflow.objects.select_for_update().filter(workspace=ws, slug=slug).first()
            if wf is None:
                raise Http404()
            access.require(request.user, ws, wf.folder, PUBLISH)
            issues = validate(wf.draft_graph, ws, slug)
            errors = [i for i in issues if i["level"] == "error"]
            if errors or not wf.draft_graph.get("nodes"):
                exc = serializers.ValidationError(
                    {"issues": errors or [{"code": "empty", "message": "Le workflow est vide."}]}
                )
                raise exc
            latest = wf.latest_version()
            version = WorkflowVersion.objects.create(
                workflow=wf,
                version=_next_version(latest.version if latest else None, ser.validated_data["bump"]),
                graph=pin_graph(wf.draft_graph, ws),
                input_schema=wf.draft_graph.get("inputs") or {"type": "object", "properties": {}},
                changelog=ser.validated_data["changelog"],
                created_by=request.user,
            )
            record("workflow.publish", actor=request.user, workspace=ws, target=wf, version=version.version)
        return Response(WorkflowVersionSerializer(version).data, status=201)


class VersionListView(ListAPIView):
    serializer_class = WorkflowVersionSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        return _workflow_or_404(self.request.user, ws, self.kwargs["slug"]).versions.order_by("-created_at", "-id")


def default_graph():
    return empty_graph()


def _example_for(schema: dict):
    """A valid-looking example body derived from the input schema (defaults/examples first)."""
    out = {}
    for name, prop in (schema.get("properties") or {}).items():
        if "default" in prop:
            out[name] = prop["default"]
        elif prop.get("examples"):
            out[name] = prop["examples"][0]
        else:
            out[name] = {
                "string": "texte",
                "integer": 1,
                "number": 1.0,
                "boolean": True,
                "array": [],
                "object": {},
            }.get(prop.get("type"), None)
    return out


class ContractView(APIView):
    """Generated call contract of a published workflow: input schema, endpoint, example request/response."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = _workflow_or_404(request.user, ws, slug)
        version = wf.latest_version()
        if version is None:
            raise serializers.ValidationError({"version": "Ce workflow n'a pas de version publiée."})
        path = f"/api/v1/workspaces/{ws.slug}/workflows/{wf.slug}/runs/"
        example = {"environment": "test", "inputs": _example_for(version.input_schema)}
        body = json.dumps(example, ensure_ascii=False)
        return Response(
            {
                "workflow": wf.slug,
                "version": version.version,
                "digest": version.digest,
                "method": "POST",
                "endpoint": path,
                "input_schema": version.input_schema,
                "example_request": example,
                "example_response_202": {
                    "id": "<uuid>",
                    "status": "running",
                    "url": f"{path}<uuid>/".replace("runs/<uuid>", "../workflow-runs/<uuid>"),
                },
                "status_url": f"/api/v1/workspaces/{ws.slug}/workflow-runs/<id>/",
                "events_url": f"/api/v1/workspaces/{ws.slug}/workflow-runs/<id>/events/?after=0",
                "artifacts_url": f"/api/v1/workspaces/{ws.slug}/workflow-runs/<id>/artifacts/",
                "cancel_url": f"/api/v1/workspaces/{ws.slug}/workflow-runs/<id>/cancel/",
                "curl": (
                    f"curl -X POST https://<hôte>{path} -H 'Authorization: Bearer <jeton>' -H 'Content-Type: application/json' "
                    f"-H 'Idempotency-Key: <clé-unique>' -d '{body}'"
                ),
                "notes": [
                    "Réponse 202 : l'exécution est asynchrone ; suivez status_url ou events_url.",
                    "Idempotency-Key : rejouer la même clé renvoie la même exécution (200).",
                    'Erreurs : {"error": {"code", "message", "details"}}.',
                ],
            }
        )
