"""Tags: several per workflow, action or AI skill; normalised, bounded, kept by export/import, shown in the catalogue."""

import pytest
from rest_framework import serializers

from core.tags import MAX_TAGS, clean_tags
from tests.conftest import client_for
from tests.wf_helpers import STR, make_action, node, publish, schema
from workflows import sync
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


def test_clean_tags_is_lenient_on_input_and_strict_on_output():
    assert clean_tags([" Réseau ", "DNS", "dns", "mise à jour", ""]) == ["réseau", "dns", "mise-à-jour"]
    assert clean_tags("a, b ,a") == ["a", "b"]
    assert clean_tags(None) == []
    for bad in (["x" * 31], ["a/b"], ["-a"], [3], "..", ["#a"]):
        with pytest.raises(serializers.ValidationError):
            clean_tags(bad)
    with pytest.raises(serializers.ValidationError):
        clean_tags([f"t{i}" for i in range(MAX_TAGS + 1)])


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
    return Workflow.objects.get(workspace=ws, slug="rapport")


def test_workflow_tags_create_edit_and_validate(world, wf):
    c = client_for(world["designer"])
    r = c.post(f"{BASE}/workflows/", {"slug": "neuf", "name": "Neuf", "tags": ["Prod", "réseau"]}, format="json")
    assert r.status_code == 201 and r.json()["tags"] == ["prod", "réseau"]
    r = c.patch(f"{BASE}/workflows/rapport/meta/", {"tags": ["Facturation", "mensuel", "facturation"]}, format="json")
    assert r.status_code == 200 and r.json()["tags"] == ["facturation", "mensuel"]
    assert c.get(f"{BASE}/workflows/rapport/").json()["tags"] == ["facturation", "mensuel"]
    assert any(w["tags"] == ["facturation", "mensuel"] for w in c.get(f"{BASE}/workflows/").json()["results"])
    assert c.patch(f"{BASE}/workflows/rapport/meta/", {"tags": ["pas valide/"]}, format="json").status_code == 400
    assert (
        c.patch(f"{BASE}/workflows/rapport/meta/", {"tags": [f"t{i}" for i in range(11)]}, format="json").status_code
        == 400
    )
    assert c.patch(f"{BASE}/workflows/rapport/meta/", {"tags": []}, format="json").json()["tags"] == []


def test_tags_follow_the_same_rights_as_the_other_fields(world, wf):
    assert (
        client_for(world["viewer"]).patch(f"{BASE}/workflows/rapport/meta/", {"tags": ["x"]}, format="json").status_code
        == 403
    )
    assert (
        client_for(world["outsider"])
        .patch(f"{BASE}/workflows/rapport/meta/", {"tags": ["x"]}, format="json")
        .status_code
        == 404
    )


def test_action_tags_edit(world):
    make_action(world["ws_a"], "echo")
    c = client_for(world["designer"])
    r = c.patch(f"{BASE}/actions/echo/meta/", {"tags": ["Texte", "démo"]}, format="json")
    assert r.status_code == 200 and r.json()["tags"] == ["texte", "démo"]
    assert c.patch(f"{BASE}/actions/echo/meta/", {"tags": ["a b/c"]}, format="json").status_code == 400
    assert (
        client_for(world["viewer"]).patch(f"{BASE}/actions/echo/meta/", {"tags": ["x"]}, format="json").status_code
        == 403
    )


def test_skill_tags_create_and_edit(world):
    c = client_for(world["designer"])
    body = {
        "slug": "qualifier",
        "name": "Qualifier",
        "tags": ["Incident", "N1"],
        "version": {
            "instructions": "Classe {{ inputs.texte }}.",
            "input_schema": {"type": "object", "properties": {"texte": {"type": "string"}}, "required": ["texte"]},
            "output_schema": {
                "type": "object",
                "properties": {"categorie": {"type": "string"}},
                "required": ["categorie"],
            },
        },
    }
    r = c.post(f"{BASE}/ai/skills/", body, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["tags"] == ["incident", "n1"]
    r = c.patch(f"{BASE}/ai/skills/qualifier/", {"tags": ["tri"]}, format="json")
    assert r.status_code == 200 and r.json()["tags"] == ["tri"]
    assert c.get(f"{BASE}/ai/skills/").json()["results"][0]["tags"] == ["tri"]


def test_catalogue_cards_carry_the_tags(world, wf):
    Workflow.objects.filter(pk=wf.pk).update(tags=["finance"], in_catalog=True)
    cards = client_for(world["viewer"]).get(f"{BASE}/catalog/").json()["results"]
    assert cards and cards[0]["tags"] == ["finance"]


def test_export_import_keeps_tags_and_ignores_bad_ones(world, wf):
    Workflow.objects.filter(pk=wf.pk).update(tags=["a", "b"])
    docs = sync.export_documents(world["ws_a"], world["designer"])
    assert [d["tags"] for d in docs if d["slug"] == "rapport"] == [["a", "b"]]
    Workflow.objects.filter(pk=wf.pk).delete()
    res = sync.import_documents(world["ws_a"], world["designer"], docs, overwrite=False, dry=False)
    assert res[0]["status"] != "refused"
    assert Workflow.objects.get(workspace=world["ws_a"], slug="rapport").tags == ["a", "b"]
    docs[0]["tags"] = ["ok", "pas/valide"]
    sync.import_documents(world["ws_a"], world["designer"], docs, overwrite=True, dry=False)
    assert Workflow.objects.get(workspace=world["ws_a"], slug="rapport").tags == []


def test_a_workflow_started_from_a_built_in_template_carries_its_tags(world):
    c = client_for(world["designer"])
    r = c.post(f"{BASE}/workflow-templates/notifier-slack/use/", {}, format="json")
    assert r.status_code == 201, r.content
    assert c.get(f"{BASE}/workflows/{r.json()['slug']}/").json()["tags"] == ["messagerie", "slack"]
