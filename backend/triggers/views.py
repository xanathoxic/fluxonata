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
from rest_framework.exceptions import APIException, AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.models import record
from runs import engine
from runs.serializers import validate_inputs_against
from tenancy.models import Membership
from tenancy.permissions import PUBLISH, ROLE_PERMISSIONS, RUN, VIEW, get_workspace
from vault.external import ExternalVaultError
from vault.models import Secret
from workflows import access
from workflows.models import Workflow

from . import cron
from .models import Schedule, Webhook, WebhookDelivery

TIMESTAMP_WINDOW = 300  # seconds
MAX_BODY = 256 * 1024
MAX_RESUME_BODY = 64 * 1024


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
        rows = Schedule.objects.filter(workspace=ws).select_related("workflow", "environment").order_by("name", "id")
        rows = access.visible(rows, request.user, ws, "workflow__folder")
        return Response({"results": ScheduleSerializer(rows, many=True).data})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = ScheduleSerializer(data=request.data)
        ser.fields["workflow"].queryset = access.visible(Workflow.objects.filter(workspace=ws), request.user, ws)
        ser.is_valid(raise_exception=True)
        if "workflow" in ser.validated_data:
            access.require(request.user, ws, ser.validated_data["workflow"].folder, PUBLISH)
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
        access.require(request.user, ws, sch.workflow.folder, PUBLISH)
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
    signing_secret = serializers.SerializerMethodField()

    class Meta:
        model = Webhook
        fields = [
            "id",
            "name",
            "workflow",
            "environment",
            "enabled",
            "provider",
            "events",
            "url",
            "created_at",
            "signing_secret",
        ]
        read_only_fields = ["created_at"]

    def get_url(self, obj) -> str:
        return f"/api/v1/hooks/{obj.id}/"

    def get_signing_secret(self, obj) -> dict | None:
        """Where the signing secret lives when it is not the one Fluxonata generated: the name and provider of the secret."""
        s = obj.external_secret
        return {"name": s.name, "provider": s.provider} if s else None

    def validate_provider(self, v):
        if v not in PROVIDERS:
            raise serializers.ValidationError(f"Fournisseur : {', '.join(PROVIDERS)}.")
        return v

    def validate_events(self, v):
        if not isinstance(v, list) or len(v) > 30 or not all(isinstance(x, str) and 0 < len(x) <= 80 for x in v):
            raise serializers.ValidationError("Liste de 30 noms d'événements au plus.")
        return v


PROVIDERS = ("fluxonata", "github", "gitlab", "jira", "token")


def provider_event(provider: str, headers, payload) -> str:
    if provider == "github":
        return headers.get("X-GitHub-Event", "")
    if provider == "gitlab":
        return headers.get("X-Gitlab-Event", "")
    if provider == "jira":
        return str(payload.get("webhookEvent", "")) if isinstance(payload, dict) else ""
    return headers.get("X-Event", "")


def provider_delivery(provider: str, headers, body: bytes) -> str:
    key = {"github": "X-GitHub-Delivery", "gitlab": "X-Gitlab-Event-UUID"}.get(provider, "")
    return headers.get(key, "") or hashlib.sha256(body).hexdigest()


def verify_provider(provider: str, secret: str, headers, body: bytes) -> bool:
    """Each service proves the call in its own way; every comparison is constant-time."""
    if provider == "github":
        got = headers.get("X-Hub-Signature-256", "").removeprefix("sha256=")
        return hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), got)
    if provider == "jira":
        got = headers.get("X-Hub-Signature", "").removeprefix("sha256=")
        return hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), got)
    if provider == "gitlab":
        return hmac.compare_digest(secret, headers.get("X-Gitlab-Token", ""))
    if provider == "token":
        return hmac.compare_digest(f"Bearer {secret}", headers.get("Authorization", ""))
    return False


