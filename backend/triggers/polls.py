"""Management of polling triggers (people who may publish)."""

from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from runs import http_node
from tenancy.models import Environment
from tenancy.permissions import PUBLISH, VIEW, get_workspace
from workflows import access
from workflows.expressions import resolve
from workflows.models import Workflow

from .models import PollTrigger


class PollSerializer(serializers.ModelSerializer):
    workflow = serializers.SlugRelatedField(slug_field="slug", queryset=Workflow.objects.none())
    environment = serializers.CharField(source="environment.kind", read_only=True)

    class Meta:
        model = PollTrigger
        fields = [
            "id",
            "name",
            "workflow",
            "environment",
            "source",
            "request",
            "interval_seconds",
            "items_path",
            "id_path",
            "mode",
            "fire_existing",
            "enabled",
            "last_poll_at",
            "last_ok",
            "last_note",
            "next_poll_at",
            "created_at",
        ]
        read_only_fields = ["id", "last_poll_at", "last_ok", "last_note", "next_poll_at", "created_at"]

    def validate_interval_seconds(self, v):
        if not 60 <= v <= 86400:
            raise serializers.ValidationError("Entre 60 secondes et 24 heures.")
        return v

    def validate_source(self, v):
        if v not in ("http", "imap", "sftp"):
            raise serializers.ValidationError("Source : http, imap ou sftp.")
        return v

    def validate_request(self, v):
        if not isinstance(v, dict) or not v.get("connection"):
            raise serializers.ValidationError("Décrivez la requête (connexion, et opération ou méthode + chemin).")
        return v


def _check(ws, env, workflow, data):
    """The request must be well-formed and the workflow able to receive `item` / `items`."""
    if data.get("source") == "imap":
        from vault.models import Connection

        if not Connection.objects.filter(workspace=ws, name=data["request"].get("connection"), kind="imap").exists():
            raise serializers.ValidationError({"request": "Connexion e-mail (type « Boîte e-mail IMAP ») introuvable."})
    elif data.get("source") == "sftp":
        from vault.models import Connection

        if not Connection.objects.filter(workspace=ws, name=data["request"].get("connection"), kind="sftp").exists():
            raise serializers.ValidationError({"request": "Connexion « Serveur SFTP » introuvable."})
        if not str(data["request"].get("path") or "").startswith("/"):
            raise serializers.ValidationError(
                {"request": "Indiquez le dossier à surveiller (chemin absolu, ex. /depot)."}
            )
    else:
        _check_http(ws, env, data)
    version = workflow.latest_version()
    if version is None:
        raise serializers.ValidationError({"workflow": "Ce workflow n'a pas de version publiée."})
    _check_inputs(version, data)


def _check_http(ws, env, data):
    try:
        http_node.prepare(
            {**data["request"], "type": "http"}, resolve, {"inputs": {}, "vars": {}, "steps": {}}, ws.pk, env.pk
        )
    except http_node.HttpStepError as exc:
        raise serializers.ValidationError({"request": exc.message}) from None


def _check_inputs(version, data):
    schema = version.input_schema or {}
    key = "items" if data.get("mode") == PollTrigger.Mode.BATCH else "item"
    if key not in (schema.get("properties") or {}):
        raise serializers.ValidationError(
            {"workflow": f"Le workflow doit déclarer une entrée « {key} » pour recevoir les données."}
        )
    others = [r for r in schema.get("required", []) if r != key]
    if others:
        raise serializers.ValidationError(
            {"workflow": f"Entrées obligatoires non fournies par le déclencheur : {', '.join(others)}."}
        )
    if not str(data.get("id_path", "")).strip():
        raise serializers.ValidationError({"id_path": "Indiquez où lire l'identifiant de chaque élément (ex. id)."})


class PollListCreateView(APIView):
    serializer_class = PollSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = PollTrigger.objects.filter(workspace=ws).select_related("workflow", "environment").order_by("name")
        rows = access.visible(rows, request.user, ws, "workflow__folder")
        return Response({"results": PollSerializer(rows, many=True).data})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = PollSerializer(data=request.data)
        ser.fields["workflow"].queryset = access.visible(Workflow.objects.filter(workspace=ws), request.user, ws)
        ser.is_valid(raise_exception=True)
        if "workflow" in ser.validated_data:
            access.require(request.user, ws, ser.validated_data["workflow"].folder, PUBLISH)
        d = ser.validated_data
        env = Environment.objects.filter(workspace=ws, kind=request.data.get("environment", "test")).first()
        if env is None:
            raise serializers.ValidationError({"environment": "Environnement inconnu."})
        _check(ws, env, d["workflow"], d)
        t = PollTrigger.objects.create(
            workspace=ws, environment=env, created_by=request.user, next_poll_at=timezone.now(), **d
        )
        record("poll_trigger.create", actor=request.user, workspace=ws, target=t, workflow=t.workflow.slug)
        return Response(PollSerializer(t).data, status=201)


class PollDetailView(APIView):
    def _get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, PUBLISH)
        t = PollTrigger.objects.filter(workspace=ws, pk=pk).select_related("workflow", "environment").first()
        if t is None:
            raise Http404()
        access.require(request.user, ws, t.workflow.folder, PUBLISH)
        return ws, t

    def patch(self, request, workspace, pk):
        ws, t = self._get(request, workspace, pk)
        ser = PollSerializer(t, data=request.data, partial=True)
        ser.fields["workflow"].queryset = access.visible(Workflow.objects.filter(workspace=ws), request.user, ws)
        ser.is_valid(raise_exception=True)
        if "workflow" in ser.validated_data:
            access.require(request.user, ws, ser.validated_data["workflow"].folder, PUBLISH)
        merged = {
            **{f: getattr(t, f) for f in ("request", "mode", "id_path", "source")},
            **ser.validated_data,
            "workflow": ser.validated_data.get("workflow", t.workflow),
        }
        _check(ws, t.environment, merged["workflow"], merged)
        ser.save()
        record("poll_trigger.update", actor=request.user, workspace=ws, target=t, fields=sorted(request.data))
        return Response(PollSerializer(t).data)

    def delete(self, request, workspace, pk):
        ws, t = self._get(request, workspace, pk)
        record("poll_trigger.delete", actor=request.user, workspace=ws, target=t)
        t.delete()
        return Response(status=204)


class PollNowView(APIView):
    """Asks the worker to poll right away; `preview: true` only reports what would be started (nothing is memorised)."""

    def post(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, PUBLISH)
        t = PollTrigger.objects.filter(workspace=ws, pk=pk).first()
        if t is None:
            raise Http404()
        PollTrigger.objects.filter(pk=t.pk).update(
            next_poll_at=timezone.now(),
            preview_next=bool(request.data.get("preview")),
            claimed_until=None,
            last_note="Relevé demandé…",
        )
        return Response({"status": "requested"}, status=202)
