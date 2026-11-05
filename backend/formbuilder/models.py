from django.conf import settings
from django.db import models


def empty_definition() -> dict:
    return {"version": 1, "sections": [{"id": "s1", "title": "Informations", "fields": []}]}


class Form(models.Model):
    """A form of the library: a mutable draft plus immutable published versions (`FormVersion`), like a workflow. A form
    is attached to a workflow, a public form or an action by naming one of its versions; `is_template` marks the ones that
    are starting points to copy (they are not attached)."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="library_forms")
    slug = models.SlugField()
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, max_length=2000)
    folder = models.CharField(max_length=200, blank=True, db_index=True)
    tags = models.JSONField(default=list, blank=True)  # see core.tags
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    is_template = models.BooleanField(default=False)
    draft_definition = models.JSONField(default=empty_definition)
    draft_revision = models.PositiveIntegerField(default=1)  # optimistic concurrency token
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_form_slug")]
        ordering = ["name", "id"]

    def latest_version(self):
        return max(self.versions.all(), key=lambda v: tuple(int(x) for x in v.version.split(".")), default=None)


class FormVersion(models.Model):
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=20)
    definition = models.JSONField()
    digest = models.CharField(max_length=80)
    changelog = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["form", "version"], name="uniq_form_version")]
        ordering = ["-created_at", "-id"]


def empty_spec() -> dict:
    return {"version": 1, "mode": "group", "parts": []}


class Control(models.Model):
    """A custom control of the library: a reusable kind of field made of other fields (a group, repeated lines, a list of
    boxes with details…). A mutable draft plus immutable published versions, like a form; the form editor offers the
    published ones, and a form field keeps a copy of the version it uses (so a published form never changes by itself)."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="library_controls")
    slug = models.SlugField()
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, max_length=2000)
    icon = models.CharField(max_length=40, blank=True, default="custom")
    folder = models.CharField(max_length=200, blank=True, db_index=True)
    tags = models.JSONField(default=list, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    draft_spec = models.JSONField(default=empty_spec)
    draft_revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_control_slug")]
        ordering = ["name", "id"]

    def latest_version(self):
        return max(self.versions.all(), key=lambda v: tuple(int(x) for x in v.version.split(".")), default=None)


class ControlVersion(models.Model):
    control = models.ForeignKey(Control, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=20)
    name = models.CharField(max_length=160)
    icon = models.CharField(max_length=40, blank=True, default="custom")
    spec = models.JSONField()
    digest = models.CharField(max_length=80)
    changelog = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["control", "version"], name="uniq_control_version")]
        ordering = ["-created_at", "-id"]
