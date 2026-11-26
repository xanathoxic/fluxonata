"""Ce que partagent la bibliothèque d'actions et l'étape « Playbook Ansible » d'un workflow : la forme et les contrôles d'une VERSION d'action (`VersionInput`) et les
versions cachées que crée l'exécution. Feuille : aucun écran ni route ici, pour que le moteur et la validation des workflows puissent s'en servir sans cycle d'imports."""

import ast
import hashlib
import json
import re

import jsonschema
import yaml
from rest_framework import serializers

from runs.runners import NETWORK_POLICIES, RUNTIMES

from . import ansible_roles
from .models import Action, ActionVersion

TAG_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,29}")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MAX_SOURCE = 200 * 1024
SECRET_LIKE = re.compile(
    r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})"
)


def semver_key(v: str):
    return tuple(int(x) for x in v.split("."))


def bump(latest: str | None, kind: str) -> str:
    if latest is None:
        return "1.0.0"
    a, b, c = semver_key(latest)
    return {"major": f"{a + 1}.0.0", "minor": f"{a}.{b + 1}.0", "patch": f"{a}.{b}.{c + 1}"}[kind]


def _check_runtime(attrs: dict) -> None:
    """Contrôles propres au langage de l'action, sans jamais exécuter son code : syntaxe Python, ou playbook et étiquettes d'une action Ansible."""
    if attrs["runtime"] == "python":
        try:
            ast.parse(attrs["source"])
        except SyntaxError as exc:
            raise serializers.ValidationError({"source": f"Erreur de syntaxe ligne {exc.lineno} : {exc.msg}"}) from None
    elif attrs["runtime"] == "ansible":
        _check_ansible(attrs)
    if attrs["runtime"] != "ansible" and attrs.get("ansible_roles"):
        raise serializers.ValidationError(
            {"ansible_roles": "Des rôles Ansible ne se déclarent que pour une action de langage « ansible »."}
        )


def _check_ansible(attrs: dict) -> None:
    """Un playbook ne part jamais sur n'importe quel runner (ADR 0037) : l'action doit exiger au moins une étiquette, que seuls portent les runners (ou les workers
    intégrés, `ORCH_WORKER_TAGS`) choisis pour joindre les machines gérées. Le playbook est lu (sans rien exécuter) : une liste non vide de jeux."""
    tags = attrs.get("required_tags") or []
    if not (isinstance(tags, list) and tags and all(isinstance(t, str) and t.strip() for t in tags)):
        raise serializers.ValidationError(
            {
                "required_tags": "Une action Ansible exige au moins une étiquette de runner (ex. « ansible » ou « site-paris ») : seuls les runners qui la portent la jouent."
            }
        )
    try:
        plays = yaml.safe_load(attrs["source"])
    except yaml.YAMLError as exc:
        raise serializers.ValidationError({"source": f"Playbook YAML invalide : {str(exc)[:150]}"}) from None
    if not (isinstance(plays, list) and plays and all(isinstance(p, dict) and "hosts" in p for p in plays)):
        raise serializers.ValidationError(
            {"source": "Un playbook est une liste de jeux (« - hosts: … ») ; chaque jeu indique ses hôtes."}
        )


