import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import pytest
from rest_framework.test import APIClient

from tests.conftest import client_for
from tests.form_helpers import make_form
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema
from triggers import lookups
from triggers.models import FormLookup, PublicForm
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"
SEEN: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if self.path.startswith("/boom"):
            code, out = 500, {"error": "secret internal detail"}
        elif self.path.startswith("/brands"):
            code = 200
            out = {
                "data": {
                    "rows": [
                        {"id": "saab", "name": "Saab"},
                        {"id": "ford", "name": "Ford", "region": "US Brands"},
                        {"id": "plymouth", "name": "Plymouth", "region": "US Brands", "off": True},
                    ]
                }
            }
        elif self.path.startswith("/order"):
            code = 200
            out = {
                "order": {"lines": [{"produit": "A", "qte": 2, "prix": 5, "inconnu": 1}, {"produit": "B", "qte": 1}]}
            }
        elif self.path.startswith("/badorder"):
            code, out = 200, {"order": {"lines": [{"qte": "beaucoup"}]}}
        elif self.path.startswith("/people"):
            out = {"data": {"rows": [{"id": 7, "name": "Alice"}, {"id": 8, "name": "Bob"}, {"id": 7, "name": "Dup"}]}}
            code = 200
        else:
            code, out = 404, {}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    SEEN.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def source(path="/people?q={q}", **extra):
    return {
        "connection": "svc",
        "path": path,
        "params": ["q"],
        "items_path": "data.rows",
        "label_path": "name",
        "value_path": "id",
        **extra,
    }


@pytest.fixture
def form(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="tok")
    s.set_value("sekret-token-123")
    s.save()
    Connection.objects.create(
        workspace=ws, name="svc", label="Svc", kind="http_bearer", config={"base_url": server}, secret=s
    )
    publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.qui }}"})],
        [],
        inputs=schema(["qui"], qui={"type": "string"}),
        user=world["designer"],
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/forms/",
        {"name": "D", "title": "D", "workflow": "demande", "environment": "test"},
        format="json",
    )
    d = {
        "sections": [{"title": "S", "fields": [{"key": "qui", "label": "Qui", "type": "combobox", "source": source()}]}]
    }
    make_form(world["designer"], "saisie", d)
    assert c.post(f"{BASE}/forms/{r.data['id']}/attach/", {"form": "saisie"}, format="json").status_code == 200, (
        "attach"
    )
    return PublicForm.objects.get(pk=r.data["id"])


def ask(form, params=None, field="qui"):
    return APIClient().post(f"/api/v1/forms/{form.token}/lookup/{field}/", {"params": params or {}}, format="json")


def test_the_web_process_only_queues_and_the_worker_answers(form):
    r = ask(form, {"q": "al"})
    assert r.status_code == 202 and SEEN == []  # nothing was called from the web process
    page = f"/api/v1/forms/{form.token}/lookup/qui/{r.data['id']}/"
    assert APIClient().get(page).data == {"state": "pending"}
    assert lookups.process_one("w1") is True and lookups.process_one("w1") is False
    out = APIClient().get(page).data
    assert out == {"state": "done", "options": [{"value": "7", "label": "Alice"}, {"value": "8", "label": "Bob"}]}
    assert SEEN == [{"path": "/people?q=al", "auth": "Bearer sekret-token-123"}]
    assert "sekret" not in json.dumps(out)


def test_an_identical_recent_lookup_is_reused_and_params_are_encoded(form):
    first = ask(form, {"q": "a/b ?x=1&host=evil"}).data["id"]
    assert ask(form, {"q": "a/b ?x=1&host=evil"}).data["id"] == first
    lookups.process_one("w1")
    assert SEEN[0]["path"] == "/people?q=a%2Fb%20%3Fx%3D1%26host%3Devil"
    assert FormLookup.objects.count() == 1


def test_an_upstream_failure_gives_a_neutral_message(form, world):
    row = FormLookup.objects.create(form=form, field_key="qui", params={}, params_hash="x")
    form.published_definition["sections"][0]["fields"][0]["source"] = source("/boom")
    form.save()
    assert lookups.process_one("w1")
    row.refresh_from_db()
    assert row.status == "failed" and "internal" not in row.error


