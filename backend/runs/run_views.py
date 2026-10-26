"""API for workflow runs, approvals and operator decisions."""

from django.http import Http404
from rest_framework import serializers
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from core import netsec
from tenancy.models import Environment
from tenancy.permissions import RUN, VIEW, get_workspace
from workflows.models import Workflow

from . import engine
from .models import Approval, StepRun, WorkflowRun
from .serializers import validate_inputs_against, with_defaults


class StepSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display")
    name = serializers.SerializerMethodField()
    approval_id = serializers.SerializerMethodField()
    execution_id = serializers.SerializerMethodField()
    child_run_ids = serializers.SerializerMethodField()
    logs = serializers.SerializerMethodField()

    class Meta:
        model = StepRun
        fields = [
            "node_id",
            "name",
            "node_type",
            "status",
            "status_label",
            "attempt",
            "output",
            "error",
            "available_at",
            "started_at",
            "finished_at",
            "approval_id",
            "execution_id",
            "child_run_ids",
            "logs",
        ]

    def get_name(self, obj) -> str:
        node = self.context["nodes"].get(obj.node_id) or (
            self.context["nodes"].get(obj.node_id[5:]) if obj.node_id.startswith("comp_") else None
        )
        label = (node or {}).get("name", obj.node_id)
        return f"Compensation : {label}" if obj.node_type == "compensation" else label

    def get_approval_id(self, obj) -> int | None:
        return obj.approval.pk if hasattr(obj, "approval") else None

    def get_execution_id(self, obj) -> str | None:
        ex = obj.executions.order_by("-created_at").first()
        return str(ex.id) if ex else None

    def get_logs(self, obj) -> str:
        """Logs of the latest execution of the step (already masked by the worker), capped."""
        ex = obj.executions.order_by("-created_at").first()
        if ex is None:
            return ""
        return "\n".join(a.logs for a in ex.attempts.order_by("number") if a.logs)[-20000:]

    def get_child_run_ids(self, obj) -> list[str]:
        return [str(i) for i in obj.child_runs.order_by("child_index").values_list("id", flat=True)]


class RunEventSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    type = serializers.CharField()
    node_id = serializers.CharField()
    message = serializers.CharField()
    created_at = serializers.DateTimeField()


class RunSerializer(serializers.ModelSerializer):
    workflow = serializers.SerializerMethodField()
    environment = serializers.CharField(source="environment.kind")
    requested_by = serializers.CharField(source="requested_by.username", default=None)
    status_label = serializers.CharField(source="get_status_display")
    needs_decision = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowRun
        fields = [
            "id",
            "workflow",
            "environment",
            "requested_by",
            "status",
            "status_label",
            "inputs",
            "outputs",
            "error",
            "created_at",
            "finished_at",
            "needs_decision",
            "cancel_requested",
            "parent_step_id",
        ]

    def get_workflow(self, obj) -> dict:
        w = obj.definition["workflow"]
        return {"slug": w["slug"], "version": w["version"], "name": obj.workflow_version.workflow.name}

    def get_needs_decision(self, obj) -> bool:
        return obj.steps.filter(status=StepRun.Status.UNCERTAIN).exists()


class RunDetailSerializer(RunSerializer):
    steps = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    graph = serializers.SerializerMethodField()
    runtime = serializers.SerializerMethodField()

    class Meta(RunSerializer.Meta):
        fields = RunSerializer.Meta.fields + ["steps", "events", "graph", "runtime"]

    def _nodes(self, obj):
        return {n["id"]: n for n in obj.definition["graph"]["nodes"]}

    def get_steps(self, obj):
        qs = obj.steps.order_by("started_at", "id")
        return StepSerializer(qs, many=True, context={"nodes": self._nodes(obj)}).data

    def get_events(self, obj):
        return RunEventSerializer(obj.events.all(), many=True).data

    def get_graph(self, obj) -> dict:
        g = obj.definition["graph"]
        return {
            "nodes": [
                {"id": n["id"], "name": n.get("name", n["id"]), "type": n["type"], "ui": n.get("ui")}
                for n in g["nodes"]
            ],
            "edges": g["edges"],
            "notes": g.get("notes", []),
        }

    def get_runtime(self, obj) -> dict:
        return obj.definition.get("runtime", {})


class StartSerializer(serializers.Serializer):
    environment = serializers.ChoiceField(choices=["dev", "test", "prod"])
    inputs = serializers.JSONField(default=dict)
    version = serializers.CharField(required=False)
    callback_url = serializers.URLField(required=False, max_length=500)


class WorkflowRunListView(ListAPIView):
    serializer_class = RunSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = WorkflowRun.objects.filter(workspace=ws, parent_step__isnull=True).select_related(
            "environment", "requested_by", "workflow_version__workflow"
        )
        if status := self.request.query_params.get("status"):
            qs = qs.filter(status=status)
        if workflow := self.request.query_params.get("workflow"):
            qs = qs.filter(workflow_version__workflow__slug=workflow)
        return qs