class WebhookListCreateView(APIView):
    serializer_class = WebhookSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = Webhook.objects.filter(workspace=ws).select_related("workflow", "environment")
        rows = access.visible(rows, request.user, ws, "workflow__folder")
        return Response({"results": WebhookSerializer(rows, many=True).data})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = WebhookSerializer(data=request.data)
        ser.fields["workflow"].queryset = access.visible(Workflow.objects.filter(workspace=ws), request.user, ws)
        ser.is_valid(raise_exception=True)
        if "workflow" in ser.validated_data:
            access.require(request.user, ws, ser.validated_data["workflow"].folder, PUBLISH)
        d = ser.validated_data
        existing = None
        if request.data.get("secret"):  # signed with a secret of the workspace (e.g. one held by the external vault)
            existing = Secret.objects.filter(workspace=ws, name=str(request.data["secret"])).first()
            if existing is None:
                raise serializers.ValidationError({"secret": "Secret inconnu dans cet espace."})
        secret = "whsec_" + secrets.token_urlsafe(32)
        hook = Webhook(
            workspace=ws,
            external_secret=existing,
            workflow=d["workflow"],
            environment=_env(ws, request.data.get("environment", "test")),
            name=d["name"],
            created_by=request.user,
            enabled=d.get("enabled", True),
            provider=d.get("provider", "fluxonata"),
            events=d.get("events", []),
        )
        if hook.provider != "fluxonata":
            props = (
                (hook.workflow.latest_version() or hook.workflow).input_schema if hook.workflow.latest_version() else {}
            ).get("properties", {})
            if "payload" not in props:
                raise serializers.ValidationError(
                    {
                        "workflow": "Le workflow doit déclarer une entrée « payload » (objet) pour recevoir le contenu de l'événement."
                    }
                )
        if existing is None:
            hook.set_secret(secret)
        hook.save()
        record(
            "webhook.create",
            actor=request.user,
            workspace=ws,
            target=hook,
            signing_secret=existing.name if existing else "",
        )
        body = WebhookSerializer(hook).data
        if existing is None:
            body["secret"] = secret
            body["notice"] = "Ce secret ne sera plus jamais affiché : copiez-le maintenant."
        else:
            body["notice"] = (
                f"Les appels sont vérifiés avec le secret « {existing.name} » ; Fluxonata ne le génère ni ne l'affiche."
            )
        return Response(body, status=201)


class WebhookDetailView(APIView):
    def _get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, PUBLISH)
        hook = Webhook.objects.filter(workspace=ws, pk=pk).first()
        if hook is None:
            raise Http404()
        access.require(request.user, ws, hook.workflow.folder, PUBLISH)
        return ws, hook

    def delete(self, request, workspace, pk):
        ws, hook = self._get(request, workspace, pk)
        record("webhook.delete", actor=request.user, workspace=ws, target=hook)
        hook.delete()
        return Response(status=204)

    def post(self, request, workspace, pk):  # rotate secret
        ws, hook = self._get(request, workspace, pk)
        if hook.external_secret_id:
            raise serializers.ValidationError(
                {
                    "secret": f"Ce webhook utilise le secret « {hook.external_secret.name} » : remplacez sa valeur dans Administration → Secrets."
                }
            )
        secret = "whsec_" + secrets.token_urlsafe(32)
        hook.set_secret(secret)
        hook.save()
        record("webhook.rotate_secret", actor=request.user, workspace=ws, target=hook)
        return Response({"secret": secret, "notice": "Ancien secret invalidé. Copiez celui-ci maintenant."})


class VaultUnavailable(APIException):
    """The sender cannot be verified because the external vault holding the signing secret does not answer: retry later."""

    status_code = 503
    default_detail = "Coffre externe indisponible : réessayez plus tard."
    default_code = "vault_unavailable"


