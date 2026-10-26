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
    secret_ciphertext = models.TextField()
    key_id = models.CharField(max_length=16)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def _purpose(self) -> str:
        return f"webhook:{self.workspace_id}"

    def set_secret(self, plaintext: str) -> None:
        self.key_id, self.secret_ciphertext = crypto.encrypt(plaintext, self._purpose())

    def reveal_secret(self) -> str:
        return crypto.decrypt(self.secret_ciphertext, self._purpose())


class WebhookDelivery(models.Model):
    """Replay protection: a (webhook, delivery id) pair is accepted once."""

    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    delivery_id = models.CharField(max_length=128)
    received_at = models.DateTimeField(auto_now_add=True)
    run = models.ForeignKey("runs.WorkflowRun", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["webhook", "delivery_id"], name="uniq_webhook_delivery")]
