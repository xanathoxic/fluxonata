import hashlib
import json

from django.conf import settings
from django.db import models

EMPTY_GRAPH = {
    "inputs": {"type": "object", "properties": {}, "required": []},
    "variables": [],  # read-only workflow constants, available as vars.<name>
    "form": None,  # optional step-by-step input form definition
    "notes": [],  # visual annotations (never executed)
    "nodes": [],
    "edges": [],
    "outputs": {},
}


def empty_graph():
    return json.loads(json.dumps(EMPTY_GRAPH))


class Workflow(models.Model):
    """Mutable draft container. Published versions are immutable (WorkflowVersion)."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="workflows")
    slug = models.SlugField()
    name = models.CharField(max_length=160)
    folder = models.CharField(max_length=200, blank=True, db_index=True)  # "Dossier/Sous-dossier": a tree of folders
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    draft_graph = models.JSONField(default=empty_graph)
    draft_revision = models.PositiveIntegerField(default=1)  # optimistic concurrency token
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    in_catalog = models.BooleanField(default=False)  # finished product offered in « Catalogue d'automatisations »
    deprecated = models.BooleanField(default=False)
    deprecation_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_workflow_slug")]
        ordering = ["name"]

    def latest_version(self):
        return max(self.versions.all(), key=lambda v: tuple(int(x) for x in v.version.split(".")), default=None)


class WorkflowVersion(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=32)
    graph = models.JSONField()  # pinned: every action node carries an exact version
    input_schema = models.JSONField()
    digest = models.CharField(max_length=71, editable=False)
    changelog = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workflow", "version"], name="uniq_workflow_version")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("WorkflowVersion is immutable once published")
        self.digest = "sha256:" + hashlib.sha256(json.dumps(self.graph, sort_keys=True).encode()).hexdigest()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("WorkflowVersion is immutable once published")


class Favorite(models.Model):
    class Kind(models.TextChoices):
        WORKFLOW = "workflow", "Workflow"
        ACTION = "action", "Action"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites")
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    slug = models.SlugField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "workspace", "kind", "slug"], name="uniq_favorite")]
