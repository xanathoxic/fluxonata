"""Lancements programmés par le nœud « Programmer » (ADR 0034) : les voir et annuler ceux qui attendent encore.

Droits côté serveur : voir = « voir » (tout membre) ; annuler = « lancer » (opérateurs et plus). Les entrées du workflow programmé ne sont
jamais renvoyées (elles peuvent contenir des données métier) : la liste donne le workflow, l'heure, l'état et l'origine."""

from django.db import transaction
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.permissions import RUN, VIEW, get_workspace

from .models import ScheduledLaunch

MAX_LIST = 500


class ScheduledLaunchSerializer(serializers.ModelSerializer):
    workflow = serializers.CharField(source="workflow.slug", read_only=True)
    workflow_name = serializers.CharField(source="workflow.name", read_only=True)
    environment = serializers.CharField(source="environment.kind", read_only=True)
    requested_by = serializers.CharField(source="requested_by.username", read_only=True, default="")
    source_workflow = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledLaunch
        fields = [
            "id",
            "workflow",
            "workflow_name",
            "version",
            "environment",
            "run_at",
            "timezone",
            "status",
            "note",
            "requested_by",
            "source_run",
            "source_node",
            "source_workflow",
            "fired_run",
            "fired_at",
            "created_at",
        ]

    def get_source_workflow(self, obj) -> str:
        run = obj.source_run
        return run.workflow_version.workflow.slug if run is not None else ""


class ScheduledLaunchListView(APIView):
    @extend_schema(operation_id="workspace_scheduled_launches_list", responses=OpenApiTypes.OBJECT)
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = ScheduledLaunch.objects.filter(workspace=ws).select_related(
            "workflow", "environment", "requested_by", "source_run__workflow_version__workflow"
        )
        status = request.query_params.get("status")
        if status in ScheduledLaunch.Status.values:
            rows = rows.filter(status=status)
        slug = request.query_params.get("workflow")
        if slug:
            rows = rows.filter(workflow__slug=slug)
        # d'abord ce qui attend (le plus proche en tête), ensuite l'historique récent
        pending = list(rows.filter(status=ScheduledLaunch.Status.PENDING)[:MAX_LIST])
        done = list(
            rows.exclude(status=ScheduledLaunch.Status.PENDING).order_by("-run_at")[: max(0, MAX_LIST - len(pending))]
        )
        return Response(
            {"count": len(pending) + len(done), "results": ScheduledLaunchSerializer(pending + done, many=True).data}
        )


class ScheduledLaunchDetailView(APIView):
    @extend_schema(operation_id="workspace_scheduled_launch_cancel", responses={204: None})
    @transaction.atomic
    def delete(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, RUN)
        launch = (
            ScheduledLaunch.objects.select_for_update().filter(workspace=ws, pk=pk).select_related("workflow").first()
        )
        if launch is None:
            raise Http404()
        if launch.status != ScheduledLaunch.Status.PENDING:
            raise serializers.ValidationError(
                {"status": "Ce lancement n'est plus en attente : il ne peut plus être annulé."}
            )
        launch.status = ScheduledLaunch.Status.CANCELLED
        launch.note = f"Annulé par {request.user.username}."
        launch.save(update_fields=["status", "note"])
        record(
            "scheduled_launch.cancel", actor=request.user, workspace=ws, target=launch, workflow=launch.workflow.slug
        )
        return Response(status=204)
