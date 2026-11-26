"""Rôles Ansible de la bibliothèque (ADR 0037, lot 6) : validation des fichiers, versions figées, dépendances, et fichiers remis au runner.

Un rôle est un ensemble de fichiers TEXTE rangés comme Ansible l'attend (`tasks/main.yml` obligatoire ; `handlers`, `defaults`, `vars`, `meta` en YAML ;
`templates` et `files` libres). Une action déclare les rôles qu'elle utilise avec leur version ; la plate-forme dépose ces rôles dans `/work/roles/<nom>/` au
moment de l'exécution (`support_files`). Seul le code déclaré arrive : aucun rôle n'est cherché ailleurs, et aucun n'est téléchargé.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from rest_framework import serializers

from .models import AnsibleRole, AnsibleRoleVersion, files_digest  # noqa: F401 - files_digest est ré-exporté pour les appelants du module

ROLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MAX_FILES = 200
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_ROLES_PER_ACTION = 20
MAX_SUPPORT_BYTES = 4 * 1024 * 1024  # tous les rôles d'une exécution
MAX_DEPTH = 6
YAML_DIRS = ("tasks", "handlers", "defaults", "vars", "meta")
FREE_DIRS = ("templates", "files")  # modèles Jinja et fichiers à copier : texte, contenu libre
PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
SECRET_VALUE = re.compile(
    r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})"
)


def semver_key(v: str) -> tuple[int, int, int]:
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def _bad(field: str, message: str) -> serializers.ValidationError:
    return serializers.ValidationError({field: message})


def check_path(path: object) -> str:
    """Un chemin relatif au dossier du rôle, sans échappement possible : jamais absolu, jamais `..`, jamais `\\`, jamais un lien ni un fichier caché."""
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 200
        or "\\" in path
        or "\x00" in path
        or path.startswith("/")
    ):
        raise _bad("files", f"Chemin invalide : « {str(path)[:60]} ».")
    parts = path.split("/")
    if len(parts) > MAX_DEPTH or not all(PART.match(p) and p not in (".", "..") for p in parts):
        raise _bad(
            "files",
            f"Chemin invalide : « {path[:60]} » (lettres, chiffres, point, tiret et souligné ; {MAX_DEPTH} niveaux au plus).",
        )
    return path


def _check_one(path: str, content: object) -> None:
    top = path.split("/")[0]
    if top not in YAML_DIRS + FREE_DIRS or "/" not in path:
        allowed = ", ".join(YAML_DIRS + FREE_DIRS)
        raise _bad("files", f"« {path} » : un fichier d'un rôle se range dans l'un de ces dossiers : {allowed}.")
    if not isinstance(content, str):
        raise _bad("files", f"« {path} » : un texte est attendu.")
    if len(content.encode()) > MAX_FILE_BYTES:
        raise _bad("files", f"« {path} » dépasse {MAX_FILE_BYTES // 1024} Ko.")
    if "\x00" in content:
        raise _bad("files", f"« {path} » n'est pas un fichier texte.")
    if SECRET_VALUE.search(content):
        raise _bad("files", f"« {path} » semble contenir un secret : utilisez une connexion et une variable.")
    if top in YAML_DIRS:
        if not path.endswith((".yml", ".yaml")):
            raise _bad("files", f"« {path} » : les fichiers de « {top} » sont en YAML (.yml).")
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise _bad("files", f"« {path} » : YAML invalide ({str(exc)[:100]}).") from None


def check_files(files: object) -> dict[str, str]:
    """Valide l'ensemble des fichiers d'une version de rôle et les renvoie ; lève une erreur DRF lisible sinon."""
    if not isinstance(files, dict) or not files:
        raise _bad("files", "Un rôle contient au moins le fichier tasks/main.yml.")
    if len(files) > MAX_FILES:
        raise _bad("files", f"{MAX_FILES} fichiers au plus.")
    total = 0
    for path, content in files.items():
        check_path(path)
        _check_one(path, content)
        total += len(content.encode())
    if total > MAX_TOTAL_BYTES:
        raise _bad("files", f"Un rôle fait {MAX_TOTAL_BYTES // 1024} Ko au plus (ici {total // 1024} Ko).")
    if "tasks/main.yml" not in files and "tasks/main.yaml" not in files:
        raise _bad("files", "Il manque tasks/main.yml : c'est le point d'entrée d'un rôle.")
    return dict(files)


def dependencies(files: dict) -> list[str]:
    """Les rôles dont celui-ci dépend (`meta/main.yml`, `dependencies:`), par nom."""
    meta = files.get("meta/main.yml") or files.get("meta/main.yaml")
    if not meta:
        return []
    try:
        data = yaml.safe_load(meta) or {}
    except yaml.YAMLError:
        return []
    out = []
    for dep in (data.get("dependencies") if isinstance(data, dict) else None) or []:
        name = dep.get("role") or dep.get("name") if isinstance(dep, dict) else dep
        if isinstance(name, str):
            out.append(name)
    return out


def _pick_version(workspace, item: object, seen: set[str]) -> AnsibleRoleVersion:
    """La version exacte d'un rôle déclaré : « latest » devient la plus récente publiée."""
    name = item.get("role") if isinstance(item, dict) else None
    wanted = item.get("version", "latest") if isinstance(item, dict) else None
    if not isinstance(name, str) or not ROLE_NAME.match(name) or not isinstance(wanted, str):
        raise _bad("roles", 'Chaque rôle s\'écrit {"role": "nom", "version": "1.0.0"}.')
    if name in seen:
        raise _bad("roles", f"Le rôle « {name} » est déclaré deux fois.")
    role = AnsibleRole.objects.filter(workspace=workspace, name=name).first()
    if role is None:
        raise _bad("roles", f"Rôle introuvable dans cet espace : « {name} ».")
    existing = list(role.versions.all())
    if wanted == "latest":
        chosen = max(existing, key=lambda v: semver_key(v.version), default=None)
    else:
        chosen = next((v for v in existing if v.version == wanted), None)
    if chosen is None:
        raise _bad("roles", f"Le rôle « {name} » n'a pas de version « {wanted} ».")
    return chosen


