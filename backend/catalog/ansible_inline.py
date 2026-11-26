"""L'étape « Playbook Ansible » d'un workflow (ADR 0037, mise à jour du 2026-11-26) : le playbook est écrit DANS le workflow, comme le code d'une étape script,
et appelle des rôles de la bibliothèque dont les versions sont figées à la publication du workflow.

Une étape est une action « en ligne » : à l'exécution, ses champs deviennent une version d'action RÉELLE rangée sous l'action interne masquée `__playbook__`
(`hidden_version`), puis tout suit le chemin des actions Ansible : runner portant les étiquettes exigées, inventaire lu au démarrage, secrets par les
connexions, rôles déposés dans le répertoire de travail. Le contenu fait l'identité : un même playbook réutilise la même version."""

from __future__ import annotations

from rest_framework import serializers

from . import ansible_roles
from .models import ActionVersion
from .versions import VersionInput, hidden_version

PLAYBOOK_SLUG = "__playbook__"
DEFAULT_TIMEOUT = 300

INPUT_SCHEMA: dict = {
    "type": "object",
    "required": ["known_hosts"],
    "properties": {
        "inventory_ref": {
            "type": "string",
            "title": "Inventaire",
            "description": "Identifiant d'un inventaire de la bibliothèque (lu au démarrage de l'exécution). Donnez celui-ci OU « inventory ».",
        },
        "inventory": {
            "type": "object",
            "title": "Inventaire (objet)",
            "description": "Un inventaire Ansible en objet (all / hosts / children / vars), par exemple construit par un script. Donnez celui-ci OU « inventory_ref ».",
        },
        "known_hosts": {
            "type": "string",
            "title": "Clés d'hôtes",
            "description": "Lignes known_hosts des machines : la connexion SSH refuse toute machine qui n'y figure pas.",
        },
        "user": {"type": "string", "title": "Compte SSH", "description": "Le compte de connexion sur les machines."},
        "check": {
            "type": "boolean",
            "title": "Mode vérification",
            "description": "Vrai : Ansible dit ce qui changerait, sans rien modifier.",
        },
        "diff": {
            "type": "boolean",
            "title": "Afficher les différences",
            "description": "Vrai : montre le contenu modifié des fichiers.",
        },
        "limit": {
            "type": "string",
            "title": "Limiter aux machines",
            "description": "Motif d'hôtes Ansible ; vide pour toutes les machines.",
        },
        "tags": {
            "type": "array",
            "title": "Étiquettes jouées",
            "description": "Ne joue que les tâches portant l'une de ces étiquettes Ansible.",
        },
        "skip_tags": {
            "type": "array",
            "title": "Étiquettes ignorées",
            "description": "Ne joue pas les tâches portant l'une de ces étiquettes Ansible.",
        },
        "become": {
            "type": "boolean",
            "title": "Élévation de privilèges",
            "description": "Vrai : les tâches s'exécutent avec les droits d'un autre compte (sudo).",
        },
        "become_user": {
            "type": "string",
            "title": "Compte d'élévation",
            "description": "Le compte dont on prend les droits (root par défaut).",
        },
        "forks": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "title": "Machines en parallèle",
            "description": "Nombre de machines traitées en même temps (1 à 10).",
        },
        "timeout": {
            "type": "integer",
            "minimum": 5,
            "maximum": 120,
            "title": "Délai de connexion",
            "description": "Secondes avant de déclarer une machine injoignable (5 à 120).",
        },
        "extra_vars": {
            "type": "object",
            "title": "Variables",
            "description": "Variables transmises au playbook (par exemple les variables des rôles).",
        },
        "fail_on_error": {
            "type": "boolean",
            "title": "Échouer sur erreur",
            "description": "Vrai (défaut) : une machine en échec ou injoignable fait échouer l'étape ; faux : le résultat complet est rendu et le workflow décide.",
        },
    },
}
OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "ok": {
            "type": "boolean",
            "title": "Réussi",
            "description": "Vrai si aucune machine n'est en échec ou injoignable.",
        },
        "check": {"type": "boolean", "title": "Mode vérification", "description": "Vrai si rien n'a été modifié."},
        "hosts": {"type": "integer", "title": "Machines", "description": "Nombre de machines jouées."},
        "stats": {
            "type": "object",
            "title": "Bilan par machine",
            "description": "Ok, modifiées, échecs et injoignables pour chaque machine.",
        },
        "totals": {"type": "object", "title": "Totaux", "description": "Le bilan de toutes les machines ensemble."},
        "tasks": {
            "type": "array",
            "title": "Tâches",
            "description": "Une ligne par tâche et par machine : statut, changement, message.",
        },
        "data": {
            "type": "object",
            "title": "Données publiées",
            "description": "Les valeurs publiées par ansible.builtin.set_stats.",
        },
    },
}
NODE_KEYS = (
    "playbook",
    "roles",
    "required_tags",
    "auth",
    "connections",
    "idempotent",
    "timeout_seconds",
    "inputs",
)


