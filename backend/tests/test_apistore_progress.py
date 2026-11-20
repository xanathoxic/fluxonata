"""Progression d'une exécution pour un consommateur de l'API store : étapes passées, total, étape en cours, états. Aucune fuite."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apistore.progress import progress
from runs.models import StepRun, WorkflowRun
from tests.apistore_helpers import BASE, active_key, call
from tests.conftest import client_for

S = StepRun.Status
NODES = [
    {"id": i, "type": "action", "name": n}
    for i, n in (("a", "Valider"), ("b", "Notifier"), ("c", "Archiver"), ("d", "Clore"))
]
M = "/api/v1/plugins/apistore/manage/store"


@pytest.fixture
def run(store):
    _, _, raw = active_key(store)
    r = WorkflowRun.objects.get(pk=call(raw).json()["run_id"])
    r.steps.all().delete()
    r.definition = {**r.definition, "graph": {**r.definition["graph"], "nodes": NODES}}
    r.status = WorkflowRun.Status.RUNNING
    r.save()
    r.raw = raw
    return r


def step(run, node_id, status, ago=0, **extra):
    s = StepRun.objects.create(run=run, node_id=node_id, node_type="action", status=status, **extra)
    StepRun.objects.filter(pk=s.pk).update(started_at=timezone.now() - timedelta(seconds=ago))
    return s


def test_it_counts_passed_steps_names_the_current_one_and_gives_both_states(run):
    step(run, "a", S.SUCCEEDED, ago=30)
    step(run, "b", S.SKIPPED, ago=20)  # branche non prise : compte comme passée
    step(run, "c", S.WAITING, ago=10)
    p = progress(run)
    assert p["steps_done"] == 2 and p["steps_total"] == 4 and p["also_active"] == 0
    assert p["current_step"] == {"name": "Archiver", "status": "waiting", "status_label": "En attente"}
    assert p["workflow_status"] == "running" and p["workflow_status_label"] == "En cours"


def test_a_step_that_has_not_started_is_neither_done_nor_current(run):
    p = progress(run)
    assert p["steps_done"] == 0 and p["steps_total"] == 4 and p["current_step"] is None


def test_with_parallel_steps_the_oldest_is_shown_and_the_others_are_counted(run):
    step(run, "b", S.RUNNING, ago=5)
    step(run, "a", S.RETRY_WAIT, ago=50)
    step(run, "c", S.RUNNING, ago=1)
    p = progress(run)
    assert p["current_step"]["name"] == "Valider" and p["current_step"]["status"] == "retry_wait"
    assert p["also_active"] == 2


def test_a_failed_run_shows_the_step_that_failed_and_a_finished_one_shows_none(run):
    step(run, "a", S.SUCCEEDED, ago=30)
    step(run, "b", S.FAILED, ago=20)
    WorkflowRun.objects.filter(pk=run.pk).update(status="failed")
    run.refresh_from_db()
    p = progress(run)
    assert p["current_step"] == {"name": "Notifier", "status": "failed", "status_label": "Échouée"}
    assert p["steps_done"] == 1 and p["workflow_status"] == "failed"
    for node_id in "cd":
        step(run, node_id, S.SKIPPED)
    StepRun.objects.filter(run=run, node_id="b").update(status="succeeded")
    WorkflowRun.objects.filter(pk=run.pk).update(status="succeeded")
    run.refresh_from_db()
    p = progress(run)
    assert (
        p["steps_done"] == p["steps_total"] == 4
        and p["current_step"] is None
        and p["workflow_status_label"] == "Réussie"
    )


def test_a_step_missing_from_the_definition_falls_back_to_its_identifier(run):
    step(run, "inconnue", S.RUNNING)
    assert progress(run)["current_step"]["name"] == "inconnue"


def test_the_status_endpoint_returns_progress_and_never_a_step_value_or_error(run):
    secret = "valeur-sensible-987"
    step(run, "a", S.SUCCEEDED, ago=20, output={"jeton": secret})
    step(run, "b", S.WAITING, ago=5, error={"message": secret})
    r = APIClient().get(f"{BASE}/store/p1/runs/{run.pk}/", HTTP_AUTHORIZATION=f"Bearer {run.raw}")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"run_id", "status", "outputs", "finished", "progress"}
    assert body["progress"]["current_step"]["name"] == "Notifier" and body["progress"]["steps_done"] == 1
    assert body["status"] == body["progress"]["workflow_status"] == "running" and body["finished"] is False
    assert set(body["progress"]) == {
        "steps_done",
        "steps_total",
        "current_step",
        "also_active",
        "workflow_status",
        "workflow_status_label",
    }
    assert secret not in json.dumps(body)


def test_the_documentation_describes_the_progress_fields_and_both_examples(store):
    d = client_for(store["asker"]).get(f"{M}/products/p1/docs/").json()
    status = next(e for e in d["endpoints"] if e["id"] == "run-status")
    names = {f[0] for f in status["response_fields"]}
    assert {
        "status",
        "finished",
        "outputs",
        "progress.steps_done",
        "progress.steps_total",
        "progress.current_step",
    } <= names
    assert all(isinstance(f[1], str) and f[1] for f in status["response_fields"])
    assert [r["example"]["status"] for r in status["responses"] if r["status"] == 200] == ["running", "succeeded"]
    o = client_for(store["asker"]).get(f"{M}/products/p1/docs/openapi/").json()
    ok = o["paths"]["/api/v1/plugins/apistore/v1/store/p1/runs/{run_id}/"]["get"]["responses"]["200"]
    assert set(ok["content"]["application/json"]["examples"]) == {"cas1", "cas2"}