def resolve_declared(workspace, declared: object) -> list[dict]:
    """Valide les rôles qu'une action déclare (`[{"role", "version"}]`) et FIGE leurs versions. Chaque rôle doit exister et ne figurer qu'une fois, et ses
    dépendances (`meta/main.yml`) doivent figurer aussi parmi les rôles déclarés. Renvoie la liste à enregistrer."""
    if declared in (None, []):
        return []
    if not isinstance(declared, list) or len(declared) > MAX_ROLES_PER_ACTION:
        raise _bad("roles", f"Une liste de {MAX_ROLES_PER_ACTION} rôles au plus est attendue.")
    versions: dict[str, AnsibleRoleVersion] = {}
    for item in declared:
        chosen = _pick_version(workspace, item, set(versions))
        versions[chosen.role.name] = chosen
    for name, v in versions.items():
        missing = [d for d in dependencies(v.files) if d not in versions]
        if missing:
            raise _bad("roles", f"Le rôle « {name} » dépend de « {missing[0]} » : déclarez-le aussi.")
    return [{"role": name, "version": v.version} for name, v in versions.items()]


def support_files(action_version) -> dict[str, str]:
    """Les fichiers à déposer dans le répertoire de travail d'une exécution : `roles/<nom>/<chemin>` pour chaque rôle figé de la version d'action.
    Lève `serializers.ValidationError` si un rôle a disparu (il ne peut pas : les rôles utilisés sont protégés) ou si l'ensemble est trop gros."""
    declared = action_version.ansible_roles or []
    if not declared:
        return {}
    out: dict[str, str] = {}
    workspace = action_version.action.workspace
    for item in declared:
        v = AnsibleRoleVersion.objects.filter(
            role__workspace=workspace, role__name=item["role"], version=item["version"]
        ).first()
        if v is None:
            raise _bad("roles", f"Rôle « {item['role']} » {item['version']} introuvable.")
        for path, content in v.files.items():
            out[f"roles/{item['role']}/{path}"] = content
    if sum(len(c.encode()) for c in out.values()) > MAX_SUPPORT_BYTES:
        raise _bad("roles", "Les rôles de cette action dépassent la taille autorisée.")
    return out


def roles_in_use(role: AnsibleRole) -> int:
    """Combien de versions d'action ET de versions de workflow (étapes « Playbook Ansible ») de l'espace déclarent ce rôle : elles le protègent contre la
    suppression. Les versions cachées que crée l'exécution d'un playbook (`__playbook__`, `__essai__`) ne comptent pas : le workflow publié fait foi."""
    from workflows.models import WorkflowVersion

    from .models import ActionVersion

    actions = sum(
        1
        for versions in ActionVersion.objects.filter(action__workspace=role.workspace)
        .exclude(ansible_roles=[])
        .exclude(action__slug__startswith="__")
        .values_list("ansible_roles", flat=True)
        if any(r.get("role") == role.name for r in versions)
    )
    workflows = sum(
        1
        for graph in WorkflowVersion.objects.filter(workflow__workspace=role.workspace).values_list("graph", flat=True)
        if any(
            n.get("type") == "ansible" and any(r.get("role") == role.name for r in n.get("roles") or [])
            for n in (graph or {}).get("nodes", [])
        )
    )
    return actions + workflows


