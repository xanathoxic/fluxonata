import copy

import pytest

from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, make_action, node, publish
from vault.models import Connection
from workflows import templates
from workflows.models import Workflow
from workflows.validation import validate_graph

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    Connection.objects.create(
        workspace=world["ws_a"], name="svc", label="S", kind="http_bearer", config={"base_url": "https://example.org"}
    )
    return world["ws_a"]


@pytest.mark.parametrize("tpl", templates.TEMPLATES, ids=lambda t: t["slug"])
def test_every_builtin_template_is_a_valid_draft_once_its_connection_is_chosen(ws, tpl):
    graph = copy.deepcopy(tpl["graph"])
    for n in graph["nodes"]:
        if n["type"] == "http":
            n["connection"] = "svc"
    errors = [i for i in validate_graph(graph, ws, "t") if i["level"] == "error"]
    assert errors == [], (tpl["slug"], errors)
    assert (
        tpl["requires"]
        == sorted({n["integration"] for n in graph["nodes"] if n.get("integration")} & set(tpl["requires"]))
        or not tpl["requires"]
    )


def test_use_creates_a_draft_and_never_publishes(ws, world):
    c = client_for(world["designer"])
    listing = c.get(f"{BASE}/workflow-templates/").data["results"]
    assert len(listing) >= 8 and {"notifier-slack", "liste-vers-lots"} <= {t["slug"] for t in listing}
    r = c.post(f"{BASE}/workflow-templates/notifier-slack/use/", {"name": "Mon alerte"}, format="json")
    assert r.status_code == 201 and r.data["slug"] == "mon-alerte"
    wf = Workflow.objects.get(workspace=ws, slug="mon-alerte")
    assert wf.latest_version() is None and wf.draft_graph["nodes"][0]["integration"] == "slack"
    again = c.post(f"{BASE}/workflow-templates/notifier-slack/use/", {"name": "Mon alerte"}, format="json")
    assert again.data["slug"] == "mon-alerte-2"
    assert c.post(f"{BASE}/workflow-templates/inconnu/use/", {}, format="json").status_code == 404
    assert (
        client_for(world["viewer"])
        .post(f"{BASE}/workflow-templates/notifier-slack/use/", {}, format="json")
        .status_code
        == 403
    )


def test_save_as_template_then_use_it_and_export_import_round_trip(ws, world):
    user = world["designer"]
    publish(ws, "source", [node("e", action="echo", inputs={"value": "x"})], [], user=user)
    Workflow.objects.filter(workspace=ws, slug="source").update(
        draft_graph={
            **Workflow.objects.get(workspace=ws, slug="source").draft_graph,
            "pins": {"e": {"value": "secret-sample"}},
        }
    )
    c = client_for(user)
    saved = c.post(f"{BASE}/workflows/source/save-as-template/", {"name": "Mon modèle"}, format="json")
    assert saved.status_code == 201 and saved.data["slug"].startswith("ws:")
    assert "Mon modèle" in [t["name"] for t in c.get(f"{BASE}/workflow-templates/").data["results"]]
    made = c.post(f"{BASE}/workflow-templates/{saved.data['slug']}/use/", {}, format="json")
    assert (
        made.status_code == 201 and "pins" not in Workflow.objects.get(workspace=ws, slug=made.data["slug"]).draft_graph
    )
    doc = c.get(f"{BASE}/workflows/source/export/").data
    assert doc["format"] == "fluxonata-workflow/1" and "pins" not in doc["graph"]
    imp = c.post(f"{BASE}/workflows/import/", doc, format="json")
    assert imp.status_code == 201 and Workflow.objects.filter(workspace=ws, slug=imp.data["slug"]).exists()
    assert c.post(f"{BASE}/workflows/import/", {"format": "autre", "graph": {}}, format="json").status_code == 400
    bad = {**doc, "graph": {"nodes": [{"id": "a", "type": "inconnu", "name": "x"}], "edges": []}}
    assert c.post(f"{BASE}/workflows/import/", bad, format="json").status_code == 400


def test_comments_on_steps(ws, world):
    publish(ws, "revue", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    wf = Workflow.objects.get(workspace=ws, slug="revue")
    Workflow.objects.filter(pk=wf.pk).update(
        draft_graph={**wf.draft_graph, "nodes": [node("e", action="echo", inputs={"value": "x"})]}
    )
    d, v = client_for(world["designer"]), client_for(world["viewer"])
    url = f"{BASE}/workflows/revue/comments/"
    made = v.post(url, {"node_id": "e", "text": "Pourquoi cette valeur ?"}, format="json")  # a reader may comment
    assert made.status_code == 201 and made.data["author"] == "vic"
    assert d.post(url, {"node_id": "zz", "text": "x"}, format="json").status_code == 400
    assert d.post(url, {"node_id": "e", "text": "  "}, format="json").status_code == 400
    assert [c["text"] for c in d.get(url, {"node": "e"}).data["results"]] == ["Pourquoi cette valeur ?"]
    cid = made.data["id"]
    assert (
        d.patch(f"{url}{cid}/", {"resolved": True}, format="json").data["resolved"] is True
    )  # an editor can resolve any
    other = d.post(url, {"node_id": "e", "text": "Note"}, format="json").data["id"]
    assert v.delete(f"{url}{other}/").status_code == 400  # a reader cannot touch someone else's remark
    assert v.delete(f"{url}{cid}/").status_code == 204
    assert client_for(world["ws_b_user"] if "ws_b_user" in world else world["designer"]).get(
        "/api/v1/workspaces/team-b/workflows/revue/comments/"
    ).status_code in (403, 404)
