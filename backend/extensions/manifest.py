"""Manifeste d'une extension : ce qu'elle déclare, vérifié avant tout chargement (aucune confiance dans son contenu)."""

import re
from dataclasses import dataclass
from typing import Any

from . import EXTENSION_API

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
WORD_RE = re.compile(r"^[a-z][a-z0-9]{0,23}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,120}$")
UI_FILE_RE = re.compile(
    r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*){0,3}\.js$"
)  # aucun segment ne commence par « . »
MENU_GROUPS = ("Utiliser", "Concevoir", "Intégrer", "Superviser")  # les groupes du menu de l'application
# droits d'un espace (tenancy.permissions) ; un test vérifie que cette liste suit celle de tenancy
PERMISSIONS = ("view", "edit", "publish", "run", "approve", "manage_connections", "administer", "catalog")
MAX_MENU = 8
TOP_KEYS = {"id", "name", "version", "api", "description", "license", "menu", "ui"}
ENTRY_KEYS = {"id", "label", "path", "group", "icon", "permission"}


class ManifestError(ValueError):
    """Le manifeste est refusé ; le message dit pourquoi, en français, sans rien citer d'autre que sa propre valeur fautive."""


@dataclass(frozen=True)
class MenuEntry:
    id: str
    label: str
    path: str
    group: str
    icon: str
    permission: str


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    description: str
    license: str
    menu: tuple[MenuEntry, ...]
    ui_module: str  # chemin relatif dans le dossier `ui/` du paquet ; "" = aucune page


def _text(raw: dict, key: str, limit: int, *, required: bool = True) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ManifestError(f"« {key} » : texte {'requis, ' if required else ''}de {limit} caractères au plus.")
    return value.strip()


def _entry(raw: Any, seen: set[str]) -> MenuEntry:
    if not isinstance(raw, dict) or set(raw) - ENTRY_KEYS:
        raise ManifestError("menu : chaque entrée est un objet (id, label, path, group, icon, permission).")
    ident = raw.get("id")
    if not isinstance(ident, str) or not WORD_RE.match(ident) or ident in seen:
        raise ManifestError("menu : « id » requis (lettres minuscules et chiffres), unique dans l'extension.")
    seen.add(ident)
    path = raw.get("path", "/")
    if not isinstance(path, str) or not PATH_RE.match(path) or ".." in path:
        raise ManifestError(
            f"menu « {ident} » : « path » commence par « / » (lettres, chiffres, . _ ~ - /), sans « .. »."
        )
    group, icon, permission = raw.get("group", "Intégrer"), raw.get("icon", "puzzle"), raw.get("permission", "view")
    if group not in MENU_GROUPS:
        raise ManifestError(f"menu « {ident} » : « group » parmi {', '.join(MENU_GROUPS)}.")
    if not isinstance(icon, str) or not WORD_RE.match(icon):
        raise ManifestError(f"menu « {ident} » : « icon » est un nom simple (ex. « puzzle »).")
    if permission not in PERMISSIONS:
        raise ManifestError(f"menu « {ident} » : « permission » parmi {', '.join(PERMISSIONS)}.")
    return MenuEntry(ident, _text(raw, "label", 40), path, group, icon, permission)


def parse_manifest(raw: Any) -> Manifest:
    """Vérifie un manifeste et le renvoie sous sa forme figée ; lève `ManifestError` au premier défaut."""
    if not isinstance(raw, dict):
        raise ManifestError("Le manifeste doit être un objet.")
    if unknown := set(raw) - TOP_KEYS:
        raise ManifestError(f"Clé(s) inconnue(s) : {', '.join(sorted(map(str, unknown)))}.")
    ident = raw.get("id")
    if not isinstance(ident, str) or not ID_RE.match(ident):
        raise ManifestError("« id » : minuscules, chiffres et tirets, 2 à 40 caractères, en commençant par une lettre.")
    version = raw.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ManifestError("« version » : trois nombres séparés par des points (ex. 1.0.0).")
    if raw.get("api") != EXTENSION_API:
        raise ManifestError(
            f"« api » : cette plateforme sert les extensions d'API {EXTENSION_API} (le manifeste vise {raw.get('api')!r})."
        )
    menu_raw = raw.get("menu", [])
    if not isinstance(menu_raw, list) or len(menu_raw) > MAX_MENU:
        raise ManifestError(f"« menu » : une liste de {MAX_MENU} entrées au plus.")
    seen: set[str] = set()
    menu = tuple(_entry(e, seen) for e in menu_raw)
    ui = raw.get("ui", {})
    module = ui.get("module", "") if isinstance(ui, dict) else None
    if module is None or set(ui) - {"module"} or (module and not UI_FILE_RE.match(module)):
        raise ManifestError(
            '« ui » : {"module": "index.js"} (fichier .js du dossier ui/ de l\'extension, sans « .. »).'
        )
    if menu and not module:
        raise ManifestError("Une entrée de menu ouvre la page de l'extension : « ui.module » est requis.")
    return Manifest(
        id=ident,
        name=_text(raw, "name", 80),
        version=version,
        description=_text(raw, "description", 300, required=False),
        license=_text(raw, "license", 40),
        menu=menu,
        ui_module=module,
    )
