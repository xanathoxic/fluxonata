"""MCP de CONCEPTION — contrôles personnalisés et accès complet aux formulaires (ADR 0025, mise à jour du 2026-11-18).

Un assistant externe peut : lire la bibliothèque de contrôles (`list_controls`, `get_control`), créer et modifier le BROUILLON d'un contrôle de n'importe
quel type (groupe, lignes répétées, liste de choix, choix unique), le vérifier sans rien écrire (`validate_control`), vérifier un formulaire sans rien écrire
(`validate_form`, avec ou sans le workflow auquel il serait rattaché) et ajouter un contrôle à un formulaire (`add_control_to_form`). Un formulaire créé ou
modifié par `create_form` / `update_form` porte déjà TOUT ce que sait faire l'éditeur : champs, grille, règles, valeurs calculées, listes dynamiques,
contrôles personnalisés, style et CSS (le CSS est normalisé par le serveur, comme à l'enregistrement depuis l'éditeur).

Ce qui reste interdit, par construction : PUBLIER un contrôle ou un formulaire, le supprimer. Un contrôle créé ici est un brouillon marqué « assistant-externe » ;
ajouté à un formulaire AVANT publication, il y est copié avec la version « 0.0.0 » (copie du brouillon) : le formulaire reste autonome, comme avec une version
publiée, et une personne publie le contrôle pour qu'il soit proposé dans l'éditeur. Mêmes validations que l'API, mêmes erreurs structurées.
"""

import copy

from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import serializers

from audit.models import record
from core.folders import clean_folder
from tenancy.permissions import EDIT
from triggers import formcontrols, formcss, formengine

from . import access
from .mcp_design_common import (
    LIMIT,
    QUERY,
    SLUG,
    _conflict,
    _limit,
    _marked,
    _match,
    _need_edit,
    _ok,
    _spec,
    _text,
    _touched,
)
from .models import Workflow

DRAFT_VERSION = "0.0.0"  # version d'une copie de BROUILLON de contrôle dans un formulaire
MAX_SPEC = 256 * 1024
# Clés d'un champ qu'`add_control_to_form` laisse régler en plus du contrôle : tout le reste du champ vient du contrôle lui-même.
EXTRA_KEYS = {
    "label",
    "help",
    "required",
    "layout",
    "visible_if",
    "rules",
    "source",
    "prefill",
    "options",
    "default",
    "compute",
    "placeholder",
}


def _controls(request, ws):
    from formbuilder.models import Control

    return access.visible(Control.objects.filter(workspace=ws).prefetch_related("versions"), request.user, ws)


def _row(c) -> dict:
    latest = c.latest_version()
    spec = c.draft_spec or {}
    return {
        "slug": c.slug,
        "name": c.name,
        "description": c.description,
        "icon": c.icon,
        "folder": c.folder,
        "tags": c.tags,
        "mode": spec.get("mode"),
        "parts": len(spec.get("parts", [])),
        "published_version": latest.version if latest else None,
        "status": "publié" if latest else "brouillon (non publié : une personne doit le publier)",
    }


# --- lecture ------------------------------------------------------------------------------------------------------------------------------
def list_controls(request, ws, args):
    q = _text(args)
    rows = [_row(c) for c in _controls(request, ws) if _match(q, c.name, c.slug, c.description, " ".join(c.tags or []))]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def get_control(request, ws, args):
    c = _controls(request, ws).filter(slug=str(args.get("slug", ""))).first()
    if c is None:
        raise Http404()
    latest = c.latest_version()
    out = _row(c) | {"draft_revision": c.draft_revision, "draft_spec": c.draft_spec}
    out["published"] = {"version": latest.version, "spec": latest.spec} if latest else None
    return _ok(out)


# --- sérialiseurs d'entrée ----------------------------------------------------------------------------------------------------------------
class CreateControlIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    icon = serializers.RegexField(r"^[A-Za-z0-9_]{1,40}$", required=False, default="custom")
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=60), required=False, default=list, max_length=19
    )
    spec = serializers.JSONField(required=False)


class UpdateControlIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    revision = serializers.IntegerField(min_value=1)
    spec = serializers.JSONField(required=False)
    name = serializers.CharField(max_length=160, required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    icon = serializers.RegexField(r"^[A-Za-z0-9_]{1,40}$", required=False)


class ValidateControlIn(serializers.Serializer):
    spec = serializers.JSONField()


class ValidateFormIn(serializers.Serializer):
    definition = serializers.JSONField()
    workflow = serializers.SlugField(max_length=50, required=False)
    map = serializers.JSONField(required=False)


class AddControlIn(serializers.Serializer):
    form = serializers.SlugField(max_length=50)
    revision = serializers.IntegerField(min_value=1)
    control = serializers.SlugField(max_length=50)
    key = serializers.RegexField(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", required=False)
    section = serializers.CharField(
        max_length=64,
        required=False,
        help_text="Identifiant de la section, ou son rang (0 = la première) ; défaut : la dernière.",
    )
    field = serializers.JSONField(required=False)


def _check_spec(value, *, draft: bool = True):
    from formbuilder.control_views import _check_spec as check

    return check(value, draft=draft)


# --- écriture : contrôles -----------------------------------------------------------------------------------------------------------------
def create_control(request, ws, args):
    from formbuilder.models import Control, empty_spec

    _need_edit(request, ws)
    ser = CreateControlIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    spec = _check_spec(d["spec"]) if "spec" in d else empty_spec()
    folder = clean_folder(d["folder"])
    access.require(request.user, ws, folder, EDIT)
    try:
        with transaction.atomic():
            control = Control.objects.create(
                workspace=ws,
                slug=d["slug"],
                name=d["name"],
                description=d["description"],
                icon=d["icon"],
                folder=folder,
                tags=_marked(d["tags"]),
                owner=request.user,
                draft_spec=spec,
            )
    except IntegrityError:
        raise serializers.ValidationError({"slug": "Cet identifiant existe déjà."}) from None
    record("control.create", actor=request.user, workspace=ws, target=control)
    _touched(request, ws, "create_control", control, slug=control.slug, mode=spec.get("mode"))
    return _ok(
        {
            "slug": control.slug,
            "revision": control.draft_revision,
            "mode": spec.get("mode"),
            "status": "brouillon (non publié)",
        }
    )


def update_control(request, ws, args):
    from formbuilder.models import Control

    _need_edit(request, ws)
    ser = UpdateControlIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    with transaction.atomic():
        control = Control.objects.select_for_update().filter(workspace=ws, slug=d["slug"]).first()
        if control is None:
            raise Http404()
        access.require(request.user, ws, control.folder, EDIT)
        if d["revision"] != control.draft_revision:
            raise _conflict(control.draft_revision)
        if "spec" in d:
            spec = _check_spec(d["spec"])
            kept = (control.draft_spec or {}).get("mode")
            if kept and spec.get("mode") != kept:
                raise serializers.ValidationError(
                    {"spec": "Le type d'un contrôle ne peut pas être changé : créez un nouveau contrôle."}
                )
            control.draft_spec = spec
            control.draft_revision += 1
        for key in ("name", "description", "icon"):
            if key in d:
                setattr(control, key, d[key])
        control.tags = _marked(control.tags)
        control.save()
    record("control.update", actor=request.user, workspace=ws, target=control, fields=sorted(d))
    _touched(request, ws, "update_control", control, slug=control.slug, revision=control.draft_revision)
    return _ok({"slug": control.slug, "revision": control.draft_revision, "status": "brouillon (non publié)"})


def validate_control(request, ws, args):
    """Sans rien écrire : les problèmes pour un BROUILLON, et ceux qui empêcheraient la PUBLICATION (un brouillon peut être vide, une version non)."""
    ser = ValidateControlIn(data=args)
    ser.is_valid(raise_exception=True)
    spec = ser.validated_data["spec"]
    if len(str(spec)) > MAX_SPEC:
        raise serializers.ValidationError({"spec": "Description trop volumineuse."})
    draft = formcontrols.check_spec(spec, draft=True)
    publishable = formcontrols.check_spec(spec, draft=False)
    return _ok(
        {
            "valid_as_draft": not draft,
            "problems": draft,
            "publishable": not publishable,
            "publish_problems": publishable,
        }
    )


# --- écriture : formulaires ---------------------------------------------------------------------------------------------------------------
def _form_problems(definition) -> dict:
    """Ce que fait l'enregistrement d'un formulaire, sans écrire : le CSS est normalisé, puis la définition vérifiée."""
    if not isinstance(definition, dict):
        return {"definition": "Un objet est attendu."}
    value = definition
    if value.get("css") not in (None, ""):
        css, bad = formcss.normalize(value["css"])
        if bad:
            return {"css": f"Ligne {bad[0]['line']} : {bad[0]['message']}"}
        value = {**value, "css": css}
    return formengine.check_definition(value)


def validate_form(request, ws, args):
    """Vérifie une définition de formulaire (et, si `workflow` est donné, son lien avec les entrées du brouillon de ce workflow) sans rien écrire."""
    ser = ValidateFormIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    problems = _form_problems(d["definition"])
    against: dict = {}
    if "workflow" in d and not problems:
        wf = access.visible(Workflow.objects.filter(workspace=ws), request.user, ws).filter(slug=d["workflow"]).first()
        if wf is None:
            raise Http404()
        mapping = d.get("map")
        if mapping is not None and not formengine._valid_map(mapping):
            raise serializers.ValidationError(
                {"map": "Correspondance invalide : {entrée du workflow : clé d'un champ du formulaire}."}
            )
        against = formengine.check_against_inputs(d["definition"], (wf.draft_graph or {}).get("inputs") or {}, mapping)
    return _ok({"valid": not problems and not against, "problems": problems, "against_workflow": against})


def _field_keys(definition: dict) -> set[str]:
    return {
        str(f.get("key")) for s in definition.get("sections", []) for f in s.get("fields", []) if isinstance(f, dict)
    }


def _free_key(wanted: str, used: set[str]) -> str:
    n = 1
    while f"{wanted}_{n}" in used:
        n += 1
    return f"{wanted}_{n}"


def _section_of(definition: dict, ref: str | None) -> dict:
    sections = definition.get("sections") or []
    if not sections:
        raise serializers.ValidationError(
            {"section": "Ce formulaire n'a aucune section : ajoutez-en une avec update_form."}
        )
    if ref is None:
        return sections[-1]
    for i, s in enumerate(sections):
        if s.get("id") == ref or str(i) == ref:
            return s
    raise serializers.ValidationError(
        {"section": f"Section « {ref} » introuvable (identifiant ou rang 0..{len(sections) - 1})."}
    )


def _below(section: dict) -> dict:
    """Mise en page d'un champ ajouté sous les autres, sur toute la largeur : seulement si la section est déjà placée sur la grille."""
    fields = [f for f in section.get("fields", []) if isinstance(f, dict)]
    if not any(isinstance(f.get("layout"), dict) for f in fields):
        return {}
    rows = [formengine._lay(f)[0] + formengine._height(f) - 1 for f in fields if isinstance(f.get("layout"), dict)]
    return {"layout": {"row": max(rows, default=0) + 1, "col": 1, "span": formengine.GRID_COLUMNS}}


def add_control_to_form(request, ws, args):
    """Ajoute un contrôle de la bibliothèque à la fin d'une section d'un formulaire (brouillon). Publié : copie de sa DERNIÈRE version. Non publié :
    copie de son brouillon, version « 0.0.0 ». Le formulaire est revalidé en entier avant d'être enregistré."""
    from formbuilder.models import Form
    from formbuilder.views import _check_definition

    _need_edit(request, ws)
    ser = AddControlIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    control = _controls(request, ws).filter(slug=d["control"]).first()
    if control is None:
        raise Http404()
    extra = d.get("field") or {}
    if not isinstance(extra, dict) or set(extra) - EXTRA_KEYS:
        raise serializers.ValidationError(
            {"field": f"Réglages permis : {', '.join(sorted(EXTRA_KEYS))} (le reste vient du contrôle)."}
        )
    latest = control.latest_version()
    spec, version = (latest.spec, latest.version) if latest else (control.draft_spec or {}, DRAFT_VERSION)
    name, icon = (latest.name, latest.icon) if latest else (control.name, control.icon)
    with transaction.atomic():
        form = Form.objects.select_for_update().filter(workspace=ws, slug=d["form"]).first()
        if form is None:
            raise Http404()
        access.require(request.user, ws, form.folder, EDIT)
        if form.is_template:
            raise serializers.ValidationError({"form": "Un modèle ne se modifie pas ici : copiez-le en formulaire."})
        if d["revision"] != form.draft_revision:
            raise _conflict(form.draft_revision)
        definition = copy.deepcopy(form.draft_definition or {})
        section = _section_of(definition, d.get("section"))
        used = _field_keys(definition)
        key = d.get("key") or _free_key(control.slug.replace("-", "_"), used)
        if key in used:
            raise serializers.ValidationError({"key": f"La clé « {key} » existe déjà dans ce formulaire."})
        field = {"key": key, "label": name, "type": "custom", **_below(section), **extra}
        field["control"] = {
            "ref": control.slug,
            "version": version,
            "name": name,
            "icon": icon or "custom",
            "spec": copy.deepcopy(spec),
        }
        section.setdefault("fields", []).append(field)
        if latest is None:  # un contrôle sans version : le brouillon peut être vide, la copie d'un formulaire non
            formcontrols.check_spec(spec, draft=True)
        form.draft_definition = _check_definition(definition)
        form.draft_revision += 1
        form.tags = _marked(form.tags)
        form.save()
    record("form.update", actor=request.user, workspace=ws, target=form, fields=["definition"])
    _touched(
        request,
        ws,
        "add_control_to_form",
        form,
        slug=form.slug,
        control=control.slug,
        key=key,
        revision=form.draft_revision,
    )
    out = {
        "form": form.slug,
        "revision": form.draft_revision,
        "key": key,
        "control": control.slug,
        "control_version": version,
    }
    if latest is None:
        out["warning"] = (
            "Contrôle NON publié : le formulaire en porte une copie du brouillon (version 0.0.0). Une personne doit publier le contrôle "
            "(Bibliothèques → Contrôles) pour qu'il soit proposé dans l'éditeur de formulaires."
        )
    return _ok(out)


# --- tables d'outils (ajoutées à celles de mcp_design et mcp_design_write) ---------------------------------------------------------------------
def read_tools() -> dict:
    return {
        "list_controls": (
            _spec(
                "list_controls",
                "Chercher des contrôles personnalisés",
                "Contrôles de la bibliothèque (groupe, lignes répétées, liste de choix, choix unique), publiés ou en brouillon. À consulter AVANT d'en créer un.",
                QUERY | LIMIT,
            ),
            list_controls,
        ),
        "get_control": (
            _spec(
                "get_control",
                "Lire un contrôle personnalisé",
                "Description complète du brouillon (`draft_spec`, avec sa révision) et de la dernière version publiée.",
                {"slug": SLUG},
                ["slug"],
            ),
            get_control,
        ),
    }


def write_tools(spec_for, obj, txt, tags, rev) -> dict:
    """`spec_for` : le fabricant de descriptions d'outils d'écriture de mcp_design_write (`_w`)."""
    readonly = {
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    }
    return {
        "create_control": (
            spec_for(
                "create_control",
                "Créer un contrôle personnalisé (brouillon)",
                "Format exact : lisez d'abord get_authoring_guide (topic=controles). Crée le BROUILLON d'un contrôle de n'importe quel type "
                "(`spec.mode` : group, repeat, items, choice). Non publié : une personne le publie.",
                {
                    "slug": SLUG,
                    "name": txt,
                    "description": txt,
                    "icon": txt,
                    "folder": txt,
                    "tags": tags,
                    "spec": obj,
                },
                ["slug", "name"],
            ),
            create_control,
        ),
        "update_control": (
            spec_for(
                "update_control",
                "Modifier le brouillon d'un contrôle",
                "Remplace la description (`spec`) du BROUILLON (révision exigée) ; le type (`mode`) ne change pas. Renvoie la nouvelle révision.",
                {"slug": SLUG, "revision": rev, "spec": obj, "name": txt, "description": txt, "icon": txt},
                ["slug", "revision"],
            ),
            update_control,
        ),
        "validate_control": (
            spec_for(
                "validate_control",
                "Vérifier un contrôle",
                "Sans rien écrire : problèmes d'une description de contrôle (`spec`), comme brouillon et pour la publication.",
                {"spec": obj},
                ["spec"],
            )
            | readonly,
            validate_control,
        ),
        "validate_form": (
            spec_for(
                "validate_form",
                "Vérifier un formulaire",
                "Sans rien écrire : problèmes d'une définition de formulaire (champs, règles, listes dynamiques, contrôles, style, CSS). Avec `workflow` : "
                "vérifie aussi le lien avec les entrées de son brouillon (`map` facultatif).",
                {"definition": obj, "workflow": SLUG, "map": obj},
                ["definition"],
            )
            | readonly,
            validate_form,
        ),
        "add_control_to_form": (
            spec_for(
                "add_control_to_form",
                "Ajouter un contrôle à un formulaire",
                "Ajoute un contrôle de la bibliothèque (list_controls) à la fin d'une section du BROUILLON d'un formulaire (révision exigée). Publié : copie de sa dernière "
                "version ; non publié : copie du brouillon (version 0.0.0, avec un avertissement). `field` : réglages du champ (label, required, layout, rules, source, prefill…).",
                {"form": SLUG, "revision": rev, "control": SLUG, "key": txt, "section": txt, "field": obj},
                ["form", "revision", "control"],
            ),
            add_control_to_form,
        ),
    }
