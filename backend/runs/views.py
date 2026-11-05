from django.http import Http404, HttpResponse
from rest_framework import serializers
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Action
from tenancy.models import Environment
from tenancy.permissions import RUN, VIEW, get_workspace
from workflows import access

from . import services
from .artifacts import SAFE_INLINE_TYPES, fetch
from .models import Artifact, Execution, RunFile, WorkflowRun
from .serializers import ExecutionDetailSerializer, ExecutionSerializer, RunRequestSerializer, validate_inputs_against


class ExecutionListCreateView(ListAPIView):
    serializer_class = ExecutionSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = Execution.objects.filter(workspace=ws, step_run__isnull=True).select_related(
            "action_version__action", "environment", "requested_by", "runner"
        )
        if status := self.request.query_params.get("status"):
            qs = qs.filter(status=status)
        return qs

    def get_serializer_class(self):
        return RunRequestSerializer if self.request.method == "POST" else ExecutionSerializer

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, RUN)
        ser = RunRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        action = Action.objects.filter(workspace=ws, slug=ser.validated_data["action"]).first()
        if action is None or action.latest_version() is None:
            raise Http404()
        env = Environment.objects.filter(workspace=ws, kind=ser.validated_data["environment"]).first()
        if env is None:
            raise serializers.ValidationError({"environment": "Environnement indisponible dans cet espace."})
        version = action.latest_version()
        problems = validate_inputs_against(version.input_schema, ser.validated_data["inputs"])
        if problems:
            raise serializers.ValidationError(problems)
        execution, created = services.create_execution(
            workspace=ws,
            environment=env,
            action_version=version,
            user=request.user,
            inputs=ser.validated_data["inputs"],
            idempotency_key=request.headers.get("Idempotency-Key"),
            connections=ser.validated_data.get("connections"),
        )
        body = ExecutionSerializer(execution).data
        body["url"] = f"/api/v1/workspaces/{ws.slug}/executions/{execution.id}/"
        return Response(body, status=202 if created else 200)


class ExecutionDetailView(RetrieveAPIView):
    serializer_class = ExecutionDetailSerializer

    def get_object(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        obj = (
            Execution.objects.filter(workspace=ws, pk=self.kwargs["pk"])
            .select_related("action_version__action", "environment", "requested_by", "runner", "step_run__run")
            .first()
        )
        if obj is None:
            raise Http404()
        if obj.step_run is not None:  # an action run by a workflow follows the access of that workflow's folder
            access.require_run(self.request.user, ws, obj.step_run.run)
        return obj


class ArtifactSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = ["id", "name", "content_type", "size", "sha256", "created_at", "download_url"]

    def get_download_url(self, obj) -> str:
        return f"/api/v1/workspaces/{obj.workspace.slug}/artifacts/{obj.id}/download/"


class ExecutionArtifactsView(ListAPIView):
    serializer_class = ArtifactSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        ex = Execution.objects.filter(workspace=ws, pk=self.kwargs["pk"]).select_related("step_run__run").first()
        if ex is not None and ex.step_run is not None:
            access.require_run(self.request.user, ws, ex.step_run.run)
        return Artifact.objects.filter(workspace=ws, execution_id=self.kwargs["pk"]).select_related("workspace")


class RunArtifactsView(ListAPIView):
    serializer_class = ArtifactSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        access.run_or_404(self.request.user, ws, WorkflowRun.objects.filter(workspace=ws, pk=self.kwargs["pk"]))
        return Artifact.objects.filter(workspace=ws, execution__step_run__run_id=self.kwargs["pk"]).select_related(
            "workspace"
        )


class ArtifactDownloadView(APIView):
    """The artifact must belong to the workspace of the URL *and* the caller must be a member of it."""

    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        art = Artifact.objects.filter(workspace=ws, pk=pk).select_related("execution__step_run__run").first()
        if art is None:
            raise Http404()
        if art.execution is not None and art.execution.step_run is not None:  # produced inside a workflow run
            access.require_run(request.user, ws, art.execution.step_run.run)
        data = fetch(art.storage_key)
        ctype = art.content_type if art.content_type in SAFE_INLINE_TYPES else "application/octet-stream"
        resp = HttpResponse(data, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="{art.name}"'
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return resp


class RunFilesView(APIView):
    """The files of a run (names, sizes, hashes — never the bytes), for the run page."""

    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        access.run_or_404(request.user, ws, WorkflowRun.objects.filter(workspace=ws, pk=pk))
        rows = RunFile.objects.filter(workspace=ws, run_id=pk).defer("data")
        return Response(
            [
                {
                    "id": str(f.pk),
                    "step_id": f.step_id,
                    "name": f.name,
                    "size": f.size,
                    "content_type": f.content_type,
                    "sha256": f.sha256,
                }
                for f in rows
            ]
        )


class RunFileDownloadView(APIView):
    """The file must belong to the workspace of the URL *and* the caller must be a member of it. Never rendered inline."""

    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        f = RunFile.objects.filter(workspace=ws, pk=pk).select_related("run").first()
        if f is None:
            raise Http404()
        access.require_run(request.user, ws, f.run)
        resp = HttpResponse(bytes(f.data), content_type="application/octet-stream")
        resp["Content-Disposition"] = f'attachment; filename="{f.name}"'
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return resp
