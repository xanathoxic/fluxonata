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
    tags = models.JSONField(default=list, blank=True)  # see core.tags
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    draft_graph = models.JSONField(default=empty_graph)
    draft_revision = models.PositiveIntegerField(default=1)  # optimistic concurrency token
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    in_catalog = models.BooleanField(default=False)  # finished product offered in « Catalogue d'automatisations »
    deprecated = models.BooleanField(default=False)
    deprecation_reason = models.CharField(max_length=300, blank=True)
    # slug of the workflow started (same environment, same author) when a run of this one fails
    error_workflow = models.SlugField(blank=True, default="")
    # slug of the workflow started when a run of this one SUCCEEDS (same environment, same author)
    success_workflow = models.SlugField(blank=True, default="")
    # offered as a tool to MCP clients (assistants, agents) through the workspace's MCP endpoint
    mcp_exposed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_workflow_slug")]
        ordering = ["name", "id"]  # a unique tie-break: paginated lists must not skip or repeat equal names

    def latest_version(self):
        return max(self.versions.all(), key=lambda v: tuple(int(x) for x in v.version.split(".")), default=None)


class FolderAccess(models.Model):
    """A PRIVATE folder: inside it (and its sub-folders, unless they have their own) only the people listed in `grants` — and
    the workspace's administrators and space managers — see or use the workflows. A grant never exceeds the person's role
    in the workspace: it can only narrow it (see `workflows.access`)."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="folder_accesses")
    folder = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "folder"], name="uniq_folder_access")]


class FolderGrant(models.Model):
    class Level(models.TextChoices):
        VIEW = "view", "Voir"
        RUN = "run", "Voir et lancer / approuver"
        EDIT = "edit", "Voir, lancer, modifier et publier"

    access = models.ForeignKey(FolderAccess, on_delete=models.CASCADE, related_name="grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    level = models.CharField(max_length=8, choices=Level.choices, default=Level.VIEW)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["access", "user"], name="uniq_folder_grant")]


class GitRepo(models.Model):
    """The Git repository a workspace synchronises its workflows with (GitHub or GitLab, through their web API). The access
    token lives in a vault connection (type « Service HTTP », address = the provider's API), never here."""

    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GITLAB = "gitlab", "GitLab"

    workspace = models.OneToOneField("tenancy.Workspace", on_delete=models.CASCADE, related_name="git_repo")
    provider = models.CharField(max_length=10, choices=Provider.choices)
    repo = models.CharField(max_length=200)  # "owner/name" (GitHub) or "group/project" (GitLab)
    branch = models.CharField(max_length=100, default="main")
    path = models.CharField(
        max_length=100, default="workflows"
    )  # directory of the repository holding `<slug>.json` files
    connection = models.CharField(max_length=100)  # name of the vault connection that carries the token
    updated_at = models.DateTimeField(auto_now=True)


class GitJob(models.Model):
    """One Git operation, run by a worker (never in the web process): `push` opens a pull/merge request with the changed
    workflows, `pull_preview` / `pull_apply` read the branch and show / import it as drafts, `check` tests the access."""

    class Status(models.TextChoices):
        QUEUED = "queued", "En attente"
        RUNNING = "running", "En cours"
        DONE = "done", "Terminée"
        FAILED = "failed", "Échouée"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="git_jobs")
    kind = models.CharField(max_length=14)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED, db_index=True)
    params = models.JSONField(default=dict)
    result = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]


class StepComment(models.Model):
    """A remark left on a step of a workflow (review, question, to-do). Internal to the workspace."""

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="comments")
    node_id = models.CharField(max_length=64, db_index=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    text = models.TextField(max_length=2000)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class WorkflowTemplate(models.Model):
    """A workflow draft kept as a model for the whole workspace."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="workflow_templates")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    graph = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


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
