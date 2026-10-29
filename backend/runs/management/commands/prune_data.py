import json
import os
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Retention: delete finished runs/executions (and their artifact files) older than N days. "
        "Audit events are only deleted after being exported to an NDJSON file. Dry run unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, required=True, help="Delete finished runs and executions older than this"
        )
        parser.add_argument(
            "--audit-days", type=int, help="Also delete audit events older than this (requires --audit-export)"
        )
        parser.add_argument("--audit-export", help="NDJSON file receiving the audit events before deletion")
        parser.add_argument("--apply", action="store_true", help="Actually delete (default: only report)")

    def handle(self, days, audit_days, audit_export, apply, **opts):
        from audit.models import AuditEvent, record
        from runs.artifacts import get_store
        from runs.models import Artifact, Execution, WorkflowRun

        if days < 1 or (audit_days is not None and audit_days < 1):
            raise CommandError("Les durées doivent être d'au moins 1 jour.")
        if audit_days is not None and not audit_export:
            raise CommandError(
                "--audit-days exige --audit-export : l'audit n'est jamais supprimé sans export préalable."
            )
        cutoff = timezone.now() - timedelta(days=days)
        runs = WorkflowRun.objects.filter(
            status__in=["succeeded", "failed", "cancelled"], finished_at__lt=cutoff, parent_step__isnull=True
        )
        execs = Execution.objects.filter(
            status__in=["succeeded", "failed", "cancelled"], finished_at__lt=cutoff, step_run__isnull=True
        )
        step_execs = Execution.objects.filter(step_run__run__in=runs)
        arts = Artifact.objects.filter(
            execution__in=list(execs.values_list("pk", flat=True)) + list(step_execs.values_list("pk", flat=True))
        )
        counts = {"workflow_runs": runs.count(), "executions": execs.count(), "artifacts": arts.count()}
        audit_qs = None
        if audit_days is not None:
            audit_qs = AuditEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=audit_days))
            counts["audit_events"] = audit_qs.count()
        self.stdout.write(("Suppression : " if apply else "Simulation (rien n'est supprimé) : ") + json.dumps(counts))
        if not apply:
            return
        store = get_store()
        for a in arts:
            store.delete(a.storage_key)
        if audit_qs is not None:
            with open(audit_export, "a", encoding="utf-8") as fh:
                for ev in audit_qs.order_by("id").iterator(chunk_size=500):
                    fh.write(
                        json.dumps(
                            {
                                "id": ev.id,
                                "created_at": ev.created_at.isoformat(),
                                "actor": ev.actor_label,
                                "workspace_id": ev.workspace_id,
                                "action": ev.action,
                                "target_type": ev.target_type,
                                "target_id": ev.target_id,
                                "data": ev.data,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                fh.flush()
                os.fsync(fh.fileno())
            audit_qs.delete()  # queryset delete bypasses the model's append-only guard: deliberate, only after the export above
        arts.delete()
        execs.delete()
        runs.delete()
        record("retention.prune", **counts, days=days)
        self.stdout.write("Terminé.")
