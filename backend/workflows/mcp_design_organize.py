"""Ranger et renommer pour les assistants MCP de conception : dossiers (lister, créer) et informations d'un élément (nom, description, étiquettes,
DOSSIER) pour workflows, formulaires, contrôles personnalisés, actions et skills IA.

Mêmes droits que l'interface : le droit du type d'élément (modifier ; publier pour les actions et les skills), l'accès au dossier d'origine ET
au dossier d'arrivée (un dossier privé n'est ni nommé ni ouvert à qui n'y a pas droit). Rien de supprimé, rien de publié : ni le code d'une action
ni le contenu d'un brouillon ne change ici (voir `update_*` et `propose_*`)."""

from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from audit.models import record
from core.folders import clean_folder
from core.tags import clean_tags
from tenancy.models import Folder
from tenancy.permissions import EDIT, PUBLISH, VIEW, has_perm

from . import access
from .mcp_design_common import SLUG, _marked, _need_edit, _ok, _spec, _touched

MAX_DEPTH = 8
MAX_FOLDERS = 500
KINDS = ("workflow", "form", "control", "action", "ai_skill", "ansible_role", "inventory")
RIGHT = {
    "workflow": EDIT,
    "form": EDIT,
    "control": EDIT,
    "action": PUBLISH,
    "ai_skill": PUBLISH,
    "ansible_role": EDIT,  # ranger un rôle ne change ni ses fichiers ni ses versions ; un jeton de conception ne porte jamais « publish » (ADR 0025)
    "inventory": EDIT,
}
NO_TAGS = ("ansible_role", "inventory")  # ni étiquettes ici ; le nom d'un rôle EST son identifiant
RECORD = {
    "workflow": "workflow.update",
    "form": "form.update",
    "control": "control.update",
    "action": "action.update",
    "ai_skill": "ai_skill.update",
    "ansible_role": "ansible_role.update",
    "inventory": "inventory.move",
}


def _model(kind: str):
    from ai.models import AiSkill
    from catalog.models import Action, AnsibleRole, Inventory
    from formbuilder.models import Control, Form

    return {
        "workflow": None,
        "form": Form,
        "control": Control,
        "action": Action,
        "ai_skill": AiSkill,
        "ansible_role": AnsibleRole,
        "inventory": Inventory,
    }[kind]


def _kind(raw) -> str:
    if raw not in KINDS:
        raise serializers.ValidationError(
            {"kind": "Type inconnu (workflow, form, control, action, ai_skill, ansible_role ou inventory)."}
        )
    return raw


def _need(request, ws, kind: str) -> None:
    _need_edit(request, ws)
    if not has_perm(request.user, ws, RIGHT[kind]):
        raise PermissionDenied("Droit de publier requis pour les actions et les skills.")


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def list_folders(request, ws, args):
    kind = _kind(args.get("kind"))
    paths = sorted(Folder.objects.filter(workspace=ws, kind=kind).values_list("path", flat=True))
    if kind == "workflow":  # un dossier privé n'est pas nommé à qui n'y a pas accès
        rules = access.rules(request.user, ws)
        paths = [p for p in paths if rules.allows(p, VIEW)]
    return _ok({"kind": kind, "count": len(paths), "folders": paths})


def create_folder(request, ws, args):
    kind = _kind(args.get("kind"))
    _need(request, ws, kind)
    path = clean_folder(args.get("path"))
    if not path:
        raise serializers.ValidationError({"path": "Indiquez le chemin du dossier (niveaux séparés par « / »)."})
    if len(path.split("/")) > MAX_DEPTH:
        raise serializers.ValidationError({"path": f"{MAX_DEPTH} niveaux au maximum."})
    access.require(request.user, ws, path, EDIT)
    parts = path.split("/")
    with transaction.atomic():
        if Folder.objects.filter(workspace=ws, kind=kind).count() >= MAX_FOLDERS:
            raise serializers.ValidationError({"path": f"Limite de {MAX_FOLDERS} dossiers atteinte."})
        created = [
            p
            for p in ("/".join(parts[: i + 1]) for i in range(len(parts)))
            if Folder.objects.get_or_create(workspace=ws, kind=kind, path=p)[1]
        ]
    if not created:
        raise serializers.ValidationError({"path": "Ce dossier existe déjà."})
    record("folder.create", actor=request.user, workspace=ws, kind=kind, path=path, external_assistant=True)
    return _ok({"kind": kind, "path": path, "created": created})


class ItemInfoIn(serializers.Serializer):
    kind = serializers.ChoiceField(choices=KINDS)
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160, required=False)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    folder = serializers.CharField(max_length=200, required=False, allow_blank=True)
    tags = serializers.ListField(child=serializers.CharField(max_length=60), required=False, max_length=19)


