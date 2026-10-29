import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from audit.models import AuditEvent, record
from runs.models import Artifact, Execution, WorkflowRun
from tests.test_artifacts import MAKE_REPORT, run_demo  # noqa: F401
from tests.wf_helpers import drain, launch, make_action, node, publish


@pytest.fixture(autouse=True)
def _store(settings, tmp_path):
    settings.ORCH_ARTIFACTS_DIR = str(tmp_path / "artifacts")


def old(qs, days):
    qs.update(finished_at=timezone.now() - timedelta(days=days))


def test_dry_run_deletes_nothing_and_apply_removes_old_runs_and_files(world, capsys):
    ws = world["ws_a"]
    make_action(ws, "echo")
    v = publish(ws, "r", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    old_run = drain(launch(ws, v, world["designer"]))
    new_run = drain(launch(ws, v, world["designer"]))
    ex = run_demo(world, MAKE_REPORT, {"title": "t", "lines": ["x"]})
    old(WorkflowRun.objects.filter(pk=old_run.pk), 200)
    old(Execution.objects.filter(pk=ex.pk), 200)
    key = Artifact.objects.get().storage_key
    from runs.artifacts import get_store

    call_command("prune_data", "--days", "90")
    assert WorkflowRun.objects.count() == 2 and get_store().get(key)  # dry run
    call_command("prune_data", "--days", "90", "--apply")
    assert list(WorkflowRun.objects.values_list("pk", flat=True)) == [new_run.pk]
    assert not Artifact.objects.exists()
    with pytest.raises(FileNotFoundError):
        get_store().get(key)
    assert Execution.objects.filter(step_run__run=new_run).exists()  # recent data untouched
    assert AuditEvent.objects.filter(action="retention.prune").exists()


def test_running_runs_are_never_pruned(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    v = publish(ws, "r2", [node("w", "wait", seconds=3600)], [], user=world["designer"])
    run = launch(ws, v, world["designer"])
    WorkflowRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=500))
    call_command("prune_data", "--days", "1", "--apply")
    assert WorkflowRun.objects.filter(pk=run.pk).exists()


def test_audit_is_only_deleted_after_export(world, tmp_path):
    ws = world["ws_a"]
    for i in range(3):
        record("x.test", workspace=ws, n=i)
    AuditEvent.objects.update(created_at=timezone.now() - timedelta(days=400))
    with pytest.raises(CommandError, match="audit-export"):
        call_command("prune_data", "--days", "30", "--audit-days", "365")
    out = tmp_path / "audit.ndjson"
    call_command("prune_data", "--days", "30", "--audit-days", "365", "--audit-export", str(out), "--apply")
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 3 and lines[0]["action"] == "x.test"  # exported first...
    assert not AuditEvent.objects.filter(action="x.test").exists()  # ...then deleted
    assert AuditEvent.objects.filter(action="retention.prune").exists()  # the pruning itself is audited
