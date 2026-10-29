"""Public forms: management (people who may publish) and the public page (anyone with the address)."""

from datetime import timedelta

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.models import record
from runs import engine
from runs.serializers import validate_inputs_against
from tenancy.models import Environment, Membership
from tenancy.permissions import PUBLISH, ROLE_PERMISSIONS, RUN, VIEW, get_workspace
from workflows import access
from workflows.models import Workflow

from .models import FormSubmission, PublicForm

MAX_BODY = 64 * 1024


class FormSerializer(serializers.ModelSerializer):
    workflow = serializers.SlugRelatedField(slug_field="slug", queryset=Workflow.objects.none())
    environment = serializers.CharField(source="environment.kind", read_only=True)
    url = serializers.SerializerMethodField()
    submissions_last_hour = serializers.SerializerMethodField()

    class Meta:
        model = PublicForm
        fields = [
            "id",
            "name",
            "title",
            "description",
            "submit_label",
            "success_message",
            "enabled",
            "max_per_hour",
            "expires_at",
            "max_uses",
            "uses",
            "state",
            "workflow",
            "environment",
            "url",
            "submissions_last_hour",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "uses"]

    state = serializers.SerializerMethodField()

    def get_state(self, obj) -> str:
        """active, disabled (by a person), expired or exhausted (the limits switched it off, or are reached right now)."""
        return form_state(obj)

    def validate_expires_at(self, v):
        if v is not None and v <= timezone.now():
            raise serializers.ValidationError("La date de fin doit être dans le futur.")
        return v

    def validate_max_uses(self, v):
        if v is not None and not 1 <= v <= 1_000_000:
            raise serializers.ValidationError("Entre 1 et 1 000 000 utilisations.")
        return v

    def get_url(self, obj) -> str:
        return f"/f/{obj.token}"

    def get_submissions_last_hour(self, obj) -> int:
        return obj.submissions.filter(created_at__gte=timezone.now() - timedelta(hours=1)).count()

    def validate_max_per_hour(self, v):
        if not 1 <= v <= 1000:
            raise serializers.ValidationError("Entre 1 et 1000 envois par heure.")
        return v


def _due(form, now=None) -> str:
    """Why the form must be off right now: "expired", "exhausted" or "" (still available)."""
    now = now or timezone.now()
    if form.expires_at is not None and form.expires_at <= now:
        return "expired"
    if form.max_uses is not None and form.uses >= form.max_uses:
        return "exhausted"
    return ""


def form_state(form) -> str:
    if not form.enabled:
        return form.closed_reason or "disabled"
    return _due(form) or "active"


def close_if_due(form) -> bool:
    """Switches the form off (and says why) when its date has passed or its uses are spent. True when it is now off."""
    reason = _due(form)
    if not reason:
        return False
    if PublicForm.objects.filter(pk=form.pk, enabled=True).update(enabled=False, closed_reason=reason):
        record("form.close", workspace=form.workspace, target=form, reason=reason, uses=form.uses)
    form.enabled, form.closed_reason = False, reason
    return True


def expire_forms() -> int:
    """Periodic sweep (a worker thread): forms whose date has passed are switched off even if nobody opens them."""
    n = 0
    for form in PublicForm.objects.filter(enabled=True).exclude(expires_at__isnull=True, max_uses__isnull=True):
        n += close_if_due(form)
    return n


class FormListCreateView(APIView):
    serializer_class = FormSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = PublicForm.objects.filter(workspace=ws).select_related("workflow", "environment").order_by("name")
        rows = access.visible(rows, request.user, ws, "workflow__folder")
        return Response({"results": FormSerializer(rows, many=True).data})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = FormSerializer(data=request.data)
        ser.fields["workflow"].queryset = access.visible(Workflow.objects.filter(workspace=ws), request.user, ws)
        ser.is_valid(raise_exception=True)
        if "workflow" in ser.validated_data:
            access.require(request.user, ws, ser.validated_data["workflow"].folder, PUBLISH)
        d = ser.validated_data
        env = Environment.objects.filter(workspace=ws, kind=request.data.get("environment", "test")).first()
        if env is None:
            raise serializers.ValidationError({"environment": "Environnement inconnu."})
        if d["workflow"].latest_version() is None:
            raise serializers.ValidationError({"workflow": "Ce workflow n'a pas de version publiée."})
        form = PublicForm.objects.create(workspace=ws, environment=env, created_by=request.user, **d)
        record("form.create", actor=request.user, workspace=ws, target=form, workflow=form.workflow.slug)
        return Response(FormSerializer(form).data, status=201)


