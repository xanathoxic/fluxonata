import threading
from http.server import HTTPServer

import pytest

from tests.conftest import client_for
from tests.test_form_lookups import SEEN, Handler, source
from tests.wf_helpers import ACTIONS, make_action, node, schema
from triggers import lookups
from vault.models import Connection, Secret
from workflows.models import Workflow, WorkflowVersion
from workflows.validation import pin_graph, validate

A = "/api/v1/workspaces/team-a"


def input_form(*fields):
    return {"version": 1, "sections": [{"title": "Demande", "fields": list(fields)}]}


def field(key, type="text", **extra):
    return {"key": key, "label": key.capitalize(), "type": type, **extra}


@pytest.fixture
def server(settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    SEEN.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def make_version(ws, slug, form, user):
    graph = {
        "inputs": schema(["sujet"], sujet={"type": "string"}, qui={"type": "string"}),
        "nodes": [node("e", action="echo", inputs={"value": "{{ inputs.sujet }}"})],
        "edges": [],
        "outputs": {},
        "input_form": form,
    }
    issues = [i for i in validate(graph, ws, slug) if i["level"] == "error"]
    wf, _ = Workflow.objects.get_or_create(workspace=ws, slug=slug, defaults={"name": slug, "in_catalog": True})
    version = WorkflowVersion.objects.create(
        workflow=wf, version="1.0.0", graph=pin_graph(graph, ws), input_schema=graph["inputs"], created_by=user
    )
    return version, issues


@pytest.fixture
def ws(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="tok")
    s.set_value("sekret-token-123")
    s.save()
    Connection.objects.create(
        workspace=ws, name="svc", label="Svc", kind="http_bearer", config={"base_url": server}, secret=s
    )
    return ws


def test_validation_binds_the_designed_form_to_the_inputs(ws, world):
    ok = input_form(field("sujet", required=True), field("qui", "combobox", source=source()))
    assert make_version(ws, "ok", ok, world["designer"])[1] == []
    for name, form in {
        "entrée inconnue": input_form(field("sujet"), field("zzz")),
        "entrée obligatoire absente": input_form(field("qui")),
        "type incompatible": input_form(field("sujet"), field("qui", "checkbox")),
        "structure": {
            "sections": [{"title": "S", "fields": [{"key": "sujet", "label": "x", "type": "text", "js": 1}]}]
        },
    }.items():
        issues = make_version(ws, f"bad-{abs(hash(name))}", form, world["designer"])[1]
        assert issues and all(i["code"] == "bad_input_form" for i in issues), name


def test_an_unknown_connection_is_only_a_warning(ws, world):
    form = input_form(field("sujet"), field("qui", "select", source={**source(), "connection": "nope"}))
    version, errors = make_version(ws, "warn", form, world["designer"])
    assert errors == []
    issues = validate(version.graph, ws, "warn")
    assert [i["code"] for i in issues if i["level"] == "warning"] == ["input_form_connection"]


def test_the_catalog_and_the_workflow_carry_the_designed_form(ws, world):
    form = input_form(field("sujet", required=True), field("qui", "select", options=[{"value": "a"}]))
    make_version(ws, "demande", form, world["designer"])
    c = client_for(world["viewer"])
    assert c.get(f"{A}/catalog/demande/").data["input_form"] == form
    assert c.get(f"{A}/workflows/demande/").data["latest_version"]["input_form"] == form


def test_signed_in_lookup_is_queued_for_the_worker_and_follows_permissions(ws, world):
    form = input_form(field("sujet"), field("qui", "combobox", source=source()))
    make_version(ws, "demande", form, world["designer"])
    c = client_for(world["designer"])
    url = f"{A}/workflows/demande/form-lookup/qui/"
    r = c.post(url, {"params": {"q": "al"}, "environment": "test"}, format="json")
    assert r.status_code == 202 and SEEN == []  # the web process calls nothing
    assert c.get(f"{url}{r.data['id']}/").data == {"state": "pending"}
    assert lookups.process_one("w1")
    out = c.get(f"{url}{r.data['id']}/").data
    assert out["state"] == "done" and [o["label"] for o in out["options"]] == ["Alice", "Bob"]
    assert SEEN[0]["auth"] == "Bearer sekret-token-123" and SEEN[0]["path"] == "/people?q=al"
    assert "sekret" not in str(out)
    again = c.post(url, {"params": {"q": "al"}, "environment": "test"}, format="json")
    assert again.data["id"] == r.data["id"]  # a recent identical lookup is reused
    assert c.post(f"{A}/workflows/demande/form-lookup/sujet/", {}, format="json").status_code == 404  # no source
    assert client_for(world["viewer"]).post(url, {}, format="json").status_code == 403
    assert client_for(world["outsider"]).post(url, {}, format="json").status_code == 404  # other workspace
    assert client_for(world["designer"]).post(f"{url}", {}, format="json").status_code == 202
    from rest_framework.test import APIClient

    assert APIClient().post(url, {}, format="json").status_code in (401, 403)
