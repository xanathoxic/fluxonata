import pytest

from runs.models import WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, edge, launch, make_action, node, publish, schema
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def _handler(ws, user, slug="alerte"):
    return publish(
        ws,
        slug,
        [node("n", action="echo", inputs={"value": "{{ inputs.message }}"})],
        [],
        inputs=schema(["message"], message={"type": "string"}),
        user=user,
    )


def test_failed_run_starts_the_error_workflow_with_the_facts(ws, world):
    user = world["designer"]
    _handler(ws, user)
    v = publish(ws, "fragile", [node("s", action="boom")], [], user=user)
    Workflow.objects.filter(workspace=ws, slug="fragile").update(error_workflow="alerte")
    run = drain(launch(ws, v, user))
    assert run.status == "failed"
    child = WorkflowRun.objects.get(workspace=ws, workflow_version__workflow__slug="alerte")
    assert "boom" in child.inputs["message"] and child.definition["trigger"] == "error_workflow"
    assert run.events.filter(type="error_workflow_started").exists()


def test_error_workflow_that_fails_does_not_chain(ws, world):
    user = world["designer"]
    publish(ws, "alerte", [node("n", action="boom")], [], user=user)
    v = publish(ws, "fragile", [node("s", action="boom")], [], user=user)
    Workflow.objects.filter(workspace=ws, slug="fragile").update(error_workflow="alerte")
    Workflow.objects.filter(workspace=ws, slug="alerte").update(error_workflow="fragile")
    drain(launch(ws, v, user))
    child = WorkflowRun.objects.get(workspace=ws, workflow_version__workflow__slug="alerte")
    drain(child)
    assert WorkflowRun.objects.filter(workspace=ws).count() == 2


def test_error_workflow_without_published_version_is_reported_not_raised(ws, world):
    user = world["designer"]
    Workflow.objects.create(workspace=ws, slug="vide", name="vide")
    v = publish(ws, "fragile", [node("s", action="boom")], [], user=user)
    Workflow.objects.filter(workspace=ws, slug="fragile").update(error_workflow="vide")
    run = drain(launch(ws, v, user))
    assert run.status == "failed" and run.events.filter(type="error_workflow_skipped").exists()


def test_meta_validates_the_error_workflow(ws, world):
    user = world["designer"]
    publish(ws, "a", [node("s", action="echo", inputs={"value": "x"})], [], user=user)
    c = client_for(user)
    assert c.patch(f"{BASE}/workflows/a/meta/", {"error_workflow": "a"}, format="json").status_code == 400
    assert c.patch(f"{BASE}/workflows/a/meta/", {"error_workflow": "inconnu"}, format="json").status_code == 400
    publish(ws, "b", [node("s", action="echo", inputs={"value": "x"})], [], user=user)
    r = c.patch(f"{BASE}/workflows/a/meta/", {"error_workflow": "b"}, format="json")
    assert r.status_code == 200 and r.data["error_workflow"] == "b"


def test_rerun_starts_a_new_run_with_the_same_inputs(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "rej",
        [node("s", action="echo", inputs={"value": "{{ inputs.t }}"})],
        [],
        inputs=schema(["t"], t={"type": "string"}),
        user=user,
    )
    old = drain(launch(ws, v, user, inputs={"t": "bonjour"}))
    c = client_for(user)
    r = c.post(f"{BASE}/workflow-runs/{old.id}/rerun/")
    assert r.status_code == 202 and r.data["id"] != str(old.id)
    new = WorkflowRun.objects.get(pk=r.data["id"])
    assert new.inputs == {"t": "bonjour"} and new.workflow_version_id == old.workflow_version_id
    assert client_for(world["viewer"]).post(f"{BASE}/workflow-runs/{old.id}/rerun/").status_code == 403


def _waiting_run(ws, user, seconds=3600):
    v = publish(
        ws,
        "attente",
        [
            node("w", "wait", seconds=seconds, resume="webhook"),
            node("e", action="echo", inputs={"value": "{{ steps.w.output.payload.code }}"}),
        ],
        [edge("w", "e")],
        user=user,
    )
    run = drain(launch(ws, v, user), max_rounds=3)
    return run, run.steps.get(node_id="w")


def test_wait_resumes_on_http_call_with_payload(ws, world):
    from rest_framework.test import APIClient

    from runs import resume

    user = world["designer"]
    run, sr = _waiting_run(ws, user)
    assert run.status == "running" and sr.status == "waiting"
    detail = client_for(user).get(f"{BASE}/workflow-runs/{run.id}/").data
    url = next(s["resume_url"] for s in detail["steps"] if s["node_id"] == "w")
    assert url == f"/api/v1/hooks/resume/{resume.token_for(sr.pk)}/"
    assert all(
        s["resume_url"] is None
        for s in client_for(world["viewer"]).get(f"{BASE}/workflow-runs/{run.id}/").data["steps"]
    )
    anon = APIClient()
    assert anon.post(f"/api/v1/hooks/resume/{sr.pk}.{'0' * 32}/", {}, format="json").status_code == 404
    assert anon.post(url, {"code": "ABC"}, format="json").status_code == 202
    assert anon.post(url, {"code": "ZZZ"}, format="json").status_code == 409
    run = drain(run)
    assert run.status == "succeeded"
    assert run.steps.get(node_id="w").output == {"resumed": True, "payload": {"code": "ABC"}}
    assert run.steps.get(node_id="e").output == {"value": "ABC"}


