from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Application-level append-only audit trail (not tamper-proof against a DB admin)."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=150, blank=True)
    workspace = models.ForeignKey("tenancy.Workspace", null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    data = models.JSONField(default=dict, blank=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("AuditEvent is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditEvent is append-only")


def record(event, *, actor=None, workspace=None, target=None, **data):
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=getattr(actor, "username", "") or "",
        workspace=workspace,
        action=event,
        target_type=type(target).__name__ if target is not None else "",
        target_id=str(getattr(target, "pk", "")) if target is not None else "",
        data=data,
    )
