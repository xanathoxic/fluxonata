"""Folder trees of workflows, actions and AI skills: create, rename (moves everything below) and delete (empty only).

A folder exists when an item uses it or when it was created explicitly (`tenancy.Folder`). Items keep a plain `folder`
path ("A/B"), so moving an item is unchanged; these routes manage the folders themselves."""

from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from ai.models import AiSkill
from audit.models import record
from catalog.models import Action
from tenancy.models import Folder
from tenancy.permissions import EDIT, PUBLISH, VIEW, get_workspace
from workflows.models import Workflow

from .folders import clean_folder

ITEMS = {"workflow": (Workflow, EDIT), "action": (Action, PUBLISH), "ai_skill": (AiSkill, PUBLISH)}
MAX_DEPTH = 8
MAX_FOLDERS = 500


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _kind(raw) -> str:
    if raw not in ITEMS:
        raise serializers.ValidationError({"kind": "Type de dossier inconnu (workflow, action ou ai_skill)."})
    return raw


def _path(raw, field="path") -> str:
    p = clean_folder(raw)
    if not p:
        raise serializers.ValidationError({field: "Indiquez un nom de dossier."})
    if len(p.split("/")) > MAX_DEPTH:
        raise serializers.ValidationError({field: f"{MAX_DEPTH} niveaux au maximum."})
    return p


def _workspace_and_kind(request, workspace, raw_kind):
    """Membership first (404 for outsiders, before anything is validated), then the kind, then the right it requires."""
    get_workspace(request.user, workspace, VIEW)
    kind = _kind(raw_kind)
    return get_workspace(request.user, workspace, ITEMS[kind][1]), kind


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _items_under(ws, kind, path):
    model = ITEMS[kind][0]
    return [i for i in model.objects.filter(workspace=ws).exclude(folder="") if _under(i.folder, path)]


def _exists(ws, kind, path) -> bool:
    if Folder.objects.filter(workspace=ws, kind=kind, path=path).exists():
        return True
    return any(
        i.folder == path or i.folder.startswith(path + "/")
        for i in ITEMS[kind][0].objects.filter(workspace=ws).exclude(folder="")
    )


class FoldersView(APIView):
    def get(self, request, workspace):
        get_workspace(request.user, workspace, VIEW)
        kind = _kind(request.query_params.get("kind"))
        ws = get_workspace(request.user, workspace, VIEW)
        return Response(
            {"results": list(Folder.objects.filter(workspace=ws, kind=kind).values_list("path", flat=True))}
        )

    @transaction.atomic
    def post(self, request, workspace):
        ws, kind = _workspace_and_kind(request, workspace, request.data.get("kind"))
        path = _path(request.data.get("path"))
        if Folder.objects.filter(workspace=ws, kind=kind).count() >= MAX_FOLDERS:
            raise serializers.ValidationError({"path": f"Limite de {MAX_FOLDERS} dossiers atteinte."})
        created = [p for p in _ancestors(path) if Folder.objects.get_or_create(workspace=ws, kind=kind, path=p)[1]]
        if not created:
            raise serializers.ValidationError({"path": "Ce dossier existe déjà."})
        record("folder.create", actor=request.user, workspace=ws, kind=kind, path=path)
        return Response({"kind": kind, "path": path}, status=201)

    @transaction.atomic
    def patch(self, request, workspace):
        """Rename or move a folder: everything below follows (items, sub-folders)."""
        ws, kind = _workspace_and_kind(request, workspace, request.data.get("kind"))
        old, new = _path(request.data.get("path")), _path(request.data.get("new_path"), "new_path")
        if not _exists(ws, kind, old):
            raise Http404()
        if new == old:
            raise serializers.ValidationError({"new_path": "Le nom est inchangé."})
        if _under(new, old):
            raise serializers.ValidationError({"new_path": "Un dossier ne peut pas être déplacé dans lui-même."})
        if _exists(ws, kind, new):
            raise serializers.ValidationError({"new_path": "Un dossier porte déjà ce nom : choisissez un autre nom."})
        moved = 0
        for item in _items_under(ws, kind, old):
            item.folder = new + item.folder[len(old) :]
            item.save(update_fields=["folder"])
            moved += 1
        for f in Folder.objects.filter(workspace=ws, kind=kind):
            if _under(f.path, old):
                f.path = new + f.path[len(old) :]
                f.save(update_fields=["path"])
        for p in _ancestors(new):
            Folder.objects.get_or_create(workspace=ws, kind=kind, path=p)
        record("folder.rename", actor=request.user, workspace=ws, kind=kind, old=old, new=new, items=moved)
        return Response({"kind": kind, "path": new, "items_moved": moved})

    @transaction.atomic
    def delete(self, request, workspace):
        ws, kind = _workspace_and_kind(request, workspace, request.query_params.get("kind"))
        path = _path(request.query_params.get("path"))
        if not _exists(ws, kind, path):
            raise Http404()
        n = len(_items_under(ws, kind, path))
        if n:
            raise serializers.ValidationError(
                {"path": f"Le dossier n'est pas vide ({n} élément(s)) : déplacez ou supprimez son contenu d'abord."}
            )
        removed = [f for f in Folder.objects.filter(workspace=ws, kind=kind) if _under(f.path, path)]
        for f in removed:
            f.delete()
        record("folder.delete", actor=request.user, workspace=ws, kind=kind, path=path, folders=len(removed))
        return Response(status=204)