class FormDetailView(APIView):
    def _get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, PUBLISH)
        form = PublicForm.objects.filter(workspace=ws, pk=pk).first()
        if form is None:
            raise Http404()
        access.require(request.user, ws, form.workflow.folder, PUBLISH)
        return ws, form

    def patch(self, request, workspace, pk):
        ws, form = self._get(request, workspace, pk)
        ser = FormSerializer(form, data=request.data, partial=True)
        ser.fields["workflow"].queryset = access.visible(Workflow.objects.filter(workspace=ws), request.user, ws)
        ser.is_valid(raise_exception=True)
        if "workflow" in ser.validated_data:
            access.require(request.user, ws, ser.validated_data["workflow"].folder, PUBLISH)
        d = ser.validated_data
        reset = bool(request.data.get("reset_uses"))
        expires = d.get("expires_at", form.expires_at)
        max_uses = d.get("max_uses", form.max_uses)
        uses = 0 if reset else form.uses
        if d.get("enabled", form.enabled) and (
            (expires is not None and expires <= timezone.now()) or (max_uses is not None and uses >= max_uses)
        ):
            raise serializers.ValidationError(
                {
                    "enabled": "Ce formulaire est arrivé à sa date de fin ou à son nombre d'utilisations : "
                    "repoussez la date, augmentez le nombre d'utilisations ou remettez le compteur à zéro avant de le réactiver."
                }
            )
        ser.save()
        if reset:
            form.uses = 0
            form.save(update_fields=["uses"])
        if form.enabled and form.closed_reason:
            form.closed_reason = ""
            form.save(update_fields=["closed_reason"])
        record("form.update", actor=request.user, workspace=ws, target=form, fields=sorted(request.data))
        return Response(FormSerializer(form).data)

    def delete(self, request, workspace, pk):
        ws, form = self._get(request, workspace, pk)
        record("form.delete", actor=request.user, workspace=ws, target=form)
        form.delete()
        return Response(status=204)


class _NoAuth(BaseAuthentication):
    def authenticate(self, request):
        return None


def _open_form(token: str) -> PublicForm:
    form = (
        PublicForm.objects.select_related("workflow", "workspace", "environment", "created_by")
        .filter(token=token, enabled=True)
        .first()
    )
    if form is None or form.workspace.archived_at is not None or close_if_due(form):
        raise Http404()
    return form


class PublicFormView(APIView):
    """GET: what the page shows. POST: a submission. Both answer 404 for an unknown or disabled form."""

    authentication_classes = [_NoAuth]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "form"

    def get(self, request, token):
        form = _open_form(token)
        version = form.workflow.latest_version()
        if version is None:
            raise Http404()
        schema = version.input_schema or {}
        return Response(
            {
                "title": form.title,
                "description": form.description,
                "submit_label": form.submit_label,
                "schema": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
                "form": version.form if hasattr(version, "form") else version.graph.get("form"),
            }
        )

    def post(self, request, token):
        if len(request.body) > MAX_BODY:
            raise serializers.ValidationError({"body": "Envoi trop volumineux."})
        form = _open_form(token)
        values = request.data.get("values", {}) if isinstance(request.data, dict) else {}
        if not isinstance(values, dict):
            raise serializers.ValidationError({"values": "Un objet est attendu."})
        if str(request.data.get("website") or "").strip():  # a person never fills the hidden field: pretend it worked
            return Response({"ok": True, "message": form.success_message})
        since = timezone.now() - timedelta(hours=1)
        if form.submissions.filter(created_at__gte=since).count() >= form.max_per_hour:
            raise Throttled(detail="Ce formulaire reçoit trop de demandes pour le moment : réessayez plus tard.")
        version = form.workflow.latest_version()
        owner = form.created_by
        member = Membership.objects.filter(user=owner, workspace=form.workspace).first() if owner else None
        if version is None or member is None or RUN not in ROLE_PERMISSIONS[member.role]:
            raise AuthenticationFailed("Ce formulaire n'est plus disponible.")
        problems = validate_inputs_against(version.input_schema, values)
        if problems:
            raise serializers.ValidationError(problems)
        with (
            transaction.atomic()
        ):  # the count and the limit are decided under a lock: no submission slips past the last use
            locked = PublicForm.objects.select_for_update().get(pk=form.pk)
            if not locked.enabled or close_if_due(locked):
                raise Http404()
            run, _ = engine.start_run(
                workspace=form.workspace,
                environment=form.environment,
                workflow_version=version,
                user=owner,
                inputs=values,
                trigger="form",
            )
            FormSubmission.objects.create(form=form, run=run)
            locked.uses += 1
            locked.save(update_fields=["uses"])
            close_if_due(locked)  # that was the last allowed use: the form switches itself off now
        return Response({"ok": True, "message": form.success_message}, status=201)
