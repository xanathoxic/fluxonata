from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core import netsec
from tenancy.permissions import ADMINISTER, VIEW, get_workspace
from workflows.models import Workflow

from . import stats
from .models import AlertDelivery, AlertRule


class DashboardView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        days = request.query_params.get("days", "7")
        days = int(days) if days.isdigit() and 1 <= int(days) <= 90 else 7
        return Response(stats.dashboard(ws, days, request.query_params.get("environment", ""), request.user))


class RetentionView(APIView):
    """Finished runs older than `days` are deleted by the worker (null: keep everything). Audit events are not touched."""

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        return Response({"retention_days": ws.retention_days})

    def put(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        days = request.data.get("retention_days")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650):
            raise serializers.ValidationError(
                {"retention_days": "Un nombre de jours entre 1 et 3650, ou vide pour tout conserver."}
            )
        ws.retention_days = days
        ws.save(update_fields=["retention_days"])
        record("workspace.retention", actor=request.user, workspace=ws, days=days)
        return Response({"retention_days": days})


class AlertSerializer(serializers.ModelSerializer):
    workflow = serializers.SlugRelatedField(
        slug_field="slug", queryset=Workflow.objects.none(), required=False, allow_null=True
    )
    url = serializers.CharField(write_only=True, required=False, max_length=500)
    destination = serializers.CharField(source="url_host", read_only=True)

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "name",
            "event",
            "enabled",
            "environment",
            "workflow",
            "threshold_percent",
            "min_runs",
            "window_minutes",
            "cooldown_minutes",
            "format",
            "url",
            "destination",
            "last_fired_at",
            "created_at",
        ]
        read_only_fields = ["id", "last_fired_at", "created_at"]

    def validate_environment(self, v):
        if v not in ("", "dev", "test", "prod"):
            raise serializers.ValidationError("dev, test, prod ou vide.")
        return v

    def validate_format(self, v):
        if v not in ("slack", "json"):
            raise serializers.ValidationError("slack ou json.")
        return v

    def validate_threshold_percent(self, v):
        if not 1 <= v <= 100:
            raise serializers.ValidationError("Entre 1 et 100.")
        return v


def _check_url(url: str) -> str:
    try:
        return netsec.validate_url(url).host
    except netsec.UnsafeURL as exc:
        raise serializers.ValidationError({"url": str(exc)}) from None


class AlertListCreateView(APIView):
    serializer_class = AlertSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        return Response(
            {"results": AlertSerializer(AlertRule.objects.filter(workspace=ws).order_by("name"), many=True).data}
        )

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = AlertSerializer(data=request.data)
        ser.fields["workflow"].queryset = Workflow.objects.filter(workspace=ws)
        ser.is_valid(raise_exception=True)
        d = dict(ser.validated_data)
        url = d.pop("url", "")
        if not url:
            raise serializers.ValidationError({"url": "Adresse de destination requise."})
        rule = AlertRule(workspace=ws, created_by=request.user, url_host=_check_url(url), **d)
        rule.set_url(url)
        rule.save()
        record("alert_rule.create", actor=request.user, workspace=ws, target=rule, trigger_event=rule.event)
        return Response(AlertSerializer(rule).data, status=201)


class AlertDetailView(APIView):
    def _get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rule = AlertRule.objects.filter(workspace=ws, pk=pk).first()
        if rule is None:
            raise Http404()
        return ws, rule

    def patch(self, request, workspace, pk):
        ws, rule = self._get(request, workspace, pk)
        ser = AlertSerializer(rule, data=request.data, partial=True)
        ser.fields["workflow"].queryset = Workflow.objects.filter(workspace=ws)
        ser.is_valid(raise_exception=True)
        url = ser.validated_data.pop("url", None)
        for k, v in ser.validated_data.items():
            setattr(rule, k, v)
        if url:
            rule.url_host = _check_url(url)
            rule.set_url(url)
        rule.save()
        record("alert_rule.update", actor=request.user, workspace=ws, target=rule, fields=sorted(request.data))
        return Response(AlertSerializer(rule).data)

    def delete(self, request, workspace, pk):
        ws, rule = self._get(request, workspace, pk)
        record("alert_rule.delete", actor=request.user, workspace=ws, target=rule)
        rule.delete()
        return Response(status=204)


class AlertTestView(APIView):
    """Queues a test message (sent by the worker, like a real alert)."""

    def post(self, request, workspace, pk):
        from django.utils import timezone

        ws = get_workspace(request.user, workspace, ADMINISTER)
        rule = AlertRule.objects.filter(workspace=ws, pk=pk).first()
        if rule is None:
            raise Http404()
        text = f"✅ Test de l'alerte « {rule.name} »."
        AlertDelivery.objects.create(
            rule=rule,
            payload={"text": text} if rule.format == "slack" else {"event": "test", "text": text},
            next_attempt_at=timezone.now(),
        )
        return Response({"status": "queued"}, status=202)
