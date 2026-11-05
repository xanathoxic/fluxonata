"""What the « Catalogue » role may do with runs: launch a catalog item its groups allow, see ITS OWN runs in a reduced form
(name, time, progress; then inputs with secrets masked, and the result). Nothing here exposes steps, logs, events or the graph.
Authorization is decided here, never by the UI."""

import re

from django.http import Http404
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.models import Environment
from workflows.catalog_views import can_launch, catalog_only, catalog_workflows, catalog_workspace

from . import engine
from .models import StepRun, WorkflowRun
from .serializers import validate_inputs_against, with_defaults

MASK = "••••••••"
SECRET_KEY = re.compile(r"(passw|mdp|secret|token|api[_-]?key|credential|private[_-]?key)", re.I)


def mask_inputs(schema: dict | None, inputs):
    """The inputs as they may be shown: values of secret fields replaced by a mask. A field is secret when its schema says so
    (`format: password` or `writeOnly`) or when its name looks like a secret. Returns (masked copy, keys masked at the top level)."""
    props = (schema or {}).get("properties") or {}
    flagged = {
        k for k, p in props.items() if isinstance(p, dict) and (p.get("format") == "password" or p.get("writeOnly"))
    }

    def walk(value, top=False):
        if isinstance(value, dict):
            return {
                k: (MASK if (k in flagged and top) or SECRET_KEY.search(str(k)) else walk(v)) for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    masked = walk(inputs, top=True)
    keys = sorted(
        k
        for k, v in (masked or {}).items()
        if isinstance(masked, dict) and v == MASK and inputs.get(k) not in (None, "")
    )
    return masked, keys


def progress(run: WorkflowRun) -> dict:
    total = len(run.definition["graph"]["nodes"])
    if run.status == WorkflowRun.Status.SUCCEEDED:
        return {"done": total, "total": total}
    done = (
        run.steps.filter(status__in=[StepRun.Status.SUCCEEDED, StepRun.Status.SKIPPED])
        .exclude(node_type="compensation")
        .count()
    )
    return {"done": min(done, total), "total": total}


def _error(run) -> str:
    err = run.error
    return (err.get("message") if isinstance(err, dict) else "") or (
        "Le traitement a échoué." if run.status == WorkflowRun.Status.FAILED else ""
    )


def _row(run: WorkflowRun) -> dict:
    w = run.definition["workflow"]
    return {
        "id": str(run.id),
        "workflow": {"slug": w["slug"], "name": run.workflow_version.workflow.name, "version": w["version"]},
        "status": run.status,
        "status_label": run.get_status_display(),
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "progress": progress(run),
    }


def _mine(user, ws):
    return (
        WorkflowRun.objects.filter(workspace=ws, requested_by=user, parent_step__isnull=True)
        .select_related("workflow_version__workflow")
        .order_by("-created_at")
    )


class CatalogRunListView(ListAPIView):
    """Your own runs, most recent first."""

    pagination_class = None

    def list(self, request, workspace):
        ws = catalog_workspace(request.user, workspace)
        return Response({"results": [_row(r) for r in _mine(request.user, ws)[:200]]})


class CatalogRunDetailView(APIView):
    def get(self, request, workspace, pk):
        ws = catalog_workspace(request.user, workspace)
        run = _mine(request.user, ws).filter(pk=pk).first()
        if run is None:
            raise Http404()  # someone else's run is not even acknowledged
        inputs, masked = mask_inputs(run.workflow_version.input_schema, run.inputs)
        granted = catalog_workflows(ws, request.user).filter(pk=run.workflow_version.workflow_id).exists()
        return Response(
            {
                **_row(run),
                "inputs": inputs,
                "masked_keys": masked,
                "outputs": run.outputs,
                "error": _error(run),
                "can_relaunch": granted and can_launch(request.user, ws),
            }
        )


class StartSerializer(serializers.Serializer):
    environment = serializers.ChoiceField(choices=["test", "prod", "dev"])
    inputs = serializers.JSONField(default=dict)


class CatalogStartView(APIView):
    """Launch the published version of a catalog item. For the « Catalogue » role the item must be granted to one of its groups
    and the development environment is refused; for the other roles the usual right to run applies."""

    serializer_class = StartSerializer

    def post(self, request, workspace, slug):
        ws = catalog_workspace(request.user, workspace)
        ser = StartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if not can_launch(request.user, ws):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Vous n'avez pas le droit de lancer cette automatisation.")
        wf = catalog_workflows(ws, request.user).filter(slug=slug).first()
        version = wf.latest_version() if wf else None
        if version is None:
            raise Http404()
        kind = ser.validated_data["environment"]
        if kind == "dev" and catalog_only(request.user, ws):
            raise serializers.ValidationError({"environment": "Environnement indisponible."})
        env = Environment.objects.filter(workspace=ws, kind=kind).first()
        if env is None:
            raise serializers.ValidationError({"environment": "Environnement indisponible dans cet espace."})
        inputs = ser.validated_data["inputs"]
        if not isinstance(inputs, dict):
            raise serializers.ValidationError({"inputs": "Les entrées doivent être un objet."})
        inputs = with_defaults(version.input_schema, inputs)
        problems = validate_inputs_against(version.input_schema, inputs)
        if problems:
            raise serializers.ValidationError(problems)
        run, created = engine.start_run(
            workspace=ws,
            environment=env,
            workflow_version=version,
            user=request.user,
            inputs=inputs,
            idempotency_key=request.headers.get("Idempotency-Key"),
            trigger="catalog",
        )
        if created:
            record("catalog.launch", actor=request.user, workspace=ws, target=wf)
        return Response(_row(run), status=202 if created else 200)
