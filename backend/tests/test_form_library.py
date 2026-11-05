import pytest
from rest_framework.test import APIClient

from formbuilder.models import Form
from tests.conftest import client_for
from tests.form_helpers import BASE, definition, field, make_form
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema
from workflows.models import Workflow

L = f"{BASE}/form-library"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def test_create_edit_publish_and_version_numbers(world):
    c = client_for(world["designer"])
    d = definition(field("sujet", required=True))
    r = c.post(f"{L}/", {"slug": "demande", "name": "Demande", "tags": ["rh"], "definition": d}, format="json")
    assert r.status_code == 201 and r.data["latest_version"] is None and r.data["fields"] == 1
    assert c.post(f"{L}/", {"slug": "demande", "name": "x"}, format="json").status_code == 400  # slug already used
    assert (
        c.post(f"{L}/", {"slug": "mauvais", "name": "x", "definition": {"sections": []}}, format="json").status_code
        == 400
    )

    detail = c.get(f"{L}/demande/").data
    assert detail["definition"] == d and detail["versions"] == [] and detail["draft_revision"] == 1
    assert c.post(f"{L}/demande/publish/", {"changelog": "première"}, format="json").data["version"] == "1.0.0"
    d2 = definition(field("sujet", required=True), field("detail"))
    saved = c.patch(f"{L}/demande/", {"definition": d2, "revision": 1}, format="json")
    assert saved.status_code == 200 and saved.data["draft_revision"] == 2
    assert c.post(f"{L}/demande/publish/", {}, format="json").data["version"] == "1.1.0"
    assert c.post(f"{L}/demande/publish/", {"bump": "major"}, format="json").data["version"] == "2.0.0"
    assert c.post(f"{L}/demande/publish/", {"bump": "patch"}, format="json").data["version"] == "2.0.1"
    old = c.get(f"{L}/demande/versions/1.0.0/").data
    assert old["definition"] == d and old["changelog"] == "première"  # a published version never changes


def test_a_stale_revision_conflicts_and_an_empty_form_is_not_published(world):
    c = client_for(world["designer"])
    c.post(f"{L}/", {"slug": "f", "name": "F"}, format="json")
    assert c.post(f"{L}/f/publish/", {}, format="json").status_code == 400  # no field yet
    ok = c.patch(f"{L}/f/", {"definition": definition(field("a")), "revision": 1}, format="json")
    assert ok.status_code == 200
    stale = c.patch(f"{L}/f/", {"definition": definition(field("b")), "revision": 1}, format="json")
    assert stale.status_code == 409 and stale.json()["error"]["details"]["current_revision"] == 2
    assert (
        c.patch(f"{L}/f/", {"definition": definition(field("b"))}, format="json").status_code == 400
    )  # revision required
    assert (
        c.patch(f"{L}/f/", {"name": "Renommé", "folder": "RH/Congés", "tags": ["a", "b"]}, format="json").status_code
        == 200
    )
    assert Form.objects.get(slug="f").folder == "RH/Congés"


def test_templates_are_copied_never_attached_nor_published(world, ws):
    c = make_form(world["designer"], "modele", definition(field("sujet")), publish=False, is_template=True)
    assert c.post(f"{L}/modele/publish/", {}, format="json").status_code == 400
    assert [f["slug"] for f in c.get(f"{L}/?template=1").data["results"]] == ["modele"]
    assert c.get(f"{L}/?template=0").data["results"] == []
    copy = c.post(f"{L}/modele/duplicate/", {"slug": "vrai", "name": "Vrai"}, format="json")
    assert copy.status_code == 201 and copy.data["is_template"] is False
    again = c.post(f"{L}/vrai/duplicate/", {"slug": "modele-2", "name": "M2", "as_template": True}, format="json")
    assert again.data["is_template"] is True
    assert Form.objects.get(slug="vrai").draft_definition == definition(field("sujet"))
    builtin = c.get(f"{L}/builtin-templates/").data["results"]
    assert {t["id"] for t in builtin} >= {"contact", "incident"}
    from triggers import formengine

    assert all(formengine.check_definition(t["definition"]) == {} for t in builtin)


