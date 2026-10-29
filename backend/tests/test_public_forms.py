import pytest
from rest_framework.test import APIClient

from runs.models import WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, make_action, node, publish, schema
from triggers.models import PublicForm

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


@pytest.fixture
def form(ws, world):
    publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.sujet }}"})],
        [],
        inputs=schema(["sujet"], sujet={"type": "string", "minLength": 3}, urgence={"type": "string"}),
        user=world["designer"],
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/forms/",
        {"name": "Demande", "title": "Faire une demande", "workflow": "demande", "environment": "test"},
        format="json",
    )
    assert r.status_code == 201, r.data
    return PublicForm.objects.get(pk=r.data["id"])


def test_the_public_page_shows_only_what_the_form_needs(form):
    r = APIClient().get(f"/api/v1/forms/{form.token}/")
    assert r.status_code == 200 and r.data["title"] == "Faire une demande"
    assert set(r.data["schema"]["properties"]) == {"sujet", "urgence"} and r.data["schema"]["required"] == ["sujet"]
    assert "demande" not in str(r.data).replace("Faire une demande", "") and "workflow" not in r.data
    assert APIClient().get("/api/v1/forms/inconnu/").status_code == 404


def test_a_submission_starts_a_run_as_the_form_owner_and_is_validated(form, ws, world):
    anon = APIClient()
    bad = anon.post(f"/api/v1/forms/{form.token}/", {"values": {"sujet": "x"}}, format="json")
    assert bad.status_code == 400
    ok = anon.post(f"/api/v1/forms/{form.token}/", {"values": {"sujet": "Imprimante en panne"}}, format="json")
    assert ok.status_code == 201 and ok.data["ok"] and "run" not in str(ok.data)
    run = WorkflowRun.objects.get(workspace=ws, workflow_version__workflow__slug="demande")
    assert run.inputs == {"sujet": "Imprimante en panne"} and run.requested_by == world["designer"]
    assert drain(run).status == "succeeded"
    assert form.submissions.count() == 1


def test_trap_field_cap_and_disabled_form(form, ws):
    anon = APIClient()
    trapped = anon.post(
        f"/api/v1/forms/{form.token}/", {"values": {"sujet": "Bonjour"}, "website": "http://spam"}, format="json"
    )
    assert (
        trapped.status_code == 200 and WorkflowRun.objects.filter(workspace=ws).count() == 0
    )  # pretended, nothing started
    PublicForm.objects.filter(pk=form.pk).update(max_per_hour=2)
    for _ in range(2):
        assert (
            anon.post(f"/api/v1/forms/{form.token}/", {"values": {"sujet": "Bonjour"}}, format="json").status_code
            == 201
        )
    assert anon.post(f"/api/v1/forms/{form.token}/", {"values": {"sujet": "Bonjour"}}, format="json").status_code == 429
    PublicForm.objects.filter(pk=form.pk).update(enabled=False)
    assert anon.get(f"/api/v1/forms/{form.token}/").status_code == 404


def test_form_owner_who_lost_the_right_to_run_stops_the_form(form, world):
    from tenancy.models import Membership, Role

    Membership.objects.filter(user=world["designer"], workspace=form.workspace).update(role=Role.VIEWER)
    r = APIClient().post(f"/api/v1/forms/{form.token}/", {"values": {"sujet": "Bonjour"}}, format="json")
    assert r.status_code in (401, 403)


def test_management_needs_the_publish_right_and_an_existing_published_workflow(ws, world, form):
    assert (
        client_for(world["viewer"])
        .post(f"{BASE}/forms/", {"name": "x", "title": "x", "workflow": "demande"}, format="json")
        .status_code
        == 403
    )
    c = client_for(world["designer"])
    assert c.get(f"{BASE}/forms/").data["results"][0]["url"] == f"/f/{form.token}"
    assert (
        c.patch(f"{BASE}/forms/{form.pk}/", {"enabled": False, "max_per_hour": 5000}, format="json").status_code == 400
    )
    assert c.patch(f"{BASE}/forms/{form.pk}/", {"enabled": False}, format="json").data["enabled"] is False
    assert c.delete(f"{BASE}/forms/{form.pk}/").status_code == 204


def _send(form, n=1):
    return [
        APIClient().post(f"/api/v1/forms/{form.token}/", {"values": {"sujet": "Bonjour"}}, format="json").status_code
        for _ in range(n)
    ]


def test_the_form_switches_itself_off_after_its_number_of_uses(form, ws, world):
    c = client_for(world["designer"])
    r = c.patch(f"{BASE}/forms/{form.pk}/", {"max_uses": 3}, format="json")
    assert r.status_code == 200 and r.data["max_uses"] == 3 and r.data["uses"] == 0 and r.data["state"] == "active"
    assert _send(form, 3) == [201, 201, 201]
    form.refresh_from_db()
    assert form.uses == 3 and form.enabled is False and form.closed_reason == "exhausted"
    assert _send(form) == [404]  # the 4th is refused, and nothing was started for it
    assert WorkflowRun.objects.filter(workspace=ws).count() == 3
    assert c.get(f"{BASE}/forms/").data["results"][0]["state"] == "exhausted"
    # a person cannot just switch it back on: the limit is reached
    stuck = c.patch(f"{BASE}/forms/{form.pk}/", {"enabled": True}, format="json")
    assert stuck.status_code == 400 and "compteur" in str(stuck.data)
    ok = c.patch(f"{BASE}/forms/{form.pk}/", {"enabled": True, "reset_uses": True}, format="json")
    assert ok.status_code == 200 and ok.data["uses"] == 0 and ok.data["state"] == "active"
    assert _send(form) == [201]


def test_the_form_expires_at_its_date_and_a_sweep_switches_it_off(form, world):
    from datetime import timedelta

    from django.utils import timezone

    from triggers.forms import expire_forms

    c = client_for(world["designer"])
    past = c.patch(
        f"{BASE}/forms/{form.pk}/", {"expires_at": (timezone.now() - timedelta(hours=1)).isoformat()}, format="json"
    )
    assert past.status_code == 400  # a date in the past is refused
    soon = (timezone.now() + timedelta(days=2)).isoformat()
    assert c.patch(f"{BASE}/forms/{form.pk}/", {"expires_at": soon}, format="json").status_code == 200
    assert _send(form) == [201]
    PublicForm.objects.filter(pk=form.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert c.get(f"{BASE}/forms/").data["results"][0]["state"] == "expired"  # visible before anything switches it off
    assert expire_forms() == 1
    form.refresh_from_db()
    assert form.enabled is False and form.closed_reason == "expired" and _send(form) == [404]
    assert APIClient().get(f"/api/v1/forms/{form.token}/").status_code == 404
    assert expire_forms() == 0
    # reactivation needs a new date
    assert c.patch(f"{BASE}/forms/{form.pk}/", {"enabled": True}, format="json").status_code == 400
    back = c.patch(f"{BASE}/forms/{form.pk}/", {"enabled": True, "expires_at": None}, format="json")
    assert back.status_code == 200 and back.data["state"] == "active" and _send(form) == [201]
