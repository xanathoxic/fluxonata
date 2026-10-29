import json

from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from tenancy.permissions import EDIT, get_workspace

from . import access, debug, diffing
from .models import Workflow

MAX_GRAPH = 600 * 1024


def _workflow(user, ws, slug) -> Workflow:
    wf = access.workflow_or_404(user, ws, slug, EDIT)
    if wf is None:
        raise Http404()
    return wf


def _graph(request, wf) -> dict:
    g = request.data.get("graph")
    if g is None:
        return wf.draft_graph
    if not isinstance(g, dict) or len(json.dumps(g)) > MAX_GRAPH:
        raise serializers.ValidationError({"graph": "Graphe invalide ou trop volumineux."})
    return g


def _run(request, ws, wf):
    rid = request.data.get("run_id") or request.query_params.get("run_id")
    run = debug.last_run(ws, wf, rid)
    if rid and run is None:
        raise Http404()
    return run


class DebugContextView(APIView):
    """Which run feeds the editor's previews, and the outputs of its steps (bounded): what « Épingler » can pin."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        wf = _workflow(request.user, ws, slug)
        run = _run(request, ws, wf)
        if run is None:
            return Response({"run": None, "outputs": {}})
        outputs = {
            sr.node_id: debug._bounded(sr.output)
            for sr in run.steps.all()
            if sr.status == "succeeded" and isinstance(sr.output, dict)
        }
        return Response(
            {
                "run": {"id": str(run.id), "status": run.status, "created_at": run.created_at, "inputs": run.inputs},
                "outputs": outputs,
            }
        )


class DebugEvalView(APIView):
    """Value of an expression on the last run's data (and pins). `items` (an expression giving a list) binds its first
    element as `item`, for the per-item expressions of a transformation."""

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        wf = _workflow(request.user, ws, slug)
        expr = request.data.get("expr")
        if not isinstance(expr, str) or not expr.strip() or len(expr) > 600:
            raise serializers.ValidationError({"expr": "Expression requise (600 caractères au plus)."})
        ctx, where = debug.build_context(_graph(request, wf), _run(request, ws, wf))
        item = None
        items = request.data.get("items")
        if isinstance(items, str) and items.strip():
            listed = debug.evaluate_preview(items.strip().removeprefix("{{").removesuffix("}}"), ctx)
            if not listed["ok"] or not isinstance(listed["value"], list) or not listed["value"]:
                return Response(
                    {
                        "ok": False,
                        "error": "La liste à traiter n'a pas de donnée d'essai (lancez le workflow ou épinglez la sortie de l'étape qui la produit).",
                    }
                )
            item = listed["value"][0]
        return Response({**debug.evaluate_preview(expr, ctx, item), "has_data": bool(where)})


class DebugTestStepView(APIView):
    """Runs one pure step (decision, choice, transformation) on the last run's data and pins; other steps answer with
    their pin or last output. Nothing with an effect is ever executed here."""

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        wf = _workflow(request.user, ws, slug)
        graph = _graph(request, wf)
        node = next((n for n in graph.get("nodes", []) if n.get("id") == request.data.get("node_id")), None)
        if node is None:
            raise Http404()
        ctx, where = debug.build_context(graph, _run(request, ws, wf))
        return Response(debug.test_step(node, ctx, where))


class VersionDiffView(APIView):
    """Differences between two versions (`a`, `b`: a version number or `draft`). Reading rights are enough."""

    def get(self, request, workspace, slug):
        from tenancy.permissions import VIEW

        ws = get_workspace(request.user, workspace, VIEW)
        wf = _workflow(request.user, ws, slug)
        graphs = {}
        for key in ("a", "b"):
            ref = request.query_params.get(key, "")
            if ref == "draft":
                graphs[key] = wf.draft_graph
            else:
                v = wf.versions.filter(version=ref).first()
                if v is None:
                    raise Http404()
                graphs[key] = v.graph
        return Response(
            {
                "a": request.query_params["a"],
                "b": request.query_params["b"],
                **diffing.diff_graphs(graphs["a"], graphs["b"]),
            }
        )
