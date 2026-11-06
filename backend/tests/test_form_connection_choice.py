"""ADR 0026 (addendum) — la connexion d'une liste dynamique peut dépendre d'un champ, parmi une liste FERMÉE fixée par le concepteur."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from tests.conftest import client_for
from tests.form_helpers import make_form
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema
from triggers import formcontrols, formengine, lookups, shaping
from triggers.models import FormLookup, PublicForm
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"
SEEN: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization")})
        data = json.dumps({"groups": {"g1": ["a@ex.fr"], "g2": ["b@ex.fr", "c@ex.fr"]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


CHOICE = {
    "path": "/acl",
    "params": ["env"],
    "connection_from": "env",
    "connections": {"prod": "tailnet-prod", "test": "tailnet-test"},
    "items_path": "groups",
    "items_as": "entries",
    "sort": "label",
}


# --- la règle de résolution -------------------------------------------------------------------------------------------------
def test_a_value_picks_one_connection_of_the_closed_list_and_nothing_else():
    assert shaping.connection_for(CHOICE, {"env": "prod"}) == "tailnet-prod"
    assert shaping.connection_for(CHOICE, {"env": "test"}) == "tailnet-test"
    for bad in ({"env": "dev"}, {"env": "PROD"}, {"env": " prod"}, {"env": ""}, {}, {"env": ["prod"]}, {"env": None}):
        assert shaping.connection_for(CHOICE, bad) is None, bad  # exact match only: no guess, no default
    # une valeur qui ressemble à un nom de connexion de l'espace mais n'est pas dans la liste : refusée
    assert shaping.connection_for(CHOICE, {"env": "tailnet-prod"}) is None
    assert shaping.connection_for(CHOICE, {"env": "__class__"}) is None


def test_a_reading_without_choice_keeps_its_one_connection():
    assert shaping.connection_for({"connection": "svc", "path": "/x"}, {"env": "prod"}) == "svc"
    assert shaping.connection_names({"connection": "svc"}) == ["svc"]
    assert shaping.connection_names(CHOICE) == ["tailnet-prod", "tailnet-test"]


# --- validation -------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "patch,ok",
    [
        ({}, True),
        ({"connections": {"prod": "a"}}, True),
        ({"connection_from": None}, False),  # champ absent mais liste présente
        ({"connections": None}, False),
        ({"params": []}, False),  # le champ n'est pas dans les champs lus : sa valeur n'arriverait pas au serveur
        ({"connection_from": "1mauvaise"}, False),
        ({"connections": {}}, False),
        ({"connections": {f"v{i}": "c" for i in range(11)}}, False),  # 10 au plus
        ({"connections": {"": "c"}}, False),
        ({"connections": {"prod": ""}}, False),
        ({"connections": {"prod": 3}}, False),
        ({"connections": ["prod"]}, False),
    ],
)
def test_both_validators_check_the_closed_list(patch, ok):
    cfg = {**CHOICE, **patch}
    cfg = {k: v for k, v in cfg.items() if v is not None}
    field = {"key": "k", "label": "K", "type": "select", "source": cfg}
    assert (formengine._check_choices(field, "f") == {}) is ok
    assert (formcontrols._check_service({"connection": "", **cfg}, formcontrols.SOURCE_KEYS, "c") == {}) is ok


def test_the_connection_is_optional_when_a_closed_list_is_given_and_required_otherwise():
    with_list = {k: v for k, v in CHOICE.items()}
    assert formcontrols._check_service(with_list, formcontrols.SOURCE_KEYS, "c") == {}
    assert formcontrols._check_service({"path": "/x"}, formcontrols.SOURCE_KEYS, "c")  # ni connexion ni liste : refusé


# --- de bout en bout -----------------------------------------------------------------------------------------------------------
@pytest.fixture
def server(settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    SEEN.clear()
    cache.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def mk_connection(ws, name, token, base):
    s = Secret(workspace=ws, name=f"{name}-tok")
    s.set_value(token)
    s.save()
    Connection.objects.create(
        workspace=ws, name=name, label=name, kind="http_bearer", config={"base_url": base}, secret=s
    )


def definition(source):
    return {
        "sections": [
            {
                "title": "S",
                "fields": [
                    {
                        "key": "env",
                        "label": "Environnement",
                        "type": "select",
                        "options": [{"value": "prod", "label": "Prod"}, {"value": "test", "label": "Test"}],
                    },
                    {"key": "groupe", "label": "Groupe", "type": "select", "source": source},
                ],
            }
        ]
    }


@pytest.fixture
def form(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    mk_connection(ws, "tailnet-prod", "jeton-prod", server)
    mk_connection(ws, "tailnet-test", "jeton-test", server)
    publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.groupe }}"})],
        [],
        inputs=schema(["groupe", "env"], groupe={"type": "string"}, env={"type": "string"}),
        user=world["designer"],
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/forms/", {"name": "D", "title": "D", "workflow": "demande", "environment": "test"}, format="json"
    )
    make_form(world["designer"], "saisie", definition(CHOICE))
    assert c.post(f"{BASE}/forms/{r.data['id']}/attach/", {"form": "saisie"}, format="json").status_code == 200
    return PublicForm.objects.get(pk=r.data["id"])


def answer(form, params):
    r = APIClient().post(f"/api/v1/forms/{form.token}/lookup/groupe/", {"params": params}, format="json")
    assert r.status_code == 202, r.content
    assert lookups.process_one("w1") is True
    return APIClient().get(f"/api/v1/forms/{form.token}/lookup/groupe/{r.data['id']}/").data


def test_the_value_of_the_field_decides_which_connection_the_server_uses(form):
    out = answer(form, {"env": "prod"})
    assert out["state"] == "done" and [o["value"] for o in out["options"]] == ["g1", "g2"]
    assert SEEN[-1]["auth"] == "Bearer jeton-prod"  # la connexion « prod », avec SES identifiants
    answer(form, {"env": "test"})
    assert SEEN[-1]["auth"] == "Bearer jeton-test"
    assert [s["auth"] for s in SEEN] == [
        "Bearer jeton-prod",
        "Bearer jeton-test",
    ]  # chaque connexion lit pour elle-même (pas de cache croisé)


@pytest.mark.parametrize("value", ["dev", "PROD", "tailnet-prod", "", "../tailnet-prod"])
def test_a_value_outside_the_list_calls_nothing_and_gives_the_neutral_message(form, value):
    out = answer(form, {"env": value})
    assert out["state"] == "failed" and "tailnet" not in json.dumps(out)
    assert SEEN == []  # aucune requête partie : ni vers la connexion du concepteur, ni vers une autre
    assert FormLookup.objects.filter(status="failed").count() == 1


def test_attaching_refuses_a_form_when_one_connection_of_the_list_does_not_exist(world, server):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    mk_connection(ws, "tailnet-prod", "jeton-prod", server)  # « tailnet-test » n'existe pas
    publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.groupe }}"})],
        [],
        inputs=schema(["groupe", "env"], groupe={"type": "string"}, env={"type": "string"}),
        user=world["designer"],
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/forms/", {"name": "D", "title": "D", "workflow": "demande", "environment": "test"}, format="json"
    )
    make_form(world["designer"], "saisie", definition(CHOICE))
    res = c.post(f"{BASE}/forms/{r.data['id']}/attach/", {"form": "saisie"}, format="json")
    assert res.status_code == 400 and "tailnet-test" in json.dumps(res.data)
