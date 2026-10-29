"""Le tour de l'assistant de l'éditeur : l'essai à blanc est exécuté par le travailleur (job), jamais par le processus web."""

import pytest

from ai import jobs, service
from ai.models import AiJob, AiPolicy
from tests.conftest import client_for
from tests.test_ai import BASE, ai_ws  # noqa: F401
from tests.test_ai_edit import body
from workflows.models import Workflow

SLUG = "statistiques-de-texte"
EDIT = f"{BASE}/workflows/{SLUG}/ai/edit/"
JOB = f"{BASE}/workflows/{SLUG}/ai/edit-jobs/"


class NoSandbox:
    """What the API container is: no Docker socket, no work directory. Counts every attempt to execute something."""

    attempts = 0

    def run(self, *args, **kwargs):
        NoSandbox.attempts += 1
        raise RuntimeError("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")


def test_the_web_process_never_starts_the_sandbox(ai_ws, world, monkeypatch):
    NoSandbox.attempts = 0
    monkeypatch.setattr(service, "RUNNER_FACTORY", NoSandbox)
    r = client_for(world["designer"]).post(EDIT, body(), format="json")
    assert r.status_code == 200 and NoSandbox.attempts == 0  # nothing was executed in the web process
    d = r.json()
    assert d["graph"] is not None and d.get("dryrun") == "skipped"
    assert "exécuté par le travailleur" in d["reply"] and "bac à sable indisponible" not in d["reply"]


def test_the_job_is_created_validated_and_run_by_the_worker(ai_ws, world):
    c = client_for(world["designer"])
    r = c.post(JOB, body(), format="json")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job_id = r.json()["id"]
    assert AiJob.objects.get(pk=job_id).kind == "edit"
    assert c.get(f"{BASE}/ai/draft-jobs/").json()["results"] == []  # the tray lists draft generations only
    job = jobs.claim_next("w1")
    jobs.process(job)
    out = c.get(f"{BASE}/ai/draft-jobs/{job_id}/").json()
    assert out["status"] == "succeeded" and out["progress"] == 100 and out["error"] is None
    assert out["result"]["graph"]["nodes"] and out["result"]["changes"] and "usage" in out["result"]
    assert out["result"].get("dryrun") != "skipped"  # the worker does run it
    stored = AiJob.objects.get(pk=job_id).payload
    assert "graph" not in stored and "message" not in stored  # neither the request nor the workflow is kept
    wf = Workflow.objects.get(workspace=ai_ws[0], slug=SLUG)
    assert wf.draft_graph != out["result"]["graph"]  # nothing is stored: the person applies it in the editor


def test_job_endpoint_rights_and_validation(ai_ws, world):
    d = client_for(world["designer"])
    assert client_for(world["viewer"]).post(JOB, body(), format="json").status_code == 403
    assert d.post(f"{BASE}/workflows/inconnu/ai/edit-jobs/", body(), format="json").status_code == 404
    assert d.post(JOB, {**body(), "message": " "}, format="json").status_code == 400
    assert d.post(JOB, {**body(), "message": "x" * 6001}, format="json").status_code == 400
    assert d.post(JOB, {**body(), "graph": "pas un objet"}, format="json").status_code == 400
    assert client_for(world["outsider"]).post(JOB, body(), format="json").status_code in (403, 404)
    other = client_for(world["outsider"])
    job_id = d.post(JOB, body(), format="json").json()["id"]
    assert other.get(f"{BASE}/ai/draft-jobs/{job_id}/").status_code in (403, 404)  # someone else's job


def test_a_broken_sandbox_says_why_and_is_logged(ai_ws, world, monkeypatch, caplog):
    monkeypatch.setattr(service, "RUNNER_FACTORY", NoSandbox)
    AiPolicy.objects.filter(workspace=ai_ws[0]).update(dry_run=True)
    c = client_for(world["designer"])
    job_id = c.post(JOB, body(), format="json").json()["id"]
    jobs.process(jobs.claim_next("w1"))
    out = c.get(f"{BASE}/ai/draft-jobs/{job_id}/").json()
    assert out["status"] == "succeeded"
    reply = out["result"]["reply"]
    assert "bac à sable indisponible" in reply and "RuntimeError" in reply and "Docker daemon" in reply
    assert "le lanceur d'actions est indisponible" in caplog.text


@pytest.mark.parametrize("path", ["", "jobs"])
def test_both_paths_share_the_same_rules_for_a_disabled_assistant(ai_ws, world, path):
    AiPolicy.objects.filter(workspace=ai_ws[0]).update(mode="off")
    url = EDIT if not path else JOB
    assert client_for(world["designer"]).post(url, body(), format="json").status_code in (400, 403)


def test_following_a_job_is_not_limited_like_model_calls(ai_ws, world, monkeypatch):
    """The editor polls the job every second: the « ai » limit (model calls) must not cut the conversation short."""
    from django.core.cache import cache
    from rest_framework.throttling import ScopedRateThrottle

    cache.clear()
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"ai": "3/min", "login": "10/min"})
    c = client_for(world["designer"])
    job_id = c.post(JOB, body(), format="json").json()["id"]
    assert [c.get(f"{BASE}/ai/draft-jobs/{job_id}/").status_code for _ in range(12)] == [200] * 12
    assert c.get(f"{BASE}/ai/draft-jobs/").status_code == 200
    assert c.post(f"{BASE}/ai/draft-jobs/{job_id}/cancel/", {}, format="json").status_code == 200
    codes = [c.post(JOB, body(), format="json").status_code for _ in range(4)]
    assert 429 in codes  # a model call is still limited
