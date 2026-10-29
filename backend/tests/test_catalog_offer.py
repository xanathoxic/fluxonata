"""« Catalogue d'automatisations » offers finished workflows that someone allowed to publish chose to show."""

import pytest

from audit.models import AuditEvent
from tests.conftest import client_for
from tests.wf_helpers import STR, make_action, node, publish, schema
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def wf(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(
        ws,
        "rapport",
        [node("a", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v=STR),
        user=world["designer"],
    )
    Workflow.objects.filter(workspace=ws, slug="rapport").update(
        name="Rapport mensuel", description="Le rapport du mois"
    )
    return Workflow.objects.get(workspace=ws, slug="rapport")


def test_nothing_is_offered_until_someone_publishes_it(world, wf):
    c = client_for(world["viewer"])
    assert c.get(f"{BASE}/catalog/").json()["results"] == []
    assert c.get(f"{BASE}/catalog/rapport/").status_code == 404


def test_designer_offers_and_withdraws_viewer_only_reads(world, wf):
    d, v = client_for(world["designer"]), client_for(world["viewer"])
    assert v.post(f"{BASE}/workflows/rapport/catalog/").status_code == 403
    assert v.delete(f"{BASE}/workflows/rapport/catalog/").status_code == 403
    assert d.post(f"{BASE}/workflows/rapport/catalog/").json() == {"slug": "rapport", "in_catalog": True}
    d.post(f"{BASE}/workflows/rapport/catalog/")  # idempotent, one audit event
    rows = v.get(f"{BASE}/catalog/").json()
    assert (
        [r["slug"] for r in rows["results"]] == ["rapport"]
        and rows["can_run"] is False
        and rows["can_publish"] is False
    )
    card = rows["results"][0]
    assert card["name"] == "Rapport mensuel" and card["version"] == "1.0.0" and card["inputs"] == 1
    detail = v.get(f"{BASE}/catalog/rapport/").json()
    assert detail["input_schema"]["required"] == ["v"] and detail["can_run"] is False
    assert AuditEvent.objects.filter(action="workflow.catalog_publish").count() == 1
    assert d.delete(f"{BASE}/workflows/rapport/catalog/").json()["in_catalog"] is False
    assert (
        v.get(f"{BASE}/catalog/").json()["results"] == []
        and AuditEvent.objects.filter(action="workflow.catalog_withdraw").count() == 1
    )


def test_a_workflow_without_a_published_version_cannot_be_offered(world):
    ws = world["ws_a"]
    Workflow.objects.create(workspace=ws, slug="brouillon", name="Brouillon")
    assert client_for(world["designer"]).post(f"{BASE}/workflows/brouillon/catalog/").status_code == 400


def test_deprecated_workflows_leave_the_catalog_and_cannot_join_it(world, wf):
    d = client_for(world["designer"])
    d.post(f"{BASE}/workflows/rapport/catalog/")
    Workflow.objects.filter(pk=wf.pk).update(deprecated=True)
    assert d.get(f"{BASE}/catalog/").json()["results"] == []
    d.delete(f"{BASE}/workflows/rapport/catalog/")
    assert d.post(f"{BASE}/workflows/rapport/catalog/").status_code == 400


def test_search_matches_name_description_and_folder(world, wf):
    d = client_for(world["designer"])
    d.post(f"{BASE}/workflows/rapport/catalog/")
    assert [r["slug"] for r in d.get(f"{BASE}/catalog/?q=mensuel").json()["results"]] == ["rapport"]
    assert [r["slug"] for r in d.get(f"{BASE}/catalog/?q=du%20mois").json()["results"]] == ["rapport"]
    assert d.get(f"{BASE}/catalog/?q=inconnu").json()["results"] == []


def test_the_catalog_is_per_workspace_and_hidden_from_non_members(world, wf):
    client_for(world["designer"]).post(f"{BASE}/workflows/rapport/catalog/")
    o = client_for(world["outsider"])
    assert o.get(f"{BASE}/catalog/").status_code == 404
    assert o.get(f"{BASE}/catalog/rapport/").status_code == 404
    assert o.post(f"{BASE}/workflows/rapport/catalog/").status_code == 404
    assert o.get("/api/v1/workspaces/team-b/catalog/").json()["results"] == []


def test_workflow_payload_tells_whether_it_is_in_the_catalog(world, wf):
    d = client_for(world["designer"])
    assert d.get(f"{BASE}/workflows/rapport/").json()["in_catalog"] is False
    d.post(f"{BASE}/workflows/rapport/catalog/")
    assert d.get(f"{BASE}/workflows/rapport/").json()["in_catalog"] is True