def test_unknown_form_field_or_job_and_queue_limit(form):
    assert ask(form, field="autre").status_code == 404
    assert APIClient().post("/api/v1/forms/inconnu/lookup/qui/", {}, format="json").status_code == 404
    assert (
        APIClient()
        .get(f"/api/v1/forms/{form.token}/lookup/qui/{form.pk and 'b' * 8}-0000-0000-0000-000000000000/")
        .status_code
        == 404
    )
    for i in range(lookups.MAX_QUEUED_PER_FORM):
        assert ask(form, {"q": str(i)}).status_code == 202
    assert ask(form, {"q": "de trop"}).status_code == 429


def test_attaching_refuses_an_unknown_connection(form, world):
    c = client_for(world["designer"])
    d = {
        "sections": [
            {
                "title": "S",
                "fields": [
                    {"key": "qui", "label": "Qui", "type": "select", "source": {**source(), "connection": "nope"}}
                ],
            }
        ]
    }
    make_form(world["designer"], "autre", d)
    r = c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "autre"}, format="json")
    assert r.status_code == 400 and "nope" in str(r.data)


def test_the_path_cannot_leave_the_connection_host(form):
    conn = Connection.objects.get(name="svc")
    for path in ("/ok @evil", "http://evil.example/", "evil", "/x\r\nHost: evil", "/people?q={zzz}"):
        with pytest.raises(ValueError):
            lookups.build_url(conn, source(path), {"q": "x"})
    assert lookups.build_url(conn, source("/people?q={q}"), {"q": "x"}).endswith("/people?q=x")
    # a path that merely looks like another host stays a path on the connection's own host
    url = lookups.build_url(conn, source("//evil.example/x"), {})
    assert urlsplit(url).hostname == "127.0.0.1" and urlsplit(url).path == "//evil.example/x"


# --- custom controls that read a service -----------------------------------------------------------------------------
def control_field(key, spec, **extra):
    """A form field using a control: the reading of the service is the FIELD's, the control carries none (it is reused by forms
    that each read their own service)."""
    return {
        "key": key,
        "label": key.capitalize(),
        "type": "custom",
        "control": {"ref": "ctl", "version": "1.0.0", "name": "C", "spec": spec},
        **extra,
    }


LINES = {
    "version": 1,
    "mode": "repeat",
    "parts": [
        {"key": "produit", "label": "Produit", "type": "text", "required": True},
        {"key": "qte", "label": "Qte", "type": "number", "required": True},
        {"key": "prix", "label": "Prix", "type": "number"},
        {"key": "total", "label": "Total", "type": "number", "compute": "{qte} * {prix}"},
    ],
}
LINES_PREFILL = {"connection": "svc", "path": "/order", "value_path": "order.lines"}
BRANDS = {"version": 1, "mode": "items", "display": "dropdown", "dynamic": True}
BRANDS_SOURCE = {
    "connection": "svc",
    "path": "/brands?q={q}",
    "params": ["q"],
    "items_path": "data.rows",
    "value_path": "id",
    "label_path": "name",
    "group_path": "region",
    "disabled_path": "off",
}


@pytest.fixture
def control_form(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="tok")
    s.set_value("sekret-token-123")
    s.save()
    Connection.objects.create(
        workspace=ws, name="svc", label="Svc", kind="http_bearer", config={"base_url": server}, secret=s
    )
    publish(
        ws,
        "commande",
        [node("e", action="echo", inputs={"value": "x"})],
        [],
        inputs=schema([], marques={"type": "array"}, lignes={"type": "array"}),
        user=world["designer"],
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/forms/", {"name": "C", "title": "C", "workflow": "commande", "environment": "test"}, format="json"
    )
    d = {
        "sections": [
            {
                "title": "S",
                "fields": [
                    control_field("marques", BRANDS, source=BRANDS_SOURCE),
                    control_field("lignes", LINES, prefill=LINES_PREFILL),
                ],
            }
        ]
    }
    make_form(world["designer"], "commande", d)
    attach = c.post(f"{BASE}/forms/{r.data['id']}/attach/", {"form": "commande"}, format="json")
    assert attach.status_code == 200, attach.data
    return PublicForm.objects.get(pk=r.data["id"])


