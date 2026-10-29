import hashlib

from django.conf import settings
from django.db import models


class Action(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEPRECATED = "deprecated", "Dépréciée"

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
