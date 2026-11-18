import json
from typing import Any, cast

from django.db import transaction
from django.http import Http404
from drf_spectacular.utils import extend_schema, extend_schema_serializer, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied
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
    input_form = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowVersion
        fields = ["version", "digest", "changelog", "created_at", "input_schema", "form", "input_form"]

    def get_input_form(self, obj) -> dict | None:
        return obj.graph.get("input_form")

    def get_form(self, obj) -> dict | None:
        return obj.graph.get("form")


class WorkflowSerializer(serializers.ModelSerializer):
    latest_version = serializers.SerializerMethodField()
    folder_level = serializers.SerializerMethodField()
    owner: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="username", read_only=True)
    runner = serializers.SerializerMethodField()
    runner_name = serializers.SerializerMethodField()

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
            "runner",
            "runner_name",
            "runner_force_children",
            "latest_version",
            "folder_level",
        ]

    def get_runner(self, obj) -> str | None:
        return str(obj.runner_id) if obj.runner_id else None

    def get_runner_name(self, obj) -> str | None:
        return obj.runner.name if obj.runner_id else None

    def get_folder_level(self, obj) -> str | None:
        """The person's level in a PRIVATE folder (view, run, edit); null when the folder is not private to them."""
        request = self.context.get("request")
        if request is None:
            return None
        context = cast("dict[str, Any]", self.context)  # DRF fournit toujours un dict ; le stub le type en Mapping
        rules = context.get("_folder_rules")
        if rules is None:  # read once for the whole list, not once per workflow
            rules = context["_folder_rules"] = access.rules(request.user, obj.workspace)
        level = rules.level(obj.folder)
        return level if level in ("view", "run", "edit") else None

    def get_latest_version(self, obj) -> dict | None:
        v = obj.latest_version()
        if not v:
            return None
        child = self.__dict__.get(
            "_version_serializer"
        )  # un seul sérialiseur de version pour toute la liste (voir AR1.5)
        if child is None:
            child = self.__dict__["_version_serializer"] = WorkflowVersionSerializer()
        return child.to_representation(v)


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


@extend_schema_serializer(component_name="WorkflowPublish")
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
        qs = qs.prefetch_related(
            "versions"
        )  # AR1.6 : latest_version() relisait les versions workflow par workflow (50 requêtes pour 50)
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
        if d["slug"] in RESERVED_SLUGS:
            raise serializers.ValidationError({"slug": "Cet identifiant est réservé : choisissez-en un autre."})
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
            wf.refresh_from_db(
                fields=["draft_graph", "draft_revision"]
            )  # ce que la base contient réellement, pas ce que le client a envoyé
        issues = validate(wf.draft_graph, ws, slug)
        # `graph` : la valeur enregistrée, pour que l'éditeur confirme ce qu'il affiche avant de dire « Brouillon enregistré »
        return Response({"revision": wf.draft_revision, "issues": issues, "graph": wf.draft_graph})


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


def publish_workflow(user, ws, slug: str, bump: str, changelog: str) -> WorkflowVersion:
    """Validates the draft of `slug` and publishes it as the next immutable version (one transaction). Raises Http404, a permission error or a
    `ValidationError` carrying the `issues`."""
    with transaction.atomic():
        wf = Workflow.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if wf is None:
            raise Http404()
        access.require(user, ws, wf.folder, PUBLISH)
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
            version=_next_version(latest.version if latest else None, bump),
            graph=pin_graph(wf.draft_graph, ws),
            input_schema=wf.draft_graph.get("inputs") or {"type": "object", "properties": {}},
            changelog=changelog,
            created_by=user,
        )
        record("workflow.publish", actor=user, workspace=ws, target=wf, version=version.version)
    return version


class PublishView(APIView):
    serializer_class = PublishSerializer

    @extend_schema(responses={201: WorkflowVersionSerializer})
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = PublishSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        version = publish_workflow(request.user, ws, slug, ser.validated_data["bump"], ser.validated_data["changelog"])
        return Response(WorkflowVersionSerializer(version).data, status=201)


