"""Utilisation et suppression d'une action ; utilisation d'un workflow (sa suppression est dans `workflows.views_extra`)."""

from django.db.models import ProtectedError
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.permissions import PUBLISH, VIEW, get_workspace
from workflows import access
from workflows.removal import workflow_facts

from .models import Action
from .removal import action_facts, delete_action
from .usage import refuse_if_used, users_of


def _action(ws, slug):
    from django.http import Http404

    action = Action.objects.filter(workspace=ws, slug=slug).first()
    if action is None:
        raise Http404()
    return action


class ActionUsageView(APIView):
    """Les workflows qui appellent cette action (brouillons et versions publiées) ; sert à décider si elle peut être supprimée."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        action = _action(ws, slug)
        return Response({**users_of(ws, request.user, "action", slug), **action_facts(action)})


class WorkflowUsageView(APIView):
    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = access.workflow_or_404(request.user, ws, slug, VIEW)
        return Response({**users_of(ws, request.user, "workflow", slug), **workflow_facts(wf)})


def flag(request, name: str) -> bool:
    return request.query_params.get(name) in ("1", "true")


class ActionDeleteView(APIView):
    """Supprime une action, seulement si aucun workflow ne l'appelle (409 avec la liste, sinon). Une action déjà exécutée n'est supprimée qu'avec
    `?with_history=1` : l'historique de ses exécutions est alors supprimé aussi, définitivement (jamais en silence)."""

    def delete(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, PUBLISH)  # comme déprécier une action : une gestion du catalogue
        action = _action(ws, slug)
        refuse_if_used(ws, request.user, "action", slug, "cette action")
        name = action.name
        try:
            removed = delete_action(action, with_history=flag(request, "with_history"))
        except ProtectedError:
            raise serializers.ValidationError(
                {"action": "Suppression refusée : des éléments conservés en dépendent. Dépréciez-la plutôt."}
            ) from None
        record(
            "action.delete", actor=request.user, workspace=ws, slug=slug, name=name, history_deleted=removed["history"]
        )
        return Response(status=204)
