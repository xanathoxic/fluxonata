from django.conf import settings
from django.db import models

from . import crypto


class Secret(models.Model):
    """Encrypted value. The plaintext is never returned by the API nor written to logs/audit."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="secrets")
    name = models.SlugField()
    ciphertext = models.TextField()
    key_id = models.CharField(max_length=16)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="uniq_secret_name")]

    PURPOSE = "secret"

    def set_value(self, plaintext: str) -> None:
        self.key_id, self.ciphertext = crypto.encrypt(plaintext, f"{self.PURPOSE}:{self.workspace_id}")

    def reveal(self) -> str:
        """Server-side only (injection into an authorized action)."""
        return crypto.decrypt(self.ciphertext, f"{self.PURPOSE}:{self.workspace_id}")


class Connection(models.Model):
    """A configured link to an external system: public settings + a reference to a secret."""

    class Kind(models.TextChoices):
        HTTP_BEARER = "http_bearer", "Service HTTP (jeton)"
        GENERIC = "generic", "Générique"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="connections")
    name = models.SlugField()
    label = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.GENERIC)
    config = models.JSONField(default=dict, blank=True)  # non-secret settings (e.g. base_url)
    secret = models.ForeignKey(Secret, null=True, blank=True, on_delete=models.SET_NULL, related_name="connections")
    environment = models.ForeignKey(
        "tenancy.Environment", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_test_ok = models.BooleanField(null=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_message = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name", "environment"], name="uniq_connection")]
