import hashlib
import hmac
import json
import secrets
import time
from zoneinfo import ZoneInfo, available_timezones

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.models import record
from runs import engine
from runs.serializers import validate_inputs_against
from tenancy.models import Membership
from tenancy.permissions import PUBLISH, ROLE_PERMISSIONS, RUN, VIEW, get_workspace
from workflows.models import Workflow

from . import cron
from .models import Schedule, Webhook, WebhookDelivery

TIMESTAMP_WINDOW = 300  # seconds
MAX_BODY = 256 * 1024


def _workflow(ws, slug):
    wf = Workflow.objects.filter(workspace=ws, slug=slug).first()
    if wf is None:
        raise Http404()
    return wf


def _env(ws, kind):
    from tenancy.models import Environment

    env = Environment.objects.filter(workspace=ws, kind=kind).first()
    if env is None:
        raise serializers.ValidationError({"environment": "Environnement indisponible dans cet espace."})
    return env


class ScheduleSerializer(serializers.ModelSerializer):
    workflow = serializers.SlugRelatedField(slug_field="slug", queryset=Workflow.objects.none())
    environment = serializers.CharField(source="environment.kind", read_only=True)
    next_runs = serializers.SerializerMethodField()

    class Meta:
        model = Schedule
        fields = [
            "id",
            "name",
            "workflow",
            "environment",
            "cron",
            "timezone",
            "inputs",
            "enabled",
            "overlap_policy",
            "missed_policy",
            "grace_seconds",
            "next_run_at",
            "last_run_at",
            "last_note",
            "next_runs",
        ]
        read_only_fields = ["next_run_at", "last_run_at", "last_note"]

    def get_next_runs(self, obj) -> list[str]:
        return [d.isoformat() for d in cron.upcoming(obj.cron, obj.timezone, 3)] if obj.enabled else []

    def validate_cron(self, value):
        try:
            cron.parse(value)
        except cron.CronError as exc:
            raise serializers.ValidationError(str(exc)) from None
        return value

    def validate_timezone(self, value):
        if value not in available_timezones():
            raise serializers.ValidationError("Fuseau horaire inconnu (ex. Europe/Paris, UTC).")
        return value


class ScheduleListCreateView(APIView):
    serializer_class = ScheduleSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = Schedule.objects.filter(workspace=ws).select_related("workflow", "environment").order_by("name")
        return Response({"results": ScheduleSerializer(rows, many=True).data})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = ScheduleSerializer(data=request.data)
        ser.fields["workflow"].queryset = Workflow.objects.filter(workspace=ws)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        env = _env(ws, request.data.get("environment", "test"))
        wf = d.pop("workflow")
        version = wf.latest_version()
        if version is None:
            raise serializers.ValidationError({"workflow": "Ce workflow n'a pas de version publiée."})
        problems = validate_inputs_against(version.input_schema, d.get("inputs", {}))
        if problems:
            raise serializers.ValidationError({"inputs": problems})
        sch = Schedule(workspace=ws, workflow=wf, environment=env, created_by=request.user, **d)
        sch.next_run_at = cron.next_fire(sch.cron, timezone.now(), sch.timezone)
        sch.save()
        record("schedule.create", actor=request.user, workspace=ws, target=sch, cron=sch.cron, timezone=sch.timezone)
        return Response(ScheduleSerializer(sch).data, status=201)


class ScheduleDetailView(APIView):
    def _get(self, request, workspace, pk, perm):
        ws = get_workspace(request.user, workspace, perm)
        sch = Schedule.objects.filter(workspace=ws, pk=pk).first()
        if sch is None:
            raise Http404()
        return ws, sch

    def patch(self, request, workspace, pk):
        ws, sch = self._get(request, workspace, pk, PUBLISH)
        if "enabled" in request.data:
            sch.enabled = bool(request.data["enabled"])
            if sch.enabled:
                sch.next_run_at = cron.next_fire(sch.cron, timezone.now(), sch.timezone)
                sch.created_by = request.user  # re-enabling transfers ownership: runs use the enabler's rights
            sch.save()
            record("schedule.toggle", actor=request.user, workspace=ws, target=sch, enabled=sch.enabled)
        return Response(ScheduleSerializer(sch).data)

    def delete(self, request, workspace, pk):
        ws, sch = self._get(request, workspace, pk, PUBLISH)
        record("schedule.delete", actor=request.user, workspace=ws, target=sch)
        sch.delete()
        return Response(status=204)


class CronPreviewView(APIView):
    def post(self, request, workspace):
        get_workspace(request.user, workspace, VIEW)
        expr, tz = str(request.data.get("cron", "")), str(request.data.get("timezone", "Europe/Paris"))
        try:
            if tz not in available_timezones():
                raise cron.CronError("Fuseau horaire inconnu.")
            runs = cron.upcoming(expr, tz, 5)
        except cron.CronError as exc:
            raise serializers.ValidationError({"cron": str(exc)}) from None
        return Response(
            {
                "next": [d.isoformat() for d in runs],
                "local": [d.astimezone(ZoneInfo(tz)).strftime("%d/%m/%Y %H:%M") for d in runs],
            }
        )


