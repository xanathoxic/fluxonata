import secrets
import uuid

from django.conf import settings
from django.db import models

from vault import crypto


class Schedule(models.Model):
    class Overlap(models.TextChoices):
        SKIP = "skip", "Ignorer si la précédente tourne encore"
        ALLOW = "allow", "Autoriser le chevauchement"

    class Missed(models.TextChoices):
        SKIP = "skip", "Ignorer les occurrences manquées"
        RUN_ONCE = "run_once", "Rattraper une seule fois"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="schedules")
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="schedules")
    environment = models.ForeignKey("tenancy.Environment", on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=120)
    cron = models.CharField(max_length=100)
    timezone = models.CharField(max_length=64, default="Europe/Paris")
    inputs = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    overlap_policy = models.CharField(max_length=8, choices=Overlap.choices, default=Overlap.SKIP)
    missed_policy = models.CharField(max_length=10, choices=Missed.choices, default=Missed.SKIP)
    grace_seconds = models.PositiveIntegerField(default=300)
    next_run_at = models.DateTimeField(null=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run = models.ForeignKey("runs.WorkflowRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class Webhook(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="webhooks")
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="webhooks")
    environment = models.ForeignKey("tenancy.Environment", on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=120)
    enabled = models.BooleanField(default=True)
    provider = models.CharField(max_length=12, default="fluxonata")  # fluxonata | github | gitlab | jira | token
    events = models.JSONField(default=list, blank=True)  # provider event names to accept (empty = all)
    secret_ciphertext = models.TextField(blank=True, default="")
    key_id = models.CharField(max_length=16, blank=True, default="")
    # a secret of the workspace (typically held by the external vault) used instead of a generated one
    external_secret = models.ForeignKey(
        "vault.Secret", null=True, blank=True, on_delete=models.PROTECT, related_name="webhooks"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def _purpose(self) -> str:
        return f"webhook:{self.workspace_id}"

    def set_secret(self, plaintext: str) -> None:
        self.key_id, self.secret_ciphertext = crypto.encrypt(plaintext, self._purpose())

    def reveal_secret(self) -> str:
        if self.external_secret_id:
            return self.external_secret.reveal()
        return crypto.decrypt(self.secret_ciphertext, self._purpose())


class WebhookDelivery(models.Model):
    """Replay protection: a (webhook, delivery id) pair is accepted once."""

    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    delivery_id = models.CharField(max_length=128)
    received_at = models.DateTimeField(auto_now_add=True)
    run = models.ForeignKey("runs.WorkflowRun", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["webhook", "delivery_id"], name="uniq_webhook_delivery")]


def _form_token() -> str:
    return secrets.token_urlsafe(24)


class PublicForm(models.Model):
    """A public page that starts a workflow from a form. The address is a capability (unguessable token); the page shows
    only what the form needs, never the workflow's name or internals. Abuse is bounded by a per-form hourly cap, a
    per-address throttle, a body limit and a hidden trap field."""

    token = models.CharField(max_length=48, unique=True, default=_form_token, editable=False)
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="public_forms")
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="public_forms")
    environment = models.ForeignKey("tenancy.Environment", on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=120)  # internal label
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True, max_length=2000)
    submit_label = models.CharField(max_length=60, default="Envoyer")
    success_message = models.CharField(max_length=300, default="Merci : votre demande a bien été envoyée.")
    enabled = models.BooleanField(default=True)
    max_per_hour = models.PositiveIntegerField(default=60)
    # availability: the form switches itself off at `expires_at` and after `max_uses` accepted submissions (null = no limit)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    uses = models.PositiveIntegerField(default=0)
    closed_reason = models.CharField(max_length=10, blank=True)  # "expired" or "exhausted" once it switched itself off
    # the library form version this page serves (null = the legacy page, built from the workflow's inputs). Versions are
    # immutable, so `published_definition` is a snapshot that always equals `library_version.definition`.
    library_version = models.ForeignKey(
        "formbuilder.FormVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    published_definition = models.JSONField(null=True, blank=True)
    # which field of the library form feeds which input of the workflow ({input: field key}); empty = fields and inputs share their names
    input_map = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class FormSubmission(models.Model):
    form = models.ForeignKey(PublicForm, on_delete=models.CASCADE, related_name="submissions")
    run = models.ForeignKey("runs.WorkflowRun", null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class FormLookup(models.Model):
    """One dynamic-list lookup of a public form, run by the WORKER (never by the web process, which only queues it and
    reads the outcome). Short-lived: the rows are swept after a few minutes, and a recent identical lookup is reused."""

    class Status(models.TextChoices):
        QUEUED = "queued", "En attente"
        RUNNING = "running", "En cours"
        DONE = "done", "Terminé"
        FAILED = "failed", "Échec"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # a lookup belongs either to a public form (anonymous visitor) or to a workflow version (a signed-in person launching
    # it from the catalog or the run page: `requested_by`, in `environment`)
    form = models.ForeignKey(PublicForm, null=True, blank=True, on_delete=models.CASCADE, related_name="lookups")
    workflow_version = models.ForeignKey(
        "workflows.WorkflowVersion", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    environment = models.ForeignKey(
        "tenancy.Environment", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    field_key = models.CharField(max_length=64)
    # "options": the choices of a dynamic list · "prefill": the whole value of a custom control (`options` then holds that value)
    kind = models.CharField(max_length=8, default="options")
    params = models.JSONField(default=dict)
    params_hash = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED, db_index=True)
    options = models.JSONField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True)
    claimed_by = models.CharField(max_length=80, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class PollTrigger(models.Model):
    """Starts a workflow when a web service shows NEW items. A worker thread calls the service (through a connection,
    like the « Requête HTTP » step) every `interval_seconds`, finds the items it has not seen (by an id read in each
    item) and starts one run per item (or one run for the batch). The first poll only memorises what is already there,
    unless `fire_existing`."""

    class Mode(models.TextChoices):
        PER_ITEM = "per_item", "Une exécution par nouvel élément"
        BATCH = "batch", "Une exécution pour tous les nouveaux éléments"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="poll_triggers")
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="poll_triggers")
    environment = models.ForeignKey("tenancy.Environment", on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=120)
    source = models.CharField(max_length=6, default="http")  # http | imap
    request = (
        models.JSONField()
    )  # an « http » step description (connection, integration/operation/params or method/path…)
    interval_seconds = models.PositiveIntegerField(default=300)
    items_path = models.CharField(max_length=120, blank=True)
    id_path = models.CharField(max_length=120)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.PER_ITEM)
    fire_existing = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    seen_ids = models.JSONField(default=list, blank=True)
    baselined = models.BooleanField(default=False)
    preview_next = models.BooleanField(default=False)  # next poll only reports what it would start
    next_poll_at = models.DateTimeField(null=True, db_index=True)
    last_poll_at = models.DateTimeField(null=True, blank=True)
    last_ok = models.BooleanField(null=True)
    last_note = models.CharField(max_length=300, blank=True)
    claimed_until = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
