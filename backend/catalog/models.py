import hashlib
import json

from django.conf import settings
from django.db import models


class Action(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEPRECATED = "deprecated", "Dépréciée"
        # ADR 0025 : proposée par un assistant externe, jamais exécutable ni publiable avant relecture humaine (activation)
        PROPOSED = "proposed", "Proposée"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="actions")
    slug = models.SlugField()
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    domain = models.CharField(max_length=80, blank=True)
    folder = models.CharField(max_length=200, blank=True)  # "Dossier/Sous-dossier": a tree of folders, like workflows
    tags = models.JSONField(default=list, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    deprecation_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_action_slug")]
        ordering = ["name", "id"]

    def latest_version(self):
        """Highest semantic version (not the most recently created: imports may add older versions)."""
        return max(self.versions.all(), key=lambda v: tuple(int(x) for x in v.version.split(".")), default=None)


class ActionVersion(models.Model):
    """Published, immutable version of an action (the mutable draft comes with P2)."""

    class SideEffects(models.TextChoices):
        NONE = "none", "Aucun"
        EXTERNAL = "external", "Effet externe"

    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=32)  # semver
    runtime = models.CharField(max_length=20, default="python")  # python | node | typescript | shell | powershell
    entrypoint = models.CharField(max_length=80, default="run")
    source = models.TextField()
    source_digest = models.CharField(max_length=71, editable=False)
    input_schema = models.JSONField()
    output_schema = models.JSONField()
    license = models.CharField(max_length=40, default="Apache-2.0")
    side_effects = models.CharField(max_length=10, choices=SideEffects.choices, default=SideEffects.NONE)
    idempotent = models.BooleanField(default=True)
    network_policy = models.CharField(max_length=20, default="none")
    limits = models.JSONField(default=dict)  # timeout_seconds, memory_mb
    examples = models.JSONField(default=list, blank=True)
    # connection slots the action needs: [{"slot": "api", "env": "API_TOKEN"}]; secrets are injected only for these
    secrets_required = models.JSONField(default=list, blank=True)
    # runner tags a worker must carry to run this version (e.g. ["windows"]); empty = any worker
    required_tags = models.JSONField(default=list, blank=True)
    # rôles Ansible (ADR 0037, lot 6) : [{"role": "web", "version": "1.2.0"}], versions EXACTES figées à la publication ; seulement pour le runtime « ansible »
    ansible_roles = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    changelog = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["action", "version"], name="uniq_action_version")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("ActionVersion is immutable once published")
        self.source_digest = "sha256:" + hashlib.sha256(self.source.encode()).hexdigest()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("ActionVersion is immutable once published")


class ActionTestCase(models.Model):
    """A saved set of inputs for an action ("test set"): offered when running a trial. Never holds secrets."""

    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name="test_cases")
    name = models.CharField(max_length=100)
    inputs = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["action", "name"], name="uniq_action_testcase_name")]
        ordering = ["name", "id"]


class Inventory(models.Model):
    """Un inventaire Ansible géré par la plate-forme (ADR 0037, lot 3) : des machines, des groupes (imbriqués) et des variables.
    Jamais d'identifiant dedans : les secrets passent par les connexions de l'action (voir `catalog.inventory.check_vars`)."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="inventories")
    slug = models.SlugField()
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    folder = models.CharField(
        max_length=200, blank=True, default=""
    )  # « A/B » : le rangement dans l'arborescence des inventaires
    variables = models.JSONField(default=dict, blank=True)  # variables communes à toutes les machines (groupe « all »)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_inventory_slug")]
        ordering = ["name", "id"]


class InventoryGroup(models.Model):
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="groups")
    name = models.CharField(max_length=64)
    variables = models.JSONField(default=dict, blank=True)
    children = models.ManyToManyField("self", symmetrical=False, related_name="parents", blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["inventory", "name"], name="uniq_inventory_group")]
        ordering = ["name", "id"]


class InventoryHost(models.Model):
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="hosts")
    name = models.CharField(max_length=255)
    variables = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(
        default=True
    )  # une machine désactivée reste dans l'inventaire mais n'est jamais passée à Ansible
    groups = models.ManyToManyField(InventoryGroup, related_name="hosts", blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["inventory", "name"], name="uniq_inventory_host")]
        ordering = ["name", "id"]


def files_digest(files: dict) -> str:
    """L'empreinte d'un ensemble de fichiers de rôle : indépendante de l'ordre des fichiers."""
    return "sha256:" + hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class AnsibleRole(models.Model):
    """Un rôle Ansible de la bibliothèque (ADR 0037, lot 6) : un nom que les playbooks appellent (`roles: [web]`) et des versions immuables."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="ansible_roles")
    name = models.CharField(max_length=50)  # le nom du dossier du rôle : lettres minuscules, chiffres, soulignés
    description = models.TextField(blank=True)
    folder = models.CharField(
        max_length=200, blank=True, default=""
    )  # « A/B » : le rangement dans l'arborescence des rôles
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="uniq_ansible_role_name")]
        ordering = ["name", "id"]


class AnsibleRoleVersion(models.Model):
    """Version publiée, immuable, d'un rôle : ses fichiers (texte), leur empreinte et ce qui a changé."""

    role = models.ForeignKey(AnsibleRole, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=32)  # semver
    files = models.JSONField()  # {"tasks/main.yml": "texte", …} : voir `catalog.ansible_roles.check_files`
    digest = models.CharField(max_length=71, editable=False)
    changelog = models.CharField(max_length=300, blank=True)
    # le contrat d'usage du rôle (à quoi il sert, variables, résultats, exemple) : voir `catalog.ansible_roles.check_contract` ; immuable comme les fichiers
    contract = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "version"], name="uniq_ansible_role_version")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("AnsibleRoleVersion is immutable once published")
        self.digest = files_digest(self.files)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AnsibleRoleVersion is immutable once published")
