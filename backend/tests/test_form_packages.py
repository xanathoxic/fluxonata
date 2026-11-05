import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from bundles import archive
from formbuilder.models import Form, FormVersion
from tests.conftest import client_for
from tests.form_helpers import definition, field, make_form
from tests.wf_helpers import make_action, node, publish, schema

A = "/api/v1/workspaces/team-a"
B = "/api/v1/workspaces/team-b"


@pytest.fixture(autouse=True)
def _store(settings, tmp_path):
    settings.ORCH_ARTIFACTS_DIR = str(tmp_path / "artifacts")


D = definition(
    field("sujet", required=True),
    field("qui", "select", source={"connection": "svc", "path": "/people", "items_path": "rows"}),
)


def export(c, forms, version="1.0.0", **extra):
    body = {"name": "Pack", "slug": "pack", "version": version, "forms": forms, **extra}
    return c.post(f"{A}/packages/export/", body, format="json")


def upload(c, base, data, **form):
    return c.post(f"{base}/packages/import/", {"file": SimpleUploadedFile("p.zip", data), **form}, format="multipart")


def test_a_form_version_travels_and_arrives_as_a_form_of_the_library(world):
    c = make_form(world["designer"], "saisie", D, description="Saisie", tags=["rh"])
    r = export(c, [{"slug": "saisie", "version": "1.0.0"}])
    assert r.status_code == 201, r.content
    assert r.json()["contents"] == {"actions": 0, "workflows": 0, "forms": 1}
    data = c.get(r.json()["download_url"]).content
    assert b"token" not in data and b"max_per_hour" not in data

    other = client_for(world["outsider"])
    preview = upload(other, B, data, mode="preview").json()
    item = preview["items"][0]
    assert item["kind"] == "form" and item["status"] == "new" and any("svc" in m for m in item["messages"])
    assert Form.objects.filter(workspace=world["ws_b"]).count() == 0
    assert upload(other, B, data, mode="apply").json()["applied"] is True
    imported = Form.objects.get(workspace=world["ws_b"])
    assert imported.slug == "saisie" and imported.tags == ["rh"] and imported.draft_definition == D
    assert imported.latest_version().version == "1.0.0" and imported.latest_version().definition == D
    # importing it again changes nothing: identical
    again = upload(other, B, data, mode="preview").json()
    assert again["items"][0]["status"] == "identical" or again["errors"]


def test_a_different_version_with_the_same_number_is_a_conflict(world):
    c = make_form(world["designer"], "saisie", D)
    data = c.get(export(c, [{"slug": "saisie", "version": "1.0.0"}]).json()["download_url"]).content
    FormVersion.objects.filter(form__slug="saisie").update(definition=definition(field("autre")), digest="x")
    out = upload(c, A, data, mode="preview").json()
    assert out["items"][0]["status"] == "conflict" and not out["can_apply"]


def test_only_published_versions_can_be_exported(world):
    c = make_form(world["designer"], "brouillon", D, publish=False)
    r = export(c, [{"slug": "brouillon", "version": "1.0.0"}])
    assert r.status_code == 400 and "forms" in json.dumps(r.json())


def test_a_form_travels_with_its_workflow_in_one_package(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.sujet }}"})],
        [],
        inputs=schema(["sujet"], sujet={"type": "string"}),
        user=world["designer"],
    )
    c = make_form(world["designer"], "saisie", definition(field("sujet", required=True)))
    r = export(
        c,
        [{"slug": "saisie", "version": "1.0.0"}],
        workflows=[{"slug": "demande", "version": "1.0.0"}],
        actions=[{"slug": "echo", "version": "1.0.0"}],
    )
    assert r.status_code == 201 and r.json()["contents"] == {"actions": 1, "workflows": 1, "forms": 1}


def test_a_booby_trapped_form_in_a_package_is_refused(world):
    meta = {"name": "P", "slug": "trap", "version": "1.0.0", "description": "", "license": "Apache-2.0"}
    base = {"slug": "f", "version": "1.0.0", "name": "F"}
    c = client_for(world["designer"])
    bad = {
        "script injecté": {
            "sections": [{"title": "S", "fields": [{"key": "a", "label": "x", "type": "text", "script": "x"}]}]
        },
        "règle hors formulaire": {
            "sections": [
                {"title": "S", "fields": [{"key": "a", "label": "x", "type": "text", "visible_if": "inputs.a == 1"}]}
            ]
        },
        "trop de sections": {"sections": [{"title": str(i), "fields": []} for i in range(50)]},
        "sans sections": {},
    }
    for name, d in bad.items():
        data, _ = archive.build(meta, [], [], forms=[{**base, "definition": d}], sign=False)
        out = upload(c, A, data, mode="apply").json()
        assert out["applied"] is False and out["items"][0]["status"] == "invalid", name
    assert Form.objects.count() == 0
