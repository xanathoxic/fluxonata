"""Publication de plusieurs workflows en une requête : chacun dans sa transaction, ceux qui ne peuvent pas l'être sont signalés sans arrêter les autres,
l'enfant avant le parent, rien à publier quand le brouillon n'a pas changé."""

import pytest

from tests.conftest import client_for
from tests.wf_helpers import STR, node, schema
from workflows.models import Workflow, WorkflowVersion

WS = "/api/v1/workspaces/team-a"


def draft(ws, slug, nodes, folder=""):
    wf, _ = Workflow.objects.get_or_create(workspace=ws, slug=slug, defaults={"name": slug.upper(), "folder": folder})
    wf.draft_graph = {"inputs": schema(["v"], v=STR), "nodes": nodes, "edges": [], "outputs": {}}
    wf.save()
    return wf


def echo(i="e"):
    return node(i, action="echo", inputs={"value": "{{ inputs.v }}"})


def publish_many(c, slugs, **extra):
    return c.post(f"{WS}/workflows/publish-many/", {"slugs": slugs, **extra}, format="json")


@pytest.fixture
def designer(ws, world):
    return client_for(world["designer"])


def test_several_drafts_are_published_at_once(ws, designer):
    draft(ws, "a", [echo()])
    draft(ws, "b", [echo()])
    r = publish_many(designer, ["a", "b"], bump="minor", changelog="lot")
    assert r.status_code == 200
    body = r.json()
    assert body["published"] == 2 and body["skipped"] == 0 and body["error"] == 0
    assert {x["slug"]: x["version"] for x in body["results"]} == {"a": "1.0.0", "b": "1.0.0"}
    assert WorkflowVersion.objects.get(workflow__slug="a").changelog == "lot"


def test_one_invalid_workflow_does_not_stop_the_others_and_is_explained(ws, designer):
    draft(ws, "ok", [echo()])
    draft(ws, "vide", [])
    draft(ws, "casse", [node("x", action="n-existe-pas")])
    body = publish_many(designer, ["ok", "vide", "casse"]).json()
    by = {x["slug"]: x for x in body["results"]}
    assert by["ok"]["status"] == "published"
    assert by["vide"]["status"] == "error" and "vide" in by["vide"]["message"].lower()
    assert by["casse"]["status"] == "error" and by["casse"]["message"]
    assert body["published"] == 1 and body["error"] == 2
    assert not WorkflowVersion.objects.filter(
        workflow__slug__in=["vide", "casse"]
    ).exists()  # rien n'a été écrit pour eux


def test_an_unchanged_draft_is_skipped_not_republished(ws, designer):
    draft(ws, "a", [echo()])
    assert publish_many(designer, ["a"]).json()["published"] == 1
    again = publish_many(designer, ["a"]).json()
    assert again["skipped"] == 1 and again["published"] == 0 and "Aucune modification" in again["results"][0]["message"]
    assert WorkflowVersion.objects.filter(workflow__slug="a").count() == 1
    draft(ws, "a", [echo("e2")])  # modifié : repart
    assert publish_many(designer, ["a"]).json()["results"][0]["version"] == "1.1.0"


def test_a_child_is_published_before_its_parent_so_the_parent_pins_the_new_version(ws, designer):
    draft(ws, "enfant", [echo()])
    assert publish_many(designer, ["enfant"]).status_code == 200  # enfant 1.0.0
    draft(ws, "enfant", [echo("e2")])  # nouvelle version à publier
    draft(ws, "parent", [node("c", "subworkflow", workflow="enfant", inputs={"v": "x"})])
    body = publish_many(designer, ["parent", "enfant"]).json()  # le parent est demandé en premier
    assert [r["slug"] for r in body["results"]] == ["enfant", "parent"]
    pinned = WorkflowVersion.objects.get(workflow__slug="parent").graph["nodes"][0]["version"]
    assert pinned == "1.1.0"  # la version de l'enfant publiée dans le même lot


def test_publication_rights_and_visibility_are_enforced_per_workflow(ws, world):
    draft(ws, "a", [echo()])
    viewer = client_for(world["viewer"])
    assert publish_many(viewer, ["a"]).status_code in (403, 404)
    assert not WorkflowVersion.objects.exists()
    outsider = client_for(world["outsider"])
    assert publish_many(outsider, ["a"]).status_code in (403, 404)
    assert not WorkflowVersion.objects.exists()


def test_an_unknown_slug_is_reported_and_the_request_is_bounded(ws, designer):
    draft(ws, "a", [echo()])
    body = publish_many(designer, ["a", "absent"]).json()
    assert body["published"] == 1 and {r["slug"]: r["status"] for r in body["results"]}["absent"] == "error"
    assert publish_many(designer, []).status_code == 400
    assert publish_many(designer, [f"w{i}" for i in range(51)]).status_code == 400
    assert publish_many(designer, ["a", "a"]).json()["results"].__len__() == 1  # doublons ignorés


def test_the_identifier_of_the_bulk_route_cannot_be_used_by_a_workflow(ws, designer):
    r = designer.post(f"{WS}/workflows/", {"name": "x", "slug": "publish-many"}, format="json")
    assert r.status_code == 400 and "réservé" in r.content.decode()