def _signing_secret(hook) -> str:
    try:
        return hook.reveal_secret()
    except ExternalVaultError:
        raise VaultUnavailable() from None


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
        if hook is not None and hook.provider != "fluxonata":
            return _provider_receive(request, hook, body, fail)
        ts = request.headers.get("X-Fluxonata-Timestamp", "")
        sig = request.headers.get("X-Fluxonata-Signature", "").removeprefix("v1=")
        if hook is None or not hook.enabled or hook.workspace.archived_at is not None or not ts.isdigit() or not sig:
            raise fail
        if abs(time.time() - int(ts)) > TIMESTAMP_WINDOW:
            raise fail
        if not hmac.compare_digest(sign(_signing_secret(hook), ts, body), sig):
            raise fail
        delivery_id = request.headers.get("X-Fluxonata-Delivery") or sig

        def prepare():
            try:
                inputs = json.loads(body or b"{}")
            except ValueError:
                raise serializers.ValidationError({"body": "JSON invalide."}) from None
            if not isinstance(inputs, dict):
                raise serializers.ValidationError({"body": "Un objet JSON est attendu."})
            owner = _check_owner(hook)
            version = hook.workflow.latest_version()
            if version is None:
                raise serializers.ValidationError({"workflow": "Aucune version publiée."})
            problems = validate_inputs_against(version.input_schema, inputs)
            if problems:
                raise serializers.ValidationError(problems)
            return owner, version, inputs

        run, duplicate = _accept_delivery(hook, delivery_id, prepare)
        if duplicate is not None:
            return duplicate
        return Response(
            {"run_id": str(run.id), "url": f"/api/v1/workspaces/{hook.workspace.slug}/workflow-runs/{run.id}/"},
            status=202,
        )


def _check_owner(hook):
    owner = hook.created_by
    m = Membership.objects.filter(user=owner, workspace=hook.workspace).first() if owner else None
    if m is None or RUN not in ROLE_PERMISSIONS[m.role]:
        raise AuthenticationFailed("Le propriétaire du webhook n'a plus le droit de lancer ce workflow.")
    return owner


def _accept_delivery(hook, delivery_id: str, prepare):
    """Enregistre la livraison ET démarre le run dans UNE transaction (AR0.4).

    Avant, la livraison était validée (committée) puis le run créé ensuite : une panne entre les deux faisait répondre « déjà traitée » à la
    nouvelle tentative de l'expéditeur alors qu'aucun run n'existait — événement perdu. Désormais : tout ou rien. Une livraison déjà liée à un run
    est un vrai doublon (on renvoie ce run) ; une livraison SANS run (reste d'une panne d'avant ce correctif) est reprise et menée à son terme.
    Retourne (run, None) ou (None, réponse de doublon)."""
    key = delivery_id[:128]
    with transaction.atomic():
        try:
            with transaction.atomic():  # point de sauvegarde : seule la collision d'identifiant est un « doublon »
                delivery = WebhookDelivery.objects.create(webhook=hook, delivery_id=key)
        except IntegrityError:
            delivery = WebhookDelivery.objects.select_for_update().filter(webhook=hook, delivery_id=key).first()
            if delivery is not None and delivery.run_id is not None:
                return None, Response(
                    {"status": "duplicate", "message": "Livraison déjà traitée.", "run_id": str(delivery.run_id)},
                    status=200,
                )
            if delivery is None:  # supprimée entre-temps (conservation des données) : l'expéditeur réessaiera
                raise
        owner, version, inputs = prepare()
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
    return run, None


def _provider_receive(request, hook, body, fail):
    """A service's own webhook (GitHub, GitLab, Jira, bearer token): its proof, an optional event filter, replay protection.
    The workflow receives `payload` (the body) and, if it declares them, `event` and `delivery`."""
    if not hook.enabled or hook.workspace.archived_at is not None:
        raise fail
    if not verify_provider(hook.provider, _signing_secret(hook), request.headers, body):
        raise fail
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise serializers.ValidationError({"body": "JSON invalide."}) from None
    if not isinstance(payload, dict):
        raise serializers.ValidationError({"body": "Un objet JSON est attendu."})
    event = provider_event(hook.provider, request.headers, payload)
    if hook.events and event not in hook.events:
        return Response({"status": "ignored", "event": event}, status=200)
    delivery_id = provider_delivery(hook.provider, request.headers, body)

    def prepare():
        owner = _check_owner(hook)
        version = hook.workflow.latest_version()
        if version is None:
            raise serializers.ValidationError({"workflow": "Aucune version publiée."})
        declared = (version.input_schema or {}).get("properties", {})
        inputs = {
            k: v for k, v in {"payload": payload, "event": event, "delivery": delivery_id}.items() if k in declared
        }
        problems = validate_inputs_against(version.input_schema, inputs)
        if problems:
            raise serializers.ValidationError(problems)
        return owner, version, inputs

    run, duplicate = _accept_delivery(hook, delivery_id, prepare)
    if duplicate is not None:
        return duplicate
    return Response({"run_id": str(run.id)}, status=202)


