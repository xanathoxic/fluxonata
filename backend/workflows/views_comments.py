from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.permissions import EDIT, VIEW, get_workspace, has_perm

from . import access
from .models import StepComment, Workflow


def _row(c: StepComment) -> dict:
    return {
        "id": c.pk,
        "node_id": c.node_id,
        "author": c.author.username if c.author else None,
        "text": c.text,
        "resolved": c.resolved,
        "created_at": c.created_at,
    }


def _wf(user, ws, slug) -> Workflow:
    return access.workflow_or_404(user, ws, slug)


class CommentListCreateView(APIView):
    """Anyone who can see the workflow can read and add remarks; only the author or an editor can change or remove one."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        qs = _wf(request.user, ws, slug).comments.select_related("author")
        if node := request.query_params.get("node"):
            qs = qs.filter(node_id=node)
        return Response({"results": [_row(c) for c in qs]})

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = _wf(request.user, ws, slug)
        text = str(request.data.get("text") or "").strip()
        node_id = str(request.data.get("node_id") or "")
        if not text or len(text) > 2000:
            raise serializers.ValidationError({"text": "Remarque requise (2000 caractères au plus)."})
        if node_id not in {n.get("id") for n in wf.draft_graph.get("nodes", [])}:
            raise serializers.ValidationError({"node_id": "Étape inconnue dans le brouillon."})
        if wf.comments.count() >= 500:
            raise serializers.ValidationError({"text": "Trop de remarques sur ce workflow (500 au plus)."})
        c = StepComment.objects.create(workflow=wf, node_id=node_id, author=request.user, text=text)
        record("workflow.comment", actor=request.user, workspace=ws, target=wf, node=node_id)
        return Response(_row(c), status=201)


class CommentDetailView(APIView):
    def _get(self, request, workspace, slug, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        c = StepComment.objects.select_related("author").filter(workflow=_wf(request.user, ws, slug), pk=pk).first()
        if c is None:
            raise Http404()
        if c.author_id != request.user.pk and not has_perm(request.user, ws, EDIT):
            raise serializers.ValidationError({"detail": "Seul l'auteur ou un éditeur peut modifier cette remarque."})
        return c

    def patch(self, request, workspace, slug, pk):
        c = self._get(request, workspace, slug, pk)
        if "resolved" in request.data:
            c.resolved = bool(request.data["resolved"])
            c.save(update_fields=["resolved"])
        return Response(_row(c))

    def delete(self, request, workspace, slug, pk):
        self._get(request, workspace, slug, pk).delete()
        return Response(status=204)