class VersionInput(serializers.Serializer):
    version = serializers.CharField(required=False)
    bump = serializers.ChoiceField(choices=["patch", "minor", "major"], default="minor")
    runtime = serializers.ChoiceField(choices=sorted(RUNTIMES), default="python")
    entrypoint = serializers.RegexField(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", default="run")
    # `source` est le nom du champ du contrat d'API ; il masque l'attribut `source` de DRF `Field` (str | None), d'où l'exemption ciblée.
    source = serializers.CharField(max_length=MAX_SOURCE, trim_whitespace=False)  # type: ignore[assignment]
    input_schema = serializers.JSONField()
    output_schema = serializers.JSONField()
    license = serializers.CharField(max_length=40, default="Apache-2.0")
    side_effects = serializers.ChoiceField(choices=["none", "external"], default="none")
    idempotent = serializers.BooleanField(default=True)
    network_policy = serializers.ChoiceField(choices=sorted(NETWORK_POLICIES), default="none")
    limits = serializers.JSONField(default=dict)
    examples = serializers.JSONField(default=list)
    secrets_required = serializers.JSONField(default=list)
    required_tags = serializers.JSONField(default=list)
    ansible_roles = serializers.JSONField(
        default=list
    )  # [{"role": "web", "version": "1.2.0"}] : seulement pour le runtime « ansible » (ADR 0037, lot 6)
    changelog = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")

    def _given(self) -> dict:
        """Ce que l'appelant a envoyé pour CETTE version : le serializer seul a `initial_data` ; imbriqué dans la création d'une action, c'est le parent qui l'a."""
        data = getattr(self, "initial_data", None)
        if data is None and self.parent is not None:
            data = (getattr(self.parent, "initial_data", None) or {}).get(self.field_name)
        return data if isinstance(data, dict) else {}

    def validate_version(self, value):
        if not SEMVER.match(value):
            raise serializers.ValidationError("Version sémantique attendue (ex. 1.2.0).")
        return value

    def validate_source(self, value):
        if SECRET_LIKE.search(value):
            raise serializers.ValidationError("Le code semble contenir un secret : utilisez une connexion.")
        return value

    def validate(self, attrs):
        for key in ("input_schema", "output_schema"):
            schema = attrs[key]
            try:
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise jsonschema.SchemaError("Le schéma doit décrire un objet (type: object).")
                jsonschema.Draft202012Validator.check_schema(schema)
            except jsonschema.SchemaError as exc:
                raise serializers.ValidationError({key: f"Schéma invalide : {exc.message[:150]}"}) from None
        _check_runtime(attrs)
        limits = {"timeout_seconds": 30, "memory_mb": 128, **attrs["limits"]}
        if not (1 <= limits["timeout_seconds"] <= 3600 and 16 <= limits["memory_mb"] <= 2048):
            raise serializers.ValidationError({"limits": "Délai : 1 à 3600 s ; mémoire : 16 à 2048 Mo."})
        attrs["limits"] = {k: limits[k] for k in ("timeout_seconds", "memory_mb")}
        for slot in attrs["secrets_required"]:
            if not (
                isinstance(slot, dict)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,30}", str(slot.get("slot", "")))
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,40}", str(slot.get("env", "")))
            ):
                raise serializers.ValidationError(
                    {"secrets_required": "Chaque entrée : {slot: minuscules, env: MAJUSCULES}."}
                )
        tags = attrs["required_tags"]
        if not (
            isinstance(tags, list) and len(tags) <= 5 and all(isinstance(t, str) and TAG_RE.fullmatch(t) for t in tags)
        ):
            raise serializers.ValidationError(
                {"required_tags": "Jusqu'à 5 étiquettes (minuscules, chiffres, - _ .), 30 caractères au plus."}
            )
        attrs["required_tags"] = sorted(set(tags))
        if attrs["side_effects"] == "external" and attrs["idempotent"] and "idempotent" not in self._given():
            attrs["idempotent"] = False  # external effects are non-idempotent unless stated otherwise
        return attrs


TRIAL_SLUG = (
    "__essai__"  # action interne (masquée des listes, comme les tâches de script) qui porte les versions d'essai
)


def hidden_version(workspace, user, slug: str, name: str, data, changelog: str) -> ActionVersion:
    """Une version RÉELLE d'action (une exécution en exige une) rangée sous une action interne masquée (`__essai__` pour les essais de brouillon, `__playbook__`
    pour les playbooks écrits dans un workflow), jamais sous l'action d'une personne. Son numéro est `0.0.<entier tiré de l'empreinte du contenu>` (semver
    strict) : un même contenu réutilise la même ligne, la liste reste bornée. Les rôles Ansible sont figés comme à la publication d'une action."""
    action, _ = Action.objects.get_or_create(
        workspace=workspace, slug=slug, defaults={"name": name, "description": "Intégré"}
    )
    fields = {
        k: data[k]
        for k in (
            "runtime",
            "entrypoint",
            "source",
            "input_schema",
            "output_schema",
            "license",
            "side_effects",
            "idempotent",
            "network_policy",
            "limits",
            "secrets_required",
            "required_tags",
        )
    }
    fields["ansible_roles"] = ansible_roles.resolve_declared(workspace, data.get("ansible_roles"))
    key = int(hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()[:12], 16)
    version = f"0.0.{key}"
    return action.versions.filter(version=version).first() or ActionVersion.objects.create(
        action=action, version=version, created_by=user, changelog=changelog, **fields
    )


def trial_version(workspace, user, data) -> ActionVersion:
    """La version d'ESSAI d'un brouillon (aucun contrat complet n'est exigé : on teste avant de publier)."""
    return hidden_version(workspace, user, TRIAL_SLUG, "Essais de brouillons", data, "Essai d'un brouillon")