def _waiting_step(token: str, kind: str):
    """The waiting « Attendre » step a capability address points to, locked for update, or 404."""
    from runs import resume
    from runs.models import StepRun, WorkflowRun

    step_id = resume.step_id_from(token)
    sr = (
        StepRun.objects.select_for_update().select_related("run").filter(pk=step_id).first()
        if step_id is not None
        else None
    )
    node = next((n for n in sr.run.definition["graph"]["nodes"] if n["id"] == sr.node_id), {}) if sr else {}
    if (
        sr is None
        or sr.node_type != "wait"
        or node.get("resume") != kind
        or sr.run.status != WorkflowRun.Status.RUNNING
        or sr.run.workspace.archived_at is not None
    ):
        raise Http404()
    return sr, node


class ResumeWaitView(APIView):
    """Public capability URL: an « Attendre » step set to resume on an HTTP call receives its payload here.
    The token answers like a missing page when invalid; a step resumes once (a second call gets 409)."""

    authentication_classes = [NoAuth]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "webhook"

    def post(self, request, token):
        from runs.models import StepRun

        if len(request.body) > MAX_RESUME_BODY:
            raise serializers.ValidationError({"body": "Corps trop volumineux."})
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            raise serializers.ValidationError({"body": "JSON invalide."}) from None
        if not isinstance(payload, dict):
            raise serializers.ValidationError({"body": "Un objet JSON est attendu."})
        with transaction.atomic():
            sr, _ = _waiting_step(token, "webhook")
            if sr.status != StepRun.Status.WAITING or sr.resume_payload is not None:
                return Response({"status": "already_resumed"}, status=409)
            sr.resume_payload = payload
            sr.save(update_fields=["resume_payload"])
            engine.wake(sr.run_id)  # le run attendait cet appel (AR4.4)
        return Response({"status": "resumed"}, status=202)


class ResumeFormView(APIView):
    """A waiting step set to resume on a FORM: the public page shows the fields, the submission resumes the run once."""

    authentication_classes = [NoAuth]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "form"

    def get(self, request, token):
        from runs.models import StepRun

        with transaction.atomic():
            sr, node = _waiting_step(token, "form")
            if sr.status != StepRun.Status.WAITING or sr.resume_payload is not None:
                return Response({"status": "already_resumed"}, status=409)
        schema = node.get("form") or {}
        return Response(
            {
                "title": node.get("name", "Reprise"),
                "description": node.get("description", ""),
                "submit_label": node.get("submit_label", "Envoyer"),
                "schema": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
                "form": None,
            }
        )

    def post(self, request, token):
        from runs.models import StepRun

        if len(request.body) > MAX_RESUME_BODY:
            raise serializers.ValidationError({"body": "Envoi trop volumineux."})
        values = request.data.get("values", {}) if isinstance(request.data, dict) else {}
        if not isinstance(values, dict):
            raise serializers.ValidationError({"values": "Un objet est attendu."})
        with transaction.atomic():
            sr, node = _waiting_step(token, "form")
            if sr.status != StepRun.Status.WAITING or sr.resume_payload is not None:
                return Response({"status": "already_resumed"}, status=409)
            schema = node.get("form") or {}
            problems = validate_inputs_against(
                {"type": "object", "properties": schema.get("properties", {}), "required": schema.get("required", [])},
                values,
            )
            if problems:
                raise serializers.ValidationError(problems)
            sr.resume_payload = values
            sr.save(update_fields=["resume_payload"])
            engine.wake(sr.run_id)
        return Response(
            {
                "status": "resumed",
                "message": node.get("success_message", "Merci : votre réponse a été prise en compte."),
            },
            status=202,
        )