MAX_BULK_PUBLISH = 50
RESERVED_SLUGS = {
    "publish-many"
}  # le premier segment d'une route fixe sous /workflows/ : un workflow de ce nom ne serait pas adressable


class PublishManySerializer(serializers.Serializer):
    bump = serializers.ChoiceField(choices=["patch", "minor", "major"], default="minor")
    changelog = serializers.CharField(required=False, allow_blank=True, default="", max_length=300)
    slugs = serializers.ListField(child=serializers.SlugField(max_length=80), min_length=1, max_length=MAX_BULK_PUBLISH)


def _children_first(ws, slugs: list[str]) -> list[str]:
    """The selected workflows ordered so that one that calls another selected one comes AFTER it: the parent then pins the child's new version."""
    drafts = dict(Workflow.objects.filter(workspace=ws, slug__in=slugs).values_list("slug", "draft_graph"))
    calls: dict[str, set[str]] = {
        s: {
            str(n["workflow"])
            for n in (g or {}).get("nodes", [])
            if isinstance(n, dict)
            and n.get("type") in ("subworkflow", "foreach", "while")
            and n.get("workflow") in slugs
        }
        for s, g in drafts.items()
    }
    done: list[str] = []

    def visit(s: str, stack: tuple = ()):
        if s in done or s in stack:
            return
        for child in sorted(calls.get(s, ())):
            visit(child, (*stack, s))
        done.append(s)

    for s in slugs:
        visit(s)
    return done


class PublishManyView(APIView):
    """Publishes several workflows in one request, each in its own transaction: one that cannot be published (invalid, empty, not allowed) is
    reported and never stops the others. A workflow whose draft equals its latest version is skipped (nothing to publish)."""

    serializer_class = PublishManySerializer

    @extend_schema(
        request=PublishManySerializer,
        responses={
            200: inline_serializer(
                "PublishManyResult",
                {
                    "results": serializers.ListField(child=serializers.DictField()),
                    "published": serializers.IntegerField(),
                    "skipped": serializers.IntegerField(),
                    "error": serializers.IntegerField(),
                },
            )
        },
    )
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = PublishManySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        slugs = list(dict.fromkeys(d["slugs"]))
        results = []
        for slug in _children_first(ws, slugs):
            wf = Workflow.objects.filter(workspace=ws, slug=slug).first()
            if wf is not None and not access.rules(request.user, ws).allows(wf.folder, VIEW):
                wf = None  # a workflow the person cannot see is "not found", like everywhere else
            if wf is None:
                results.append({"slug": slug, "status": "error", "message": "Workflow introuvable."})
                continue
            latest = wf.latest_version()
            if latest is not None and wf.draft_graph.get("nodes") and pin_graph(wf.draft_graph, ws) == latest.graph:
                results.append(
                    {
                        "slug": slug,
                        "name": wf.name,
                        "status": "skipped",
                        "message": f"Aucune modification depuis la v{latest.version}.",
                        "version": latest.version,
                    }
                )
                continue
            try:
                version = publish_workflow(request.user, ws, slug, d["bump"], d["changelog"])
            except serializers.ValidationError as exc:
                issues = (exc.detail.get("issues") if isinstance(exc.detail, dict) else None) or []
                first = issues[0] if issues else {}
                message = first.get("message") if isinstance(first, dict) else None
                results.append(
                    {
                        "slug": slug,
                        "name": wf.name,
                        "status": "error",
                        "message": str(message or "Publication refusée."),
                        "issues": len(issues),
                    }
                )
            except PermissionDenied as exc:
                results.append({"slug": slug, "name": wf.name, "status": "error", "message": str(exc.detail)})
            else:
                results.append({"slug": slug, "name": wf.name, "status": "published", "version": version.version})
        counts = {k: sum(1 for r in results if r["status"] == k) for k in ("published", "skipped", "error")}
        return Response({"results": results, **counts})


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
