"""ADR 0026 — mettre en forme la réponse d'un service : objet → options, filtre, libellé calculé, tri, `{champ}` dans la lecture."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from tests.conftest import client_for
from tests.form_helpers import make_form
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema
from triggers import formcontrols, formengine, lookups
from triggers.models import FormLookup, PublicForm
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"
SEEN: list[str] = []
POLICY = {
    "groups": {
        "group:ops": ["bob@ex.fr", "alice@ex.fr"],
        "group:dev": ["carl@ex.fr"],
        "autonome": [],
    },
    "hosts": {"srv": "100.64.0.1"},
    "acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}],
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append(self.path)
        if self.path.startswith("/acl"):
            out = POLICY
        elif self.path.startswith("/big"):
            out = {"groups": {f"group:g{i}": ["x@ex.fr"] for i in range(40_000)}}  # bien plus d'1 Mo
        elif self.path.startswith("/people"):
            out = [{"prenom": "Alice", "nom": "Martin", "id": 1}, {"prenom": "Bob", "nom": "Durand", "id": 2}]
        else:
            out = {}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


# --- la mise en forme seule (calcul pur) -------------------------------------------------------------------------------------
def opts(body, **cfg):
    return lookups.to_options(body, cfg, params=cfg.pop("_params", None))


def test_an_object_becomes_options_from_its_keys_and_a_filter_keeps_the_groups_only():
    cfg = {"items_path": "groups", "items_as": "entries", "where": "starts_with(item.key, 'group:')", "sort": "label"}
    assert lookups.to_options(POLICY, cfg) == [
        {"value": "group:dev", "label": "group:dev"},
        {"value": "group:ops", "label": "group:ops"},
    ]
    desc = lookups.to_options(POLICY, {**cfg, "sort": "label_desc"})
    assert [o["value"] for o in desc] == ["group:ops", "group:dev"]


def test_a_placeholder_in_the_path_reads_the_chosen_entry_and_is_one_key_only():
    cfg = {"items_path": "groups.{groupe}", "params": ["groupe"]}
    assert [o["value"] for o in lookups.to_options(POLICY, cfg, params={"groupe": "group:ops"})] == [
        "bob@ex.fr",
        "alice@ex.fr",
    ]
    nested = {"a": {"b": ["x"]}, "a.b": ["y"]}
    # un point dans la valeur n'ouvre pas un second niveau : « a.b » est UNE clé
    assert [
        o["value"] for o in lookups.to_options(nested, {"items_path": "{k}", "params": ["k"]}, params={"k": "a.b"})
    ] == ["y"]
    with pytest.raises(ValueError):
        lookups.to_options(POLICY, cfg, params={})  # sans valeur, rien n'est lu


def test_a_label_expression_builds_the_label_and_falls_back_to_the_value():
    people = [{"prenom": "Alice", "nom": "Martin", "id": 1}, {"nom": "Seul", "id": 2}]
    cfg = {"value_path": "id", "label_expr": "item.prenom + ' ' + upper(item.nom)"}
    assert lookups.to_options(people, cfg) == [
        {"value": "1", "label": "Alice MARTIN"},
        {"value": "2", "label": "2"},  # prenom absent : l'expression échoue, on retombe sur la valeur
    ]


def test_a_filter_that_cannot_read_an_item_leaves_it_out_and_a_sort_reads_everything_first():
    people = [{"id": i, "nom": n, "actif": a} for i, (n, a) in enumerate([("Zoé", True), ("Ada", True), ("Max", None)])]
    cfg = {"value_path": "id", "label_path": "nom", "where": "item.actif", "sort": "label"}
    assert [o["label"] for o in lookups.to_options(people, cfg)] == ["Ada", "Zoé"]
    many = [{"id": i, "nom": f"n{999 - i:04d}"} for i in range(500)]  # lus du plus grand au plus petit
    out = lookups.to_options(many, {"value_path": "id", "label_path": "nom", "sort": "label"})
    # le tri se fait AVANT la coupe à 200 : on garde les 200 plus petits (n0500…n0699), pas les 200 premiers lus
    assert [out[0]["label"], out[-1]["label"], len(out)] == ["n0500", "n0699", lookups.MAX_OPTIONS]


def test_a_list_is_still_required_unless_entries_are_asked_for():
    with pytest.raises(ValueError):
        lookups.to_options(POLICY, {"items_path": "groups"})
    with pytest.raises(ValueError):
        lookups.to_options(POLICY, {"items_path": "hosts.srv", "items_as": "entries"})


# --- validation : ce que le concepteur peut écrire ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extra,ok",
    [
        ({"items_as": "entries", "where": "starts_with(item.key, 'g')", "sort": "label"}, True),
        ({"items_path": "groups.{groupe}", "params": ["groupe"]}, True),
        ({"items_path": "groups.{groupe}"}, False),  # {groupe} pas dans les champs lus
        ({"items_as": "table"}, False),
        ({"sort": "au hasard"}, False),
        ({"where": "item.nom +"}, False),  # syntaxe
        ({"where": "inputs.x == 1"}, False),  # seul « item » est connu
        ({"label_expr": "boom(item.x)"}, False),  # fonction inconnue
        ({"where": "x" * 400}, False),
    ],
)
def test_both_validators_accept_or_refuse_the_shaping_keys(extra, ok):
    cfg = {"connection": "svc", "path": "/acl", "items_path": "groups", **extra}
    field = {"key": "k", "label": "K", "type": "select", "source": cfg}
    assert (formengine._check_choices(field, "f") == {}) is ok
    assert (formcontrols._check_service(cfg, formcontrols.SOURCE_KEYS, "c") == {}) is ok


# --- de bout en bout : deux listes en cascade, un gros fichier lu une seule fois -------------------------------------------------
@pytest.fixture
def server(settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    SEEN.clear()
    cache.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


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
        [node("e", action="echo", inputs={"value": "{{ inputs.membre }}"})],
        [],
        inputs=schema(["groupe", "membre"], groupe={"type": "string"}, membre={"type": "string"}),
        user=world["designer"],
    )
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/forms/", {"name": "D", "title": "D", "workflow": "demande", "environment": "test"}, format="json"
    )
    groupes = {
        "connection": "svc",
        "path": "/acl",
        "items_path": "groups",
        "items_as": "entries",
        "where": "starts_with(item.key, 'group:')",
        "sort": "label",
    }
    membres = {
        "connection": "svc",
        "path": "/acl",
        "params": ["groupe"],
        "items_path": "groups.{groupe}",
        "sort": "label",
    }
    d = {
        "sections": [
            {
                "title": "S",
                "fields": [
                    {"key": "groupe", "label": "Groupe", "type": "select", "source": groupes},
                    {"key": "membre", "label": "Membre", "type": "select", "source": membres},
                ],
            }
        ]
    }
    make_form(world["designer"], "saisie", d)
    assert c.post(f"{BASE}/forms/{r.data['id']}/attach/", {"form": "saisie"}, format="json").status_code == 200
    return PublicForm.objects.get(pk=r.data["id"])


def answer(form, field, params=None):
    r = APIClient().post(f"/api/v1/forms/{form.token}/lookup/{field}/", {"params": params or {}}, format="json")
    assert r.status_code == 202, r.content
    assert lookups.process_one("w1") is True
    return APIClient().get(f"/api/v1/forms/{form.token}/lookup/{field}/{r.data['id']}/").data


def test_groups_then_members_in_cascade_with_one_download(form):
    first = answer(form, "groupe")
    assert first["options"] == [
        {"value": "group:dev", "label": "group:dev"},
        {"value": "group:ops", "label": "group:ops"},
    ]
    second = answer(form, "membre", {"groupe": "group:ops"})
    assert [o["value"] for o in second["options"]] == ["alice@ex.fr", "bob@ex.fr"]
    assert SEEN == ["/acl"]  # le fichier n'est lu qu'une fois : la deuxième liste réutilise la réponse récente
    assert answer(form, "membre", {"groupe": "group:inconnu"})["state"] == "failed"  # pas d'entrée : message neutre


def test_a_reply_bigger_than_the_bound_is_refused_whole_with_a_neutral_message(form):
    form.published_definition["sections"][0]["fields"][0]["source"]["path"] = "/big"
    form.save()
    out = answer(form, "groupe")
    assert (
        out["state"] == "failed"
        and "big" not in json.dumps(out)
        and FormLookup.objects.filter(status="failed").count() == 1
    )


def test_the_designer_preview_reads_a_pasted_reply_with_the_same_shaping(world):
    c = client_for(world["designer"])
    spec = {"version": 1, "mode": "choice", "dynamic": True, "parts": []}
    body = {
        "kind": "options",
        "spec": spec,
        "config": {
            "connection": "svc",
            "path": "/acl",
            "params": ["groupe"],
            "items_path": "groups.{groupe}",
        },
        "sample": POLICY,
        "params": {"groupe": "group:dev"},
    }
    r = c.post(f"{BASE}/control-library/source-preview/", body, format="json")
    assert r.status_code == 200 and [o["value"] for o in r.data["options"]] == ["carl@ex.fr"], r.data
    no_value = c.post(f"{BASE}/control-library/source-preview/", {**body, "params": {}}, format="json")
    assert "valeur d'essai" in no_value.data["problem"]