# --- contrat d'usage du rôle ---------------------------------------------------------------------------------------------------------------------

CONTRACT_TYPES = ("str", "int", "float", "bool", "list", "dict", "path")
VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
MAX_VARIABLES = 60
MAX_OUTPUTS = 30


def _text(raw: object, field: str, limit: int) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str) or len(raw) > limit:
        raise _bad("contract", f"« {field} » : un texte de {limit} caractères au plus est attendu.")
    return raw.strip()


def _named_rows(raw: object, field: str, limit: int) -> list[dict]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > limit or not all(isinstance(r, dict) for r in raw):
        raise _bad("contract", f"« {field} » : une liste de {limit} éléments au plus est attendue.")
    names = [r.get("name") for r in raw]
    for n in names:
        if not isinstance(n, str) or not VAR_NAME.match(n):
            raise _bad("contract", f"« {field} » : nom invalide « {str(n)[:40]} » (lettres, chiffres et soulignés).")
    if len(set(names)) != len(names):
        raise _bad("contract", f"« {field} » : un nom figure deux fois.")
    return raw


def check_contract(raw: object) -> dict:
    """Valide et nettoie le contrat d'usage d'un rôle : `summary` (à quoi il sert, une phrase), `description`, `variables` (nom, type, obligatoire, valeur par défaut,
    description), `outputs` (ce qu'il publie : faits, résultats enregistrés) et `example` (un playbook qui l'appelle). Un contrat vide est permis (rôles plus anciens)."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise _bad("contract", "Le contrat doit être un objet.")
    variables = []
    for v in _named_rows(raw.get("variables"), "variables", MAX_VARIABLES):
        kind = v.get("type", "str")
        if kind not in CONTRACT_TYPES:
            raise _bad("contract", f"Variable « {v['name']} » : type inconnu ({', '.join(CONTRACT_TYPES)}).")
        default = v.get("default")
        if default is not None and len(str(default)) > 2000:
            raise _bad("contract", f"Variable « {v['name']} » : valeur par défaut trop longue.")
        variables.append(
            {
                "name": v["name"],
                "type": kind,
                "required": bool(v.get("required", False)),
                **({"default": default} if default is not None else {}),
                "description": _text(v.get("description"), f"description de {v['name']}", 500),
            }
        )
    outputs = [
        {"name": o["name"], "description": _text(o.get("description"), f"description de {o['name']}", 500)}
        for o in _named_rows(raw.get("outputs"), "outputs", MAX_OUTPUTS)
    ]
    clean: dict[str, Any] = {
        "summary": _text(raw.get("summary"), "summary", 200),
        "description": _text(raw.get("description"), "description", 4000),
        "variables": variables,
        "outputs": outputs,
        "example": _text(raw.get("example"), "example", 4000),
    }
    for field in ("summary", "description", "example"):
        if SECRET_VALUE.search(clean[field]):
            raise _bad("contract", f"« {field} » semble contenir un secret : ne l'écrivez pas ici.")
    return clean


def contract_problems(contract: dict) -> list[str]:
    """Ce qu'il manque pour qu'on comprenne comment utiliser le rôle (conseil, ne bloque pas la publication)."""
    out: list[str] = []
    if not (contract or {}).get("summary"):
        out.append("Dites en une phrase à quoi sert le rôle.")
    for v in (contract or {}).get("variables", []):
        if not v.get("description"):
            out.append(f"Décrivez la variable « {v['name']} ».")
    return out


def draft_contract(files: dict) -> dict:
    """Une ébauche de contrat tirée de `defaults/main.yml` : une variable par clé, son type et sa valeur par défaut. À compléter par la personne."""
    try:
        data = yaml.safe_load(files.get("defaults/main.yml", "") or "") or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    kinds = {bool: "bool", int: "int", float: "float", list: "list", dict: "dict", str: "str"}
    rows = []
    for name, value in list(data.items())[:MAX_VARIABLES]:
        if not isinstance(name, str) or not VAR_NAME.match(name):
            continue
        row: dict = {"name": name, "type": kinds.get(type(value), "str"), "required": False, "description": ""}
        if isinstance(value, (str, int, float, bool, list, dict)) and len(str(value)) <= 2000:
            row["default"] = value
        rows.append(row)
    return {"variables": rows}
