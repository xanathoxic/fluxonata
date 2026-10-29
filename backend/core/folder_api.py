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
from tenancy.models import Folder, Membership
from tenancy.permissions import ADMINISTER, EDIT, PUBLISH, VIEW, get_workspace
from workflows import access
from workflows.models import FolderAccess, FolderGrant, Workflow

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


def _ancestors_under(ws, path) -> list[str]:
    return [a.folder for a in FolderAccess.objects.filter(workspace=ws) if _under(a.folder, path)]


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
        paths = list(Folder.objects.filter(workspace=ws, kind=kind).values_list("path", flat=True))
        if kind != "workflow":
            return Response({"results": paths})
        rules = access.rules(request.user, ws)  # a private folder is not even named to someone without access
        return Response(
            {
                "results": [p for p in paths if rules.allows(p, VIEW)],
                "private": sorted(p for p in rules.private if rules.allows(p, VIEW)),
            }
        )

    @transaction.atomic
    def post(self, request, workspace):
        ws, kind = _workspace_and_kind(request, workspace, request.data.get("kind"))
        path = _path(request.data.get("path"))
        if kind == "workflow":
            access.require(request.user, ws, path, EDIT)
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
        if kind == "workflow":
            access.require(request.user, ws, old, EDIT)
            access.require(request.user, ws, new, EDIT)
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
        if kind == "workflow":  # the private folders (and who may enter them) follow the rename
            for lock in FolderAccess.objects.filter(workspace=ws):
                if _under(lock.folder, old):
                    lock.folder = new + lock.folder[len(old) :]
                    lock.save(update_fields=["folder"])
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
        if kind == "workflow":
            access.require(request.user, ws, path, EDIT)
        n = len(_items_under(ws, kind, path))
        if n:
            raise serializers.ValidationError(
                {"path": f"Le dossier n'est pas vide ({n} élément(s)) : déplacez ou supprimez son contenu d'abord."}
            )
        removed = [f for f in Folder.objects.filter(workspace=ws, kind=kind) if _under(f.path, path)]
        for f in removed:
            f.delete()
        if kind == "workflow":
            FolderAccess.objects.filter(workspace=ws, folder__in=[a for a in _ancestors_under(ws, path)]).delete()
        record("folder.delete", actor=request.user, workspace=ws, kind=kind, path=path, folders=len(removed))
        return Response(status=204)


class FolderAccessView(APIView):
    """Private folders of the workspace and who may enter them (administrators only; they always have access themselves).
    A grant narrows a role, never widens it: see `workflows.access`."""

    @staticmethod
    def _row(a):
        return {
            "folder": a.folder,
            "grants": [
                {"username": g.user.username, "level": g.level} for g in a.grants.select_related("user").order_by("id")
            ],
        }

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rows = FolderAccess.objects.filter(workspace=ws).order_by("folder")
        return Response({"results": [self._row(a) for a in rows], "levels": dict(FolderGrant.Level.choices)})

    @transaction.atomic
    def put(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        folder = _path(request.data.get("folder"), "folder")
        grants = request.data.get("grants") or []
        if not isinstance(grants, list) or len(grants) > 200:
            raise serializers.ValidationError({"grants": "Une liste de 200 personnes au plus."})
        wanted: dict[int, str] = {}
        for g in grants:
            if not isinstance(g, dict) or g.get("level") not in FolderGrant.Level.values:
                raise serializers.ValidationError({"grants": "Chaque accès : {username, level (view, run ou edit)}."})
            m = Membership.objects.filter(workspace=ws, user__username=str(g.get("username", ""))).first()
            if m is None:
                raise serializers.ValidationError({"grants": f"« {g.get('username')} » n'est pas membre de l'espace."})
            wanted[m.user_id] = g["level"]
        a, created = FolderAccess.objects.get_or_create(workspace=ws, folder=folder)
        a.grants.exclude(user_id__in=list(wanted)).delete()
        for uid, level in wanted.items():
            FolderGrant.objects.update_or_create(access=a, user_id=uid, defaults={"level": level})
        Folder.objects.get_or_create(workspace=ws, kind="workflow", path=folder)
        record("folder.access_set", actor=request.user, workspace=ws, folder=folder, people=len(wanted), new=created)
        return Response(self._row(a))

    @transaction.atomic
    def delete(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        folder = _path(request.query_params.get("folder"), "folder")
        a = FolderAccess.objects.filter(workspace=ws, folder=folder).first()
        if a is None:
            raise Http404()
        a.delete()
        record("folder.access_clear", actor=request.user, workspace=ws, folder=folder)
        return Response(status=204)