def test_wait_without_call_ends_at_the_timeout_with_resumed_false(ws, world):
    from datetime import timedelta

    from django.utils import timezone

    run, sr = _waiting_run(ws, world["designer"], seconds=5)
    sr.available_at = timezone.now() - timedelta(seconds=1)
    sr.save()
    run = drain(run, max_rounds=4)
    assert run.steps.get(node_id="w").output == {"resumed": False, "payload": {}}


def test_rerun_from_failed_step_reuses_earlier_results_without_repeating_effects(ws, world, tmp_path):
    user, log = world["designer"], str(tmp_path / "log.txt")
    v = publish(
        ws,
        "reprise",
        [
            node("a", action="log", inputs={"path": log, "label": "a"}),
            node("b", action="flaky", inputs={"marker": str(tmp_path / "m")}),
            node("c", action="echo", inputs={"value": "fin"}),
        ],
        [edge("a", "b"), edge("b", "c")],
        user=user,
    )
    old = drain(launch(ws, v, user))
    assert old.status == "failed" and old.error["failed_step"] == "b"
    c = client_for(user)
    r = c.post(f"{BASE}/workflow-runs/{old.id}/rerun/", {"from_failed": True}, format="json")
    assert r.status_code == 202
    new = drain(WorkflowRun.objects.get(pk=r.data["id"]))
    assert new.status == "succeeded"
    assert open(log).read().split() == ["a"]  # « a » was NOT executed a second time
    assert new.steps.get(node_id="a").output == {"ok": True}
    assert new.events.filter(type="resumed_from_failed").exists()
    ok = c.post(f"{BASE}/workflow-runs/{new.id}/rerun/", {"from_failed": True}, format="json")
    assert ok.status_code == 400  # a run that succeeded cannot be resumed at a failed step


def test_compensated_steps_are_not_reused(ws, world, tmp_path):
    from runs import engine

    user, log = world["designer"], str(tmp_path / "log.txt")
    comp = {"action": "log", "inputs": {"path": log, "label": "undo"}}
    v = publish(
        ws,
        "saga2",
        [
            node("a", action="log", inputs={"path": log, "label": "a"}, compensation=comp),
            node("b", action="boom"),
        ],
        [edge("a", "b")],
        user=user,
    )
    old = drain(launch(ws, v, user))
    assert old.status == "failed" and set(engine.reusable_steps(old)) == set()


def test_success_workflow_receives_the_outputs_and_does_not_chain(ws, world):
    user = world["designer"]
    _handler(ws, user, slug="bravo")
    v = publish(
        ws,
        "reussit",
        [node("e", action="echo", inputs={"value": "ok"})],
        [],
        outputs={"v": "{{ steps.e.output.value }}"},
        user=user,
    )
    Workflow.objects.filter(workspace=ws, slug="reussit").update(success_workflow="bravo")
    run = drain(launch(ws, v, user))
    assert run.status == "succeeded" and run.events.filter(type="success_workflow_started").exists()
    child = WorkflowRun.objects.get(workspace=ws, workflow_version__workflow__slug="bravo")
    assert child.definition["trigger"] == "success_workflow"
    Workflow.objects.filter(workspace=ws, slug="bravo").update(success_workflow="reussit")
    drain(child)
    assert WorkflowRun.objects.filter(workspace=ws).count() == 2  # the follow-up does not start another one
    c = client_for(user)
    assert c.patch(f"{BASE}/workflows/reussit/meta/", {"success_workflow": "reussit"}, format="json").status_code == 400
    assert c.patch(f"{BASE}/workflows/reussit/meta/", {"success_workflow": "inconnu"}, format="json").status_code == 400
    assert (
        c.patch(f"{BASE}/workflows/reussit/meta/", {"success_workflow": "bravo"}, format="json").data[
            "success_workflow"
        ]
        == "bravo"
    )


def test_wait_resumes_on_a_public_form(ws, world):
    from rest_framework.test import APIClient

    from runs import resume

    user = world["designer"]
    v = publish(
        ws,
        "valid",
        [
            node(
                "w",
                "wait",
                seconds=3600,
                resume="form",
                form={
                    "properties": {
                        "decision": {"type": "string", "title": "Décision", "enum": ["oui", "non"]},
                        "commentaire": {"type": "string"},
                    },
                    "required": ["decision"],
                },
            ),
            node("e", action="echo", inputs={"value": "{{ steps.w.output.payload.decision }}"}),
        ],
        [edge("w", "e")],
        user=user,
    )
    run = drain(launch(ws, v, user), max_rounds=3)
    sr = run.steps.get(node_id="w")
    detail = client_for(user).get(f"{BASE}/workflow-runs/{run.id}/").data
    assert next(s["resume_url"] for s in detail["steps"] if s["node_id"] == "w") == f"/r/{resume.token_for(sr.pk)}"
    anon, url = APIClient(), f"/api/v1/resume-forms/{resume.token_for(sr.pk)}/"
    page = anon.get(url)
    assert (
        page.status_code == 200
        and set(page.data["schema"]["properties"]) == {"decision", "commentaire"}
        and page.data["schema"]["required"] == ["decision"]
    )
    assert anon.post(url, {"values": {"decision": "peut-être"}}, format="json").status_code == 400
    assert anon.post(url, {"values": {"decision": "oui"}}, format="json").status_code == 202
    assert anon.post(url, {"values": {"decision": "non"}}, format="json").status_code == 409
    assert anon.get(url).status_code == 409
    run = drain(run)
    assert run.status == "succeeded" and run.steps.get(node_id="e").output == {"value": "oui"}
    assert anon.get("/api/v1/resume-forms/1.0000/").status_code == 404
