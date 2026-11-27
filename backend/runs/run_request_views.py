"""Demandes de lancement d'un assistant (ADR 0025, amendement du 2026-11-26) : les voir, les confirmer, les refuser.

Voir = « voir » (dossiers privés respectés) ; confirmer ou refuser = « lancer » ET une session humaine (jamais un jeton, donc jamais un assistant)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from tenancy.permissions import ADMINISTER, RUN, VIEW, get_workspace, has_perm, require_human_session
from workflows import access

from . import run_requests
from .models import RunRequest

MAX_LIST = 100


class AssistantRunRequestSerializer(serializers.ModelSerializer):
    workflow = serializers.SerializerMethodField()
    environment = serializers.CharField(source="environment.kind")
    status_label = serializers.CharField(source="get_status_display")
    requested_by = serializers.CharField(source="requested_by.username", default=None)
    decided_by = serializers.CharField(source="decided_by.username", default=None)
    run_id = serializers.UUIDField(allow_null=True)
    can_decide = serializers.SerializerMethodField()

    class Meta:
        model = RunRequest
        fields = [
            "id",
            "workflow",
            "environment",
            "inputs",
            "reason",
            "client",
            "status",
            "status_label",
            "requested_by",
            "created_at",
            "expires_at",
            "decided_by",
            "decided_at",
            "comment",
            "run_id",
            "can_decide",
        ]

    def get_workflow(self, obj) -> dict:
        v = obj.workflow_version
        return {"slug": v.workflow.slug, "name": v.workflow.name, "version": v.version}

    def get_can_decide(self, obj) -> bool:
        req = self.context["request"]
        ws = obj.workspace
        return (
            obj.status == RunRequest.Status.PENDING
            and has_perm(req.user, ws, RUN)
            and access.Rules(req.user, ws).allows(obj.workflow_version.workflow.folder, RUN)
        )


class AssistantRunDecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, default="", max_length=300)


class RunRequestListView(APIView):
    @extend_schema(responses={200: AssistantRunRequestSerializer(many=True)})
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        run_requests.expire_stale(ws)
        qs = RunRequest.objects.filter(workspace=ws).select_related(
            "workflow_version__workflow", "environment", "requested_by", "decided_by"
        )
        status = request.query_params.get("status")
        if status in RunRequest.Status.values:
            qs = qs.filter(status=status)
        qs = access.visible(qs, request.user, ws, "workflow_version__workflow__folder")[:MAX_LIST]
        return Response(AssistantRunRequestSerializer(qs, many=True, context={"request": request}).data)


class _DecideView(APIView):
    serializer_class = AssistantRunDecisionSerializer
    decide = staticmethod(run_requests.confirm)  # (pas « action » : drf-spectacular lit cet attribut)

    @extend_schema(responses={200: AssistantRunRequestSerializer})
    def post(self, request, workspace, pk):
        require_human_session(request)
        ws = get_workspace(request.user, workspace, RUN)
        ser = AssistantRunDecisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = type(self).decide(ws, pk, request.user, ser.validated_data["comment"])
        return Response(AssistantRunRequestSerializer(req, context={"request": request}).data)


class RunRequestConfirmView(_DecideView):
    decide = staticmethod(run_requests.confirm)


class RunRequestRefuseView(_DecideView):
    decide = staticmethod(run_requests.refuse)


class AutoRunSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    enabled_by = serializers.CharField(allow_null=True, read_only=True)
    enabled_at = serializers.DateTimeField(allow_null=True, read_only=True)
    can_change = serializers.BooleanField(read_only=True)


class AutoRunView(APIView):
    """Validation automatique des lancements demandés par un assistant : lue par qui peut voir l'espace, changée par un administrateur de l'espace
    (session humaine). Seuls dev et recette sont concernés, jamais la production."""

    serializer_class = AutoRunSerializer

    @extend_schema(responses={200: AutoRunSerializer})
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        return Response(run_requests.auto_setting(ws) | {"can_change": has_perm(request.user, ws, ADMINISTER)})

    @extend_schema(responses={200: AutoRunSerializer})
    def put(self, request, workspace):
        require_human_session(request)
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = AutoRunSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(run_requests.set_auto(ws, request.user, ser.validated_data["enabled"]) | {"can_change": True})