def ask_kind(form, field, kind, params=None):
    return APIClient().post(
        f"/api/v1/forms/{form.token}/lookup/{field}/", {"kind": kind, "params": params or {}}, format="json"
    )


def test_a_control_gets_its_options_from_the_service_with_groups_and_disabled_ones(control_form):
    r = ask_kind(control_form, "marques", "options", {"q": "a"})
    assert r.status_code == 202 and SEEN == []
    assert lookups.process_one("w1")
    out = APIClient().get(f"/api/v1/forms/{control_form.token}/lookup/marques/{r.data['id']}/").data
    assert out == {
        "state": "done",
        "options": [
            {"value": "saab", "label": "Saab"},
            {"value": "ford", "label": "Ford", "group": "US Brands"},
            {"value": "plymouth", "label": "Plymouth", "group": "US Brands", "disabled": True},
        ],
    }
    assert SEEN == [{"path": "/brands?q=a", "auth": "Bearer sekret-token-123"}]
    # the options and the pre-fill are two different asks: a control without `prefill` has nothing to pre-fill
    assert ask_kind(control_form, "marques", "prefill").status_code == 404
    assert ask_kind(control_form, "marques", "autre").status_code == 404
    assert ask_kind(control_form, "lignes", "options").status_code == 404


def test_a_control_is_prefilled_with_a_value_the_server_has_checked_and_completed(control_form):
    r = ask_kind(control_form, "lignes", "prefill")
    assert r.status_code == 202
    assert lookups.process_one("w1")
    out = APIClient().get(f"/api/v1/forms/{control_form.token}/lookup/lignes/{r.data['id']}/").data
    assert out == {
        "state": "done",
        "value": [
            {"produit": "A", "qte": 2, "prix": 5, "total": 10},  # unknown key dropped, calculated value computed
            {"produit": "B", "qte": 1},
        ],
    }


def test_a_reply_that_does_not_respect_the_control_is_refused_whole_with_a_neutral_message(control_form):
    control_form.published_definition["sections"][0]["fields"][1]["prefill"]["path"] = "/badorder"
    control_form.save()
    r = ask_kind(control_form, "lignes", "prefill")
    assert lookups.process_one("w1")
    out = APIClient().get(f"/api/v1/forms/{control_form.token}/lookup/lignes/{r.data['id']}/").data
    assert out["state"] == "failed" and "beaucoup" not in str(out) and "Qte" not in str(out)


def test_attaching_refuses_a_control_with_an_unknown_connection(control_form, world):
    c = client_for(world["designer"])
    field = control_field("marques", BRANDS, source={**BRANDS_SOURCE, "connection": "nope"})
    make_form(world["designer"], "autre-c", {"sections": [{"title": "S", "fields": [field]}]})
    r = c.post(f"{BASE}/forms/{control_form.pk}/attach/", {"form": "autre-c"}, format="json")
    assert r.status_code == 400 and "nope" in str(r.data)


def test_the_options_of_a_control_field_carry_the_values_of_the_fields_they_open():
    field = {
        "control": {
            "spec": {
                "parts": [
                    {"key": "nom", "type": "text"},
                    {"key": "t", "type": "heading"},
                    {"key": "c", "type": "number", "compute": "1"},
                ]
            }
        }
    }
    assert (
        lookups.detail_keys(field) == ["nom"]
        and lookups.detail_keys(None) == []
        and lookups.detail_keys({"type": "select"}) == []
    )
    body = [{"value": "a", "nom": "Dupont"}, {"value": "b"}]
    out = lookups.to_options(body, {"value_path": "value"}, keys=lookups.detail_keys(field))
    assert out == [{"value": "a", "label": "a", "data": {"nom": "Dupont"}}, {"value": "b", "label": "b"}]
    assert lookups.to_options(body, {"value_path": "value"}) == [
        {"value": "a", "label": "a"},
        {"value": "b", "label": "b"},
    ]
