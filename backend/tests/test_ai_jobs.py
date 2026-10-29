"""Draft generation as a job: created by the API, run by the worker, followed live (progress + log)."""

from datetime import timedelta

from django.utils import timezone

from ai import jobs
from ai.models import AiJob, AiPolicy
from tests.conftest import client_for, make_user
from tests.test_ai import BASE, ai_ws, cfg  # noqa: F401
from workflows.models import Workflow

URL = f"{BASE}/ai/draft-jobs/"
BODY = {
    "request": "Compter les mots d'un texte puis attendre",
    "name": "Essai job",
    "slug": "essai-job",
    "provider": "fake",
}


def test_creating_a_job_is_immediate_and_validated(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    r = c.post(URL, BODY, format="json")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert AiJob.objects.get(pk=r.json()["id"]).payload["request"] == BODY["request"]
    assert c.post(URL, {**BODY, "request": ""}, format="json").status_code == 400
    assert c.post(URL, {**BODY, "slug": "mauvais slug!"}, format="json").status_code == 400
    Workflow.objects.create(workspace=ws, slug="existe", name="x")
    assert c.post(URL, {**BODY, "slug": "existe"}, format="json").status_code == 400
    assert client_for(world["viewer"]).post(URL, BODY, format="json").status_code == 403  # editing right required
    AiPolicy.objects.filter(workspace=ws).update(mode="off")
    assert c.post(URL, {**BODY, "slug": "autre"}, format="json").status_code in (
        400,
        403,
    )  # refused now, not as a failed job


def test_the_worker_runs_the_job_and_the_log_tells_the_phases_in_order(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    job_id = c.post(URL, BODY, format="json").json()["id"]
    job = jobs.claim_next("w1")
    assert str(job.id) == job_id and job.status == "running" and jobs.claim_next("w2") is None  # claimed once
    jobs.process(job)
    out = c.get(f"{URL}{job_id}/").json()
    assert out["status"] == "succeeded" and out["progress"] == 100 and out["error"] is None
    phases = [e["phase"] for e in out["events"]]
    assert phases[:2] == ["queued", "context"] and phases.index("plan") < phases.index("build") < phases.index(
        "save"
    ) < phases.index("done")
    assert (
        all(e["state"] in ("done", "running", "warning") for e in out["events"][:-1])
        and out["events"][-1]["state"] == "done"
    )
    assert out["result"]["workflow"] == "essai-job" and out["result"]["graph"]["nodes"] and "usage" in out["result"]
    assert Workflow.objects.filter(workspace=ws, slug="essai-job").exists()  # the draft is stored, nothing published
    assert AiJob.objects.get(pk=job_id).payload == {
        "slug": "essai-job",
        "name": "Essai job",
    }  # the request text is not kept


def test_progress_only_goes_forward_and_a_phase_that_goes_on_is_updated_not_repeated(ai_ws, world):
    ws, _ = ai_ws
    job = jobs.enqueue_draft(ws, world["designer"], "x", "s1", "n", None)
    from ai import progress

    token = progress.install(jobs._sink(job))
    try:
        progress.report("dryrun", "scénario 1/3")
        progress.report("dryrun", "scénario 2/3")
        progress.report("review")
    finally:
        progress.uninstall(token)
    events = AiJob.objects.get(pk=job.pk).events
    assert [e["phase"] for e in events] == ["queued", "dryrun", "review"] and events[1]["detail"] == "scénario 2/3"
    assert events[1]["state"] == "done" and events[2]["state"] == "running"  # the previous phase is closed


def test_a_failure_ends_the_job_with_a_readable_error(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    job_id = c.post(URL, BODY, format="json").json()["id"]
    Workflow.objects.create(workspace=ws, slug="essai-job", name="pris entre-temps")
    jobs.process(jobs.claim_next("w1"))
    out = c.get(f"{URL}{job_id}/").json()
    assert out["status"] == "failed" and out["error"]["code"] == "SLUG_EXISTS" and out["events"][-1]["state"] == "error"
    assert out["result"] is None


def test_only_its_author_can_follow_a_job_and_a_vanished_worker_is_reported(ai_ws, world):
    ws, _ = ai_ws
    job_id = client_for(world["designer"]).post(URL, BODY, format="json").json()["id"]
    other = make_user("autre", ws)
    assert client_for(other).get(f"{URL}{job_id}/").status_code == 404
    job = jobs.claim_next("w1")
    AiJob.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=30))
    jobs.housekeeping()
    out = client_for(world["designer"]).get(f"{URL}{job_id}/").json()
    assert out["status"] == "failed" and out["error"]["code"] == "INTERRUPTED"
    AiJob.objects.filter(pk=job.pk).update(finished_at=timezone.now() - timedelta(days=2))
    jobs.housekeeping()
    assert not AiJob.objects.filter(pk=job.pk).exists()  # old jobs are removed


def test_the_policy_exposes_and_changes_the_dry_run_switch(ai_ws, world):
    ws, _ = ai_ws
    admin = make_user("admin-ia", ws, role="admin")
    c = client_for(admin)
    assert c.get(f"{BASE}/ai/policy/").json()["dry_run"] is False  # this fixture turns it off
    r = c.put(f"{BASE}/ai/policy/", {"mode": "local_strict", "dry_run": True}, format="json")
    assert r.status_code == 200 and c.get(f"{BASE}/ai/policy/").json()["dry_run"] is True


# ------------------------------------------------------------------------------------------------ cancel and the tray
def test_a_queued_generation_can_be_cancelled_and_nobody_runs_it(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    job_id = c.post(URL, BODY, format="json").json()["id"]
    r = c.post(f"{URL}{job_id}/cancel/")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert jobs.claim_next("w1") is None  # not claimed
    out = c.get(f"{URL}{job_id}/").json()
    assert out["status"] == "cancelled" and "aucun brouillon" in out["error"]["message"]
    assert c.post(f"{URL}{job_id}/cancel/").json()["status"] == "cancelled"  # idempotent


def test_a_running_generation_stops_at_its_next_phase_and_saves_nothing(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    job_id = c.post(URL, BODY, format="json").json()["id"]
    job = jobs.claim_next("w1")  # the worker has it...
    c.post(f"{URL}{job_id}/cancel/")  # ...and the person cancels
    jobs.process(job)
    out = c.get(f"{URL}{job_id}/").json()
    assert out["status"] == "cancelled" and out["result"] is None
    assert out["events"][-1]["phase"] == "cancelled" and "Rien n'a été enregistré" in out["events"][-1]["detail"]
    assert not Workflow.objects.filter(workspace=ws, slug="essai-job").exists()
    assert AiJob.objects.get(pk=job_id).payload == {"slug": "essai-job", "name": "Essai job"}


def test_a_finished_generation_cannot_be_cancelled_and_only_its_author_can_cancel(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    job_id = c.post(URL, BODY, format="json").json()["id"]
    jobs.process(jobs.claim_next("w1"))
    assert c.post(f"{URL}{job_id}/cancel/").json()["status"] == "succeeded"  # nothing to cancel any more
    other = make_user("intrus", ws)
    assert client_for(other).post(f"{URL}{job_id}/cancel/").status_code == 404


def test_the_tray_lists_the_persons_recent_generations_with_where_they_are(ai_ws, world):
    ws, _ = ai_ws
    c = client_for(world["designer"])
    done = c.post(URL, BODY, format="json").json()["id"]
    jobs.process(jobs.claim_next("w1"))
    running = c.post(URL, {**BODY, "slug": "deuxieme", "name": "Deuxième"}, format="json").json()["id"]
    jobs.claim_next("w1")
    other = make_user("autre2", ws)
    client_for(other).post(URL, {**BODY, "slug": "pas-a-moi"}, format="json")
    rows = c.get(URL).json()["results"]
    by = {r["id"]: r for r in rows}
    assert set(by) == {done, running}  # not somebody else's
    assert by[running]["status"] == "running" and by[running]["name"] == "Deuxième"
    assert by[done]["status"] == "succeeded" and by[done]["workflow"] == "essai-job" and by[done]["progress"] == 100