# ------------------------------------------------------------------ webhooks
class WebhookSerializer(serializers.ModelSerializer):
    workflow = serializers.SlugRelatedField(slug_field="slug", queryset=Workflow.objects.none())
    environment = serializers.CharField(source="environment.kind", read_only=True)
    url = serializers.SerializerMethodField()

    class Meta:
        model = Webhook
        fields = ["id", "name", "workflow", "environment", "enabled", "url", "created_at"]
        read_only_fields = ["created_at"]

    def get_url(self, obj) -> str:
        return f"/api/v1/hooks/{obj.id}/"


class WebhookListCreateView(APIView):
    serializer_class = WebhookSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = Webhook.objects.filter(workspace=ws).select_related("workflow", "environment")
        return Response({"results": WebhookSerializer(rows, many=True).data})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = WebhookSerializer(data=request.data)
        ser.fields["workflow"].queryset = Workflow.objects.filter(workspace=ws)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        secret = "whsec_" + secrets.token_urlsafe(32)
        hook = Webhook(
            workspace=ws,
            workflow=d["workflow"],
            environment=_env(ws, request.data.get("environment", "test")),
            name=d["name"],
            created_by=request.user,
            enabled=d.get("enabled", True),
        )
        hook.set_secret(secret)
        hook.save()
        record("webhook.create", actor=request.user, workspace=ws, target=hook)
        body = WebhookSerializer(hook).data
        body["secret"] = secret
        body["notice"] = "Ce secret ne sera plus jamais affiché : copiez-le maintenant."
        return Response(body, status=201)


class WebhookDetailView(APIView):
    def _get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, PUBLISH)
        hook = Webhook.objects.filter(workspace=ws, pk=pk).first()
        if hook is None:
            raise Http404()
        return ws, hook

    def delete(self, request, workspace, pk):
        ws, hook = self._get(request, workspace, pk)
        record("webhook.delete", actor=request.user, workspace=ws, target=hook)
        hook.delete()
        return Response(status=204)

    def post(self, request, workspace, pk):  # rotate secret
        ws, hook = self._get(request, workspace, pk)
        secret = "whsec_" + secrets.token_urlsafe(32)
        hook.set_secret(secret)
        hook.save()
        record("webhook.rotate_secret", actor=request.user, workspace=ws, target=hook)
        return Response({"secret": secret, "notice": "Ancien secret invalidé. Copiez celui-ci maintenant."})


class NoAuth(BaseAuthentication):
    """The webhook authenticates itself (HMAC); this only makes failures answer 401 instead of 403."""

    def authenticate(self, request):
        return None

    def authenticate_header(self, request):
        return "HMAC-SHA256"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class WebhookReceiveView(APIView):
    """Public endpoint: authenticated by HMAC signature + time window + replay protection."""

    authentication_classes = [NoAuth]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "webhook"

    def post(self, request, pk):
        body = request.body
        fail = AuthenticationFailed("Signature invalide.")  # same answer for every cause (no oracle)
        if len(body) > MAX_BODY:
            raise serializers.ValidationError({"body": "Corps trop volumineux."})
        hook = (
            Webhook.objects.select_related("workflow", "workspace", "environment", "created_by").filter(pk=pk).first()
        )
        ts = request.headers.get("X-Fluxonata-Timestamp", "")
        sig = request.headers.get("X-Fluxonata-Signature", "").removeprefix("v1=")
        if hook is None or not hook.enabled or hook.workspace.archived_at is not None or not ts.isdigit() or not sig:
            raise fail
        if abs(time.time() - int(ts)) > TIMESTAMP_WINDOW:
            raise fail
        if not hmac.compare_digest(sign(hook.reveal_secret(), ts, body), sig):
            raise fail
        delivery_id = request.headers.get("X-Fluxonata-Delivery") or sig
        try:
            with transaction.atomic():
                delivery = WebhookDelivery.objects.create(webhook=hook, delivery_id=delivery_id[:128])
        except IntegrityError:
            return Response({"status": "duplicate", "message": "Livraison déjà traitée."}, status=200)
        try:
            inputs = json.loads(body or b"{}")
        except ValueError:
            raise serializers.ValidationError({"body": "JSON invalide."}) from None
        if not isinstance(inputs, dict):
            raise serializers.ValidationError({"body": "Un objet JSON est attendu."})
        owner = hook.created_by
        m = Membership.objects.filter(user=owner, workspace=hook.workspace).first() if owner else None
        if m is None or RUN not in ROLE_PERMISSIONS[m.role]:
            raise AuthenticationFailed("Le propriétaire du webhook n'a plus le droit de lancer ce workflow.")
        version = hook.workflow.latest_version()
        if version is None:
            raise serializers.ValidationError({"workflow": "Aucune version publiée."})
        problems = validate_inputs_against(version.input_schema, inputs)
        if problems:
            raise serializers.ValidationError(problems)
        run, _ = engine.start_run(
            workspace=hook.workspace,
            environment=hook.environment,
            workflow_version=version,
            user=owner,
            inputs=inputs,
            idempotency_key=f"hook-{hook.pk}-{delivery_id[:100]}",
            trigger="webhook",
        )
        delivery.run = run
        delivery.save(update_fields=["run"])
        return Response(
            {"run_id": str(run.id), "url": f"/api/v1/workspaces/{hook.workspace.slug}/workflow-runs/{run.id}/"},
            status=202,
        )