class WorkflowRunDetailView(RetrieveAPIView):
    serializer_class = RunDetailSerializer

    def get_object(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        obj = (
            WorkflowRun.objects.filter(workspace=ws, pk=self.kwargs["pk"])
            .select_related("environment", "requested_by", "workflow_version__workflow")
            .first()
        )
        if obj is None:
            raise Http404()
        return obj


class StartWorkflowRunView(APIView):
    serializer_class = StartSerializer

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, RUN)
        ser = StartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        wf = Workflow.objects.filter(workspace=ws, slug=slug).first()
        if wf is None:
            raise Http404()
        version = (
            wf.versions.filter(version=ser.validated_data["version"]).first()
            if ser.validated_data.get("version")
            else wf.latest_version()
        )
        if version is None:
            raise serializers.ValidationError({"version": "Ce workflow n'a pas de version publiée."})
        env = Environment.objects.filter(workspace=ws, kind=ser.validated_data["environment"]).first()
        if env is None:
            raise serializers.ValidationError({"environment": "Environnement indisponible dans cet espace."})
        inputs = ser.validated_data["inputs"]
        if not isinstance(inputs, dict):
            raise serializers.ValidationError({"inputs": "Les entrées doivent être un objet."})
        inputs = with_defaults(version.input_schema, inputs)
        problems = validate_inputs_against(version.input_schema, inputs)
        if problems:
            raise serializers.ValidationError(problems)
        callback_url = ser.validated_data.get("callback_url", "")
        if callback_url:
            try:
                netsec.validate_url(callback_url)
            except netsec.UnsafeURL as exc:
                raise serializers.ValidationError({"callback_url": str(exc)}) from None
        run, created = engine.start_run(
            workspace=ws,
            environment=env,
            workflow_version=version,
            user=request.user,
            inputs=inputs,
            idempotency_key=request.headers.get("Idempotency-Key"),
            callback_url=callback_url,
        )
        body = RunSerializer(run).data
        body["url"] = f"/api/v1/workspaces/{ws.slug}/workflow-runs/{run.id}/"
        return Response(body, status=202 if created else 200)


class CancelRunView(APIView):
    def post(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, RUN)
        if not WorkflowRun.objects.filter(workspace=ws, pk=pk).exists():
            raise Http404()
        run = engine.request_run_cancel(pk, request.user)
        return Response(RunSerializer(run).data, status=202)


class DecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["retry", "succeed", "fail"])
    output = serializers.JSONField(required=False)


class StepDecisionView(APIView):
    serializer_class = DecisionSerializer

    def post(self, request, workspace, pk, node):
        ws = get_workspace(request.user, workspace, RUN)
        sr = StepRun.objects.filter(run__workspace=ws, run_id=pk, node_id=node).first()
        if sr is None:
            raise Http404()
        ser = DecisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        engine.decide_uncertain(sr.pk, request.user, ser.validated_data["decision"], ser.validated_data.get("output"))
        return Response(status=204)


class ApprovalSerializer(serializers.ModelSerializer):
    requested_by = serializers.CharField(source="requested_by.username", default=None)
    decided_by = serializers.CharField(source="decided_by.username", default=None)
    run_id = serializers.UUIDField(source="step_run.run_id")
    workflow = serializers.SerializerMethodField()
    context = serializers.SerializerMethodField()
    can_decide = serializers.SerializerMethodField()

    class Meta:
        model = Approval
        fields = [
            "id",
            "title",
            "instructions",
            "status",
            "requested_by",
            "decided_by",
            "comment",
            "created_at",
            "decided_at",
            "run_id",
            "workflow",
            "context",
            "can_decide",
        ]

    def get_workflow(self, obj) -> str:
        return obj.step_run.run.workflow_version.workflow.name

    def get_context(self, obj) -> dict:
        return obj.step_run.run.inputs

    def get_can_decide(self, obj) -> bool:
        u = self.context["request"].user
        run = obj.step_run.run
        prod_ban = run.environment.kind == "prod" and (run.definition.get("governance") or {}).get("rules", {}).get(
            "forbid_self_approval_in_prod", True
        )
        return obj.status == "pending" and ((obj.allow_self_approval and not prod_ban) or obj.requested_by_id != u.id)


class ApprovalListView(ListAPIView):
    serializer_class = ApprovalSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = (
            Approval.objects.filter(workspace=ws)
            .select_related("requested_by", "decided_by", "step_run__run__workflow_version__workflow")
            .order_by("-created_at")
        )
        return qs.filter(status=self.request.query_params["status"]) if "status" in self.request.query_params else qs


class ApprovalDecideSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    comment = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class ApprovalDecideView(APIView):
    serializer_class = ApprovalDecideSerializer

    def post(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        if not Approval.objects.filter(workspace=ws, pk=pk).exists():
            raise Http404()
        ser = ApprovalDecideSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ap = engine.decide_approval(pk, request.user, ser.validated_data["approve"], ser.validated_data["comment"])
        return Response(ApprovalSerializer(ap, context={"request": request}).data)


class RunEventsView(APIView):
    """Ordered event stream with catch-up: `?after=<seq>` returns only newer events (a reconnecting client never misses any)."""

    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        run = WorkflowRun.objects.filter(workspace=ws, pk=pk).first()
        if run is None:
            raise Http404()
        try:
            after = int(request.query_params.get("after", 0))
        except ValueError:
            raise serializers.ValidationError({"after": "Entier attendu."}) from None
        events = run.events.filter(seq__gt=after).order_by("seq")[:500]
        data = RunEventSerializer(events, many=True).data
        return Response({"status": run.status, "last_seq": data[-1]["seq"] if data else after, "results": data})