def test_a_form_in_use_cannot_be_deleted(world, ws):
    v = publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.sujet }}"})],
        [],
        inputs=schema(["sujet"], sujet={"type": "string"}),
        user=world["designer"],
    )
    Workflow.objects.filter(workspace=ws, slug="demande").update(draft_graph=v.graph)
    c = make_form(world["designer"], "ma-saisie", definition(field("sujet", required=True)))
    wf = c.get(f"{BASE}/workflows/demande/").data
    graph = {**wf["draft_graph"], "input_form": {"ref": {"form": "ma-saisie", "version": None}}}
    saved = c.put(f"{BASE}/workflows/demande/draft/", {"graph": graph, "revision": wf["draft_revision"]}, format="json")
    assert saved.status_code == 200 and not [i for i in saved.data["issues"] if i["level"] == "error"]
    assert c.get(f"{L}/ma-saisie/").data["usage"]["workflows"] == [
        {"slug": "demande", "versions": [], "in_draft": True}
    ]
    pub = c.post(f"{BASE}/workflows/demande/publish/", {}, format="json")
    assert pub.status_code == 201, pub.data
    usage = c.get(f"{L}/ma-saisie/").data["usage"]["workflows"]
    assert usage[0]["versions"] and usage[0]["in_draft"] is True
    assert c.delete(f"{L}/ma-saisie/").status_code == 400
    # the published version of the workflow carries the exact form version and a copy of its definition
    latest = c.get(f"{BASE}/workflows/demande/").data["latest_version"]["input_form"]
    assert latest["ref"] == {"form": "ma-saisie", "version": "1.0.0"} and latest["sections"]
    # editing and republishing the form changes nothing for that workflow version
    d = c.get(f"{L}/ma-saisie/").data
    c.patch(
        f"{L}/ma-saisie/",
        {"definition": definition(field("sujet"), field("x")), "revision": d["draft_revision"]},
        format="json",
    )
    c.post(f"{L}/ma-saisie/publish/", {}, format="json")
    assert c.get(f"{BASE}/workflows/demande/").data["latest_version"]["input_form"]["ref"]["version"] == "1.0.0"


def test_a_draft_referencing_a_missing_or_unbound_form_is_flagged(world, ws):
    v = publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.sujet }}"})],
        [],
        inputs=schema(["sujet"], sujet={"type": "string"}),
        user=world["designer"],
    )
    Workflow.objects.filter(workspace=ws, slug="demande").update(draft_graph=v.graph)
    c = make_form(world["designer"], "autre", definition(field("zzz")))
    wf = c.get(f"{BASE}/workflows/demande/").data
    for ref, expect in (
        ({"form": "nope", "version": None}, "introuvable"),
        ({"form": "autre", "version": None}, "zzz"),
    ):
        graph = {**wf["draft_graph"], "input_form": {"ref": ref}}
        r = c.put(
            f"{BASE}/workflows/demande/draft/",
            {"graph": graph, "revision": c.get(f"{BASE}/workflows/demande/").data["draft_revision"]},
            format="json",
        )
        assert any(expect in i["message"] and i["level"] == "error" for i in r.data["issues"]), r.data["issues"]


def test_permissions_and_isolation(world, ws):
    make_form(world["designer"], "f", definition(field("a")))
    viewer, outsider = client_for(world["viewer"]), client_for(world["outsider"])
    assert viewer.get(f"{L}/f/").status_code == 200 and viewer.get(f"{L}/").status_code == 200
    assert viewer.post(f"{L}/", {"slug": "v", "name": "V"}, format="json").status_code == 403
    assert viewer.patch(f"{L}/f/", {"name": "x"}, format="json").status_code == 403
    assert viewer.post(f"{L}/f/publish/", {}, format="json").status_code == 403
    assert viewer.delete(f"{L}/f/").status_code == 403
    assert outsider.get(f"{L}/f/").status_code == 404 and outsider.get(f"{L}/").status_code == 404
    assert APIClient().get(f"{L}/f/").status_code in (401, 403)
