"""Public forms: management (people who may publish) and the public page (anyone with the address)."""

from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
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
from formbuilder import service as formservice
from runs import engine, http_node
from runs.serializers import validate_inputs_against
from tenancy.models import Environment, Membership
from tenancy.permissions import PUBLISH, ROLE_PERMISSIONS, RUN, VIEW, get_workspace
from workflows import access
from workflows.models import Workflow

from . import formengine, lookups
from .models import FormLookup, FormSubmission, PublicForm

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
            "library_form",
            "library_version",
            "published",
            "workflow",
            "environment",
            "url",
            "submissions_last_hour",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "uses"]

    state = serializers.SerializerMethodField()
    published = serializers.SerializerMethodField()
    library_form = serializers.SerializerMethodField()
    library_version = serializers.SerializerMethodField()

    def get_library_form(self, obj) -> str | None:
        return obj.library_version.form.slug if obj.library_version else None

    def get_library_version(self, obj) -> str | None:
        return obj.library_version.version if obj.library_version else None

    def get_published(self, obj) -> bool:
        return obj.published_definition is not None

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
        if hasattr(obj, "n_last_hour"):
            return obj.n_last_hour
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
        rows = (
            PublicForm.objects.filter(workspace=ws)
            .select_related("workflow", "environment", "library_version__form")
            .annotate(
                n_last_hour=Count(
                    "submissions", filter=Q(submissions__created_at__gte=timezone.now() - timedelta(hours=1))
                )
            )
            .order_by("name")
        )  # AR1.6 : 756 requêtes pour 287 formulaires avant (version, formulaire de bibliothèque et envois lus ligne par ligne)
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

    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        form = PublicForm.objects.filter(workspace=ws, pk=pk).select_related("workflow", "environment").first()
        if form is None:
            raise Http404()
        access.require(request.user, ws, form.workflow.folder, VIEW)
        return Response(FormSerializer(form).data)

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


class FormAttachView(FormDetailView):
    """Attaches a version of a library form to the public form (the latest published one when none is named): the page
    then serves exactly that version, checked against the workflow's current inputs. DELETE detaches it."""

    http_method_names = ["post", "delete", "options"]  # not the detail view's get/patch

    def post(self, request, workspace, pk):
        ws, form = self._get(request, workspace, pk)
        version = form.workflow.latest_version()
        if version is None:
            raise serializers.ValidationError({"workflow": "Ce workflow n'a pas de version publiée."})
        try:
            lib, lib_version = formservice.resolve(
                ws, {"form": request.data.get("form"), "version": request.data.get("version")}
            )
        except formservice.FormRefError as exc:
            raise serializers.ValidationError({"form": str(exc)}) from None
        access.require(request.user, ws, lib.folder, VIEW)
        definition = lib_version.definition
        mapping = request.data.get("input_map")
        if mapping is None:  # by default the correspondence the workflow itself chose for this same form
            pinned = (version.graph or {}).get("input_form") or {}
            mapping = pinned.get("map") if (pinned.get("ref") or {}).get("form") == lib.slug else None
        if mapping is not None and not formengine._valid_map(mapping):
            raise serializers.ValidationError({"input_map": "Correspondance des entrées invalide."})
        problems = formengine.check_against_inputs(definition, version.input_schema, mapping)
        if not problems:
            for key, src in formengine.iter_sources(definition):
                name = src.get("connection", "")
                if not http_node.find_connection(ws.pk, form.environment_id, name):
                    problems[key] = f"Connexion « {name} » introuvable pour cet environnement (type « Service HTTP »)."
        if problems:
            raise serializers.ValidationError(problems)
        form.library_version = lib_version
        form.published_definition = definition
        form.input_map = mapping or {}
        form.save(update_fields=["library_version", "published_definition", "input_map"])
        record("form.attach", actor=request.user, workspace=ws, target=form, form=lib.slug, version=lib_version.version)
        return Response(FormSerializer(form).data)

    def delete(self, request, workspace, pk):
        """The public page goes back to the workflow's own inputs."""
        ws, form = self._get(request, workspace, pk)
        form.library_version = None
        form.published_definition = None
        form.input_map = {}
        form.save(update_fields=["library_version", "published_definition", "input_map"])
        record("form.detach", actor=request.user, workspace=ws, target=form)
        return Response(FormSerializer(form).data)


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
        if form.published_definition is not None:
            return Response(
                {
                    "title": form.title,
                    "description": form.description,
                    "submit_label": form.submit_label,
                    "definition": form.published_definition,
                }
            )
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
        if form.published_definition is not None:
            values, problems = formengine.validate_submission(form.published_definition, values)
            if problems:
                raise serializers.ValidationError(problems)
            values = formengine.map_inputs(form.published_definition, version.input_schema, form.input_map, values)
        problems = validate_inputs_against(version.input_schema, values)
        if problems:
            if form.published_definition is not None and form.input_map:
                back = formengine.input_fields(form.published_definition, version.input_schema, form.input_map)
                problems = {back.get(k, k): v for k, v in problems.items()}
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