def _item(ws, kind: str, slug: str):
    if kind == "workflow":
        from .models import Workflow

        return Workflow.objects.select_for_update().filter(workspace=ws, slug=slug).first()
    if kind == "ansible_role":  # un rôle se désigne par son nom
        return _model(kind).objects.select_for_update().filter(workspace=ws, name=slug).first()
    return _model(kind).objects.select_for_update().filter(workspace=ws, slug=slug).first()


def _ensure_tree(ws, kind: str, target: str) -> None:
    """Le dossier d'arrivée (et ses parents) existe dans l'arborescence."""
    parts = target.split("/") if target else []
    for i in range(len(parts)):
        Folder.objects.get_or_create(workspace=ws, kind=kind, path="/".join(parts[: i + 1]))


def _apply(item, kind: str, d: dict, target) -> None:
    if target is not None:
        item.folder = target
    for key in ("name", "description"):
        if key in d:
            setattr(item, key, d[key])
    if kind in NO_TAGS:
        return
    if "tags" in d:
        item.tags = clean_tags(d["tags"])
    item.tags = _marked(item.tags)[: 10 if kind == "action" else 20]


def slug_of(item) -> str:
    return getattr(item, "slug", None) or item.name


def update_item_info(request, ws, args):
    ser = ItemInfoIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    changes = set(d) & {"name", "description", "folder", "tags"}
    if not changes:
        raise serializers.ValidationError("Indiquez au moins un changement : name, description, folder ou tags.")
    kind = d["kind"]
    if kind in NO_TAGS and ({"tags"} & set(d) or (kind == "ansible_role" and "name" in d)):
        raise serializers.ValidationError(
            "Un rôle se range et se décrit (folder, description) ; son nom est son identifiant. Un inventaire n'a pas d'étiquettes."
        )
    _need(request, ws, kind)
    target = clean_folder(d["folder"]) if "folder" in d else None
    if target is not None and len(target.split("/")) > MAX_DEPTH:
        raise serializers.ValidationError({"folder": f"{MAX_DEPTH} niveaux au maximum."})
    with transaction.atomic():
        item = _item(ws, kind, d["slug"])
        if item is None:
            raise Http404()
        access.require(request.user, ws, item.folder, EDIT)
        if kind == "form" and item.is_template:
            raise serializers.ValidationError({"slug": "Un modèle ne se modifie pas ici : copiez-le en formulaire."})
        before = item.folder
        if target is not None:
            access.require(request.user, ws, target, EDIT)
            _ensure_tree(ws, kind, target)
        _apply(item, kind, d, target)
        item.save()
    record(RECORD[kind], actor=request.user, workspace=ws, target=item, fields=sorted(changes))
    _touched(request, ws, "update_item_info", item, slug=slug_of(item), kind=kind, fields=sorted(changes))
    return _ok(
        {
            "kind": kind,
            "slug": slug_of(item),
            "name": getattr(item, "name", slug_of(item)),
            "folder": item.folder,
            "moved_from": before if target is not None and before != item.folder else None,
            "tags": getattr(item, "tags", []),
            "status": "informations mises à jour (contenu, code et versions inchangés)",
        }
    )


def read_tools() -> dict:
    return {
        "list_folders": (
            _spec(
                "list_folders",
                "Lister les dossiers",
                "Arborescence de dossiers d'un type d'élément (workflow, form, control, action, ai_skill, ansible_role, inventory) : chemins « A/B ». À lire avant de ranger quelque chose.",
                {"kind": {"type": "string", "enum": list(KINDS)}},
                ["kind"],
            ),
            list_folders,
        )
    }


def write_tools(spec_for, txt, tags) -> dict:
    return {
        "create_folder": (
            spec_for(
                "create_folder",
                "Créer un dossier",
                "Crée un dossier (et ses parents) pour ranger des éléments d'un type donné. Refusé s'il existe déjà.",
                {"kind": {"type": "string", "enum": list(KINDS)}, "path": txt},
                ["kind", "path"],
            ),
            create_folder,
        ),
        "update_item_info": (
            spec_for(
                "update_item_info",
                "Ranger ou renommer un élément",
                "Change le dossier (`folder`, « » pour la racine), le nom, la description ou les étiquettes d'un workflow, formulaire, contrôle, action ou skill IA ; pour un rôle Ansible (désigné par son nom) : le dossier et la description ; pour un inventaire : le dossier, le nom et la description. "
                "Le contenu (graphe, définition, code, versions) n'est PAS touché : pour cela, update_workflow / update_form / update_control / propose_action / propose_skill.",
                {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "slug": SLUG,
                    "folder": txt,
                    "name": txt,
                    "description": txt,
                    "tags": tags,
                },
                ["kind", "slug"],
            ),
            update_item_info,
        ),
    }
