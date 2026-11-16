"""Promotions of a workflow version to another environment (request, list, decision). The routes sit under the workspace;
they live with the workflows because they read folder access (`workflows.access`)."""

from typing import Any, cast

from django.http import Http404
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from tenancy import governance
from tenancy.models import Environment, Promotion
from tenancy.permissions import RUN, VIEW, get_workspace

from . import access
from .models import WorkflowVersion


class PromotionSerializer(serializers.ModelSerializer):
    workflow = serializers.CharField(source="workflow_version.workflow.name")
    workflow_slug = serializers.CharField(source="workflow_version.workflow.slug")
    version = serializers.CharField(source="workflow_version.version")
    environment = serializers.CharField(source="environment.kind")
    requested_by = serializers.CharField(source="requested_by.username", default=None)
    reviewed_by = serializers.CharField(source="reviewed_by.username", default=None)
    can_decide = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = [
            "id",
            "workflow",
            "workflow_slug",
            "version",
            "environment",
            "status",
            "requested_by",
            "reviewed_by",
            "comment",
            "created_at",
            "decided_at",
            "can_decide",
        ]

    def get_can_decide(self, obj) -> bool:
        u = self.context["request"].user
        policies = cast(
            "dict[str, Any]", self.context
        ).setdefault(  # DRF fournit toujours un dict ; le stub le type en Mapping
            "_policies", {}
        )  # AR1.6 : la politique est lue une fois par espace, pas une fois par demande
        if obj.workspace_id not in policies:
            policies[obj.workspace_id] = governance.current_policy(obj.workspace)[1]
        rules = policies[obj.workspace_id]
        return obj.status == "requested" and not (rules["forbid_self_review"] and obj.requested_by_id == u.id)


class PromotionListView(ListAPIView):
    serializer_class = PromotionSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = (
            Promotion.objects.filter(workspace=ws)
            .select_related("workflow_version__workflow", "environment", "requested_by", "reviewed_by", "workspace")
            .order_by("-created_at")
        )
        qs = access.visible(qs, self.request.user, ws, "workflow_version__workflow__folder")
        return qs.filter(status=self.request.query_params["status"]) if "status" in self.request.query_params else qs


class PromoteView(APIView):
    def post(self, request, workspace, slug, version):
        ws = get_workspace(request.user, workspace, VIEW)
        wv = WorkflowVersion.objects.filter(workflow__workspace=ws, workflow__slug=slug, version=version).first()
        env = Environment.objects.filter(workspace=ws, kind=request.data.get("environment")).first()
        if wv is None or env is None:
            raise Http404()
        access.require(request.user, ws, wv.workflow.folder, RUN)
        promo = governance.request_promotion(wv, env, request.user, str(request.data.get("comment", "")))
        return Response(PromotionSerializer(promo, context={"request": request}).data, status=201)


class PromotionDecideView(APIView):
    def post(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        found = Promotion.objects.filter(workspace=ws, pk=pk).select_related("workflow_version__workflow").first()
        if found is None:
            raise Http404()
        access.require(request.user, ws, found.workflow_version.workflow.folder, VIEW)
        promo = governance.decide_promotion(
            pk, request.user, bool(request.data.get("approve")), str(request.data.get("comment", ""))
        )
        return Response(PromotionSerializer(promo, context={"request": request}).data)