def _kind(request) -> str:
    """What is asked: the options of a list (default) or the pre-filled value of a custom control."""
    kind = request.data.get("kind", "options") if isinstance(request.data, dict) else "options"
    if kind not in lookups.KINDS:
        raise Http404()
    return kind


def _done(row) -> dict:
    return (
        {"state": "done", "value": row.options}
        if row.kind == "prefill"
        else {"state": "done", "options": row.options or []}
    )


class PublicLookupView(APIView):
    """POST: asks for the options of a dynamic-list field (queued for the worker, answered 202 with a job to follow).
    GET: the outcome. 404 for an unknown form, field or job: nothing says which."""

    authentication_classes = [_NoAuth]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "form_lookup"

    def post(self, request, token, field, job=None):
        form = _open_form(token)
        kind = _kind(request)
        source = None if job is not None else lookups.find_source(form, field, kind)
        if source is None:
            raise Http404()
        raw = request.data.get("params") if isinstance(request.data, dict) else None
        job = lookups.enqueue(form, field, lookups.clean_params(raw, source.get("params", [])), kind)
        if job is None:
            raise Throttled(detail="Trop de recherches en cours : réessayez dans un instant.")
        return Response({"id": str(job.pk)}, status=202)

    def get(self, request, token, field, job=None):
        form = _open_form(token)
        row = FormLookup.objects.filter(pk=job, form=form, field_key=field).first() if job else None
        if row is None:
            raise Http404()
        if row.status == FormLookup.Status.DONE:
            return Response(_done(row))
        if row.status == FormLookup.Status.FAILED:
            return Response({"state": "failed", "message": row.error})
        return Response({"state": "pending"})


class WorkflowLookupView(APIView):
    """Dynamic lists of a workflow's designed input form, for a signed-in person who may launch it (catalog, run page).
    Same mechanism as the public form: queued for the worker, answered 202 with a job to follow."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "form_lookup"

    def _version(self, request, workspace, slug):
        from workflows.catalog_views import catalog_only, catalog_workflows, catalog_workspace

        ws = catalog_workspace(request.user, workspace)
        if catalog_only(request.user, ws):  # « Catalogue » role: only the items its groups are granted
            wf = catalog_workflows(ws, request.user).filter(slug=slug).first()
            if wf is None:
                raise Http404()
        else:
            ws = get_workspace(request.user, workspace, RUN)
            wf = access.workflow_or_404(request.user, ws, slug, RUN)
        version = wf.latest_version()
        if version is None:
            raise Http404()
        return ws, version

    def post(self, request, workspace, slug, field, job=None):
        ws, version = self._version(request, workspace, slug)
        what = _kind(request)
        source = None if job is not None else lookups.source_in((version.graph or {}).get("input_form"), field, what)
        if source is None:
            raise Http404()
        kind = request.data.get("environment", "test") if isinstance(request.data, dict) else "test"
        env = Environment.objects.filter(workspace=ws, kind=kind if kind in ("dev", "test", "prod") else "test").first()
        if env is None:
            raise Http404()
        raw = request.data.get("params") if isinstance(request.data, dict) else None
        job = lookups.enqueue_for_version(
            version, env, request.user, field, lookups.clean_params(raw, source.get("params", [])), what
        )
        if job is None:
            raise Throttled(detail="Trop de recherches en cours : réessayez dans un instant.")
        return Response({"id": str(job.pk)}, status=202)

    def get(self, request, workspace, slug, field, job=None):
        _, version = self._version(request, workspace, slug)
        row = FormLookup.objects.filter(pk=job, workflow_version=version, field_key=field).first() if job else None
        if row is None:
            raise Http404()
        if row.status == FormLookup.Status.DONE:
            return Response(_done(row))
        if row.status == FormLookup.Status.FAILED:
            return Response({"state": "failed", "message": row.error})
        return Response({"state": "pending"})