def version_data(node: dict) -> dict:
    """Les champs d'une version d'action (ce que reçoit `VersionInput`) que décrit l'étape."""
    conns = node.get("connections") or {}
    secrets = []
    if "ssh" in conns:
        env = "FLX_SSH_PASSWORD" if node.get("auth") == "password" else "FLX_SSH_KEY_B64"
        secrets.append({"slot": "ssh", "env": env})
    if "become" in conns:
        secrets.append({"slot": "become", "env": "FLX_BECOME_PASSWORD"})
    try:
        timeout = int(node.get("timeout_seconds") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {
        "runtime": "ansible",
        "source": node.get("playbook") or "",
        "input_schema": INPUT_SCHEMA,
        "output_schema": OUTPUT_SCHEMA,
        "side_effects": "external",
        "idempotent": bool(node.get("idempotent", False)),
        "network_policy": "targets",
        "required_tags": node.get("required_tags") or [],
        "ansible_roles": node.get("roles") or [],
        "secrets_required": secrets,
        "limits": {"timeout_seconds": timeout, "memory_mb": 256},
    }


def validated(node: dict) -> dict:
    """Les champs de l'étape passés par les MÊMES contrôles qu'une version d'action (playbook YAML, étiquettes de runner obligatoires, limites)."""
    ser = VersionInput(data=version_data(node))
    ser.is_valid(raise_exception=True)
    return ser.validated_data


def problems(workspace, node: dict) -> list[tuple[str, str]]:
    """Ce qui empêche l'étape de s'exécuter : (code, message en français). Rien n'est écrit."""
    out: list[tuple[str, str]] = []
    if not str(node.get("playbook") or "").strip():
        return [("empty_playbook", "Le playbook est vide.")]
    try:
        data = validated(node)
    except serializers.ValidationError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"": exc.detail}
        for field, msgs in detail.items():
            for msg in msgs if isinstance(msgs, list) else [msgs]:
                code = (
                    "bad_playbook"
                    if field == "source"
                    else "missing_runner_tags"
                    if field == "required_tags"
                    else "bad_ansible"
                )
                out.append((code, str(msg)))
        return out
    try:
        ansible_roles.resolve_declared(workspace, data.get("ansible_roles"))
    except serializers.ValidationError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"": exc.detail}
        for msgs in detail.values():
            out.extend(("bad_role", str(m)) for m in (msgs if isinstance(msgs, list) else [msgs]))
    return out


def pin_roles(workspace, node: dict) -> list[dict]:
    """Les rôles de l'étape avec leur version EXACTE (« latest » devient la plus récente publiée) ; vide s'il n'y en a pas."""
    return ansible_roles.resolve_declared(workspace, node.get("roles") or [])


def inline_version(workspace, user, node: dict) -> ActionVersion:
    return hidden_version(
        workspace, user, PLAYBOOK_SLUG, "Playbooks de workflows", validated(node), "Playbook d'un workflow"
    )
