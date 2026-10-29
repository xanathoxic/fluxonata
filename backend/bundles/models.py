from django.conf import settings
from django.db import models


class Package(models.Model):
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="packages")
    slug = models.SlugField()
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_package_slug")]


class PackageVersion(models.Model):
    """Immutable record of an exported/imported archive (kept in the artifact store)."""

    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=32)
    manifest = models.JSONField()
    checksum = models.CharField(max_length=71)
    archive_key = models.CharField(max_length=200)
    origin = models.CharField(max_length=10, default="export")  # export | import
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["package", "version"], name="uniq_package_version")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("PackageVersion is immutable")
        super().save(*args, **kwargs)


class TrustedKey(models.Model):
    """Public key of a package publisher that this workspace trusts."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="trusted_keys")
    name = models.CharField(max_length=120)
    public_key = models.CharField(max_length=100)
    key_id = models.CharField(max_length=32)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "public_key"], name="uniq_trusted_key")]
