"""Inventaires Ansible gérés par la plate-forme (ADR 0037, lot 3) : validation, API (droits, isolation, audit), rendu, import, et résolution de `inventory_ref`
au moment de la prise en charge de l'exécution."""

import json
import uuid

import pytest
from rest_framework import serializers

from audit.models import AuditEvent
from catalog import inventory as lib
from catalog.models import Action, ActionVersion, Inventory
from runs import services
from runs.inventory_inputs import InventoryError, effective_inputs
from tenancy.models import Environment
from tests.conftest import client_for
from tests.wf_helpers import schema

BASE = "/api/v1/workspaces/team-a"

DOC = {
    "slug": "prod",
    "name": "Production",
    "description": "Serveurs de production",
    "variables": {"env": "prod"},
    "groups": [
        {"name": "web", "variables": {"port": 8080}, "children": []},
        {"name": "db", "variables": {}, "children": []},
        {"name": "france", "variables": {}, "children": ["web", "db"]},
    ],
    "hosts": [
        {"name": "web1.exemple.fr", "variables": {"ansible_host": "10.0.0.5"}, "enabled": True, "groups": ["web"]},
        {"name": "web2.exemple.fr", "variables": {}, "enabled": False, "groups": ["web"]},
        {"name": "db1", "variables": {}, "enabled": True, "groups": ["db"]},
        {"name": "seul", "variables": {}, "enabled": True, "groups": []},
    ],
}


def create(c, **over):
    return c.post(f"{BASE}/inventories/", {**DOC, **over}, format="json")


# --- validation ----------------------------------------------------------------------------------------------------------------------


def invalid(doc, field=None):
    with pytest.raises(serializers.ValidationError) as e:
        lib.clean_document(doc)
    if field:
        assert field in str(e.value.detail)
    return e.value


def test_a_valid_document_is_normalised():
    out = lib.clean_document(DOC)
    assert [g["name"] for g in out["groups"]] == ["web", "db", "france"] and out["groups"][2]["children"] == [
        "db",
        "web",
    ]
    assert out["hosts"][1]["enabled"] is False and out["variables"] == {"env": "prod"}


def test_an_empty_document_is_valid():
    assert lib.clean_document({}) == {"variables": {}, "groups": [], "hosts": []}


@pytest.mark.parametrize(
    ("doc", "field"),
    [
        ([], None),
        ({"groups": "web"}, "groups"),
        ({"groups": [{"name": "a b"}]}, "groups"),  # espace
        ({"groups": [{"name": "web-1"}]}, "groups"),  # tiret : refusé par Ansible
        ({"groups": [{"name": "all"}]}, "groups"),
        ({"groups": [{"name": "ungrouped"}]}, "groups"),
        ({"groups": [{"name": "a"}, {"name": "a"}]}, "groups"),
        ({"groups": [{"name": "a", "children": ["a"]}]}, "groups"),  # son propre parent
        ({"groups": [{"name": "a", "children": ["inconnu"]}]}, "groups"),
        ({"groups": [{"name": "a", "children": ["b"]}, {"name": "b", "children": ["a"]}]}, "boucle"),
        (
            {
                "groups": [
                    {"name": "a", "children": ["b"]},
                    {"name": "b", "children": ["c"]},
                    {"name": "c", "children": ["a"]},
                ]
            },
            "boucle",
        ),
        ({"hosts": [{"name": "-mauvais"}]}, "hosts"),
        ({"hosts": [{"name": "h; rm -rf /"}]}, "hosts"),
        ({"hosts": [{"name": "h"}, {"name": "h"}]}, "hosts"),
        ({"hosts": [{"name": "h", "groups": ["nulle-part"]}]}, "hosts"),
        ({"hosts": [{"name": "h", "enabled": "oui"}]}, "hosts"),
        ({"hosts": [{"name": "h" * 256}]}, "hosts"),
        ({"hosts": [{"name": f"h{i}"} for i in range(lib.MAX_HOSTS + 1)]}, "hosts"),
        ({"groups": [{"name": f"g{i}"} for i in range(lib.MAX_GROUPS + 1)]}, "groups"),
    ],
)
def test_bad_documents_are_refused(doc, field):
    invalid(doc, field)


@pytest.mark.parametrize(
    "key",
    [
        "ansible_password",
        "ansible_become_password",
        "ansible_ssh_pass",
        "ansible_ssh_private_key_file",
        "ansible_ssh_common_args",
        "db_passphrase",
        "api_token",
        "MY_SECRET",
        "vault_key",
    ],
)
def test_credentials_and_host_key_policy_cannot_live_in_variables(key):
    invalid({"variables": {key: "x"}}, "variables")
    invalid({"hosts": [{"name": "h", "variables": {key: "x"}}]}, "hosts")
    invalid({"groups": [{"name": "g", "variables": {key: "x"}}]}, "groups")


def test_harmless_lookalike_names_are_allowed():
    ok = lib.clean_document(
        {"variables": {"passenger_count": 3, "tokenizer": "x", "ansible_user": "deploy", "ansible_port": 2222}}
    )
    assert ok["variables"]["passenger_count"] == 3


@pytest.mark.parametrize(
    "variables",
    [
        {"1abc": 1},
        {"a-b": 1},
        {"": 1},
        {"x": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"},  # scan:allow - faux secret de test
        {"x": "AKIAABCDEFGHIJKLMNOP"},  # scan:allow - faux secret de test
        {"x": "y" * (lib.MAX_VARS_BYTES + 1)},
        "pas un objet",
    ],
)
def test_bad_variables_are_refused(variables):
    invalid({"variables": variables}, "variables")


# --- rendu -----------------------------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_render_follows_ansible_inventory_format_and_skips_disabled_hosts(world):
    inv = Inventory.objects.create(workspace=world["ws_a"], slug="prod", name="Production")
    lib.save_document(inv, lib.clean_document(DOC))
    out = lib.render(inv)["all"]
    assert out["vars"] == {"env": "prod"} and out["hosts"] == {"seul": {}}  # sans groupe : dans all.hosts
    assert out["children"]["web"] == {
        "hosts": {"web1.exemple.fr": {"ansible_host": "10.0.0.5"}},
        "vars": {"port": 8080},
    }
    assert "web2.exemple.fr" not in json.dumps(out)  # désactivée
    assert out["children"]["france"] == {"children": {"db": {}, "web": {}}}
    assert out["children"]["db"] == {"hosts": {"db1": {}}}


@pytest.mark.django_db
def test_document_round_trip(world):
    inv = Inventory.objects.create(workspace=world["ws_a"], slug="prod", name="Production", description="d")
    lib.save_document(inv, lib.clean_document(DOC))
    back = lib.load_document(inv)
    assert back["name"] == "Production" and back["hosts"][0]["name"] == "db1"  # triés par nom
    assert {h["name"] for h in back["hosts"]} == {h["name"] for h in DOC["hosts"]}
    lib.save_document(inv, lib.clean_document({"hosts": [{"name": "seule"}]}))  # remplacement en bloc
    assert [h["name"] for h in lib.load_document(inv)["hosts"]] == ["seule"] and lib.load_document(inv)["groups"] == []


# --- import YAML / JSON -----------------------------------------------------------------------------------------------------------------

YAML_ALL = """
all:
  vars: {env: prod}
  hosts:
    seul: {}
  children:
    web:
      hosts:
        web1: {ansible_host: 10.0.0.5}
        web2: {}
      vars: {port: 8080}
    france:
      children:
        web: {}
        db:
          hosts:
            db1: {}
"""


def test_import_yaml_with_all_and_nested_groups():
    doc = lib.clean_document(lib.parse_import(YAML_ALL))
    assert doc["variables"] == {"env": "prod"}
    by = {g["name"]: g for g in doc["groups"]}
    assert by["france"]["children"] == ["db", "web"] and by["web"]["variables"] == {"port": 8080}
    hosts = {h["name"]: h for h in doc["hosts"]}
    assert hosts["web1"]["groups"] == ["web"] and hosts["db1"]["groups"] == ["db"] and hosts["seul"]["groups"] == []


def test_import_json_with_groups_at_the_root():
    doc = lib.clean_document(
        lib.parse_import(json.dumps({"web": {"hosts": {"a": None, "b": {"x": 1}}}, "all": {"hosts": {"c": {}}}}))
    )
    assert {h["name"]: h["groups"] for h in doc["hosts"]} == {"a": ["web"], "b": ["web"], "c": []}


@pytest.mark.parametrize(
    "text", ["", "   ", "- une liste", "juste du texte", "a: [oups", "x" * (lib.MAX_IMPORT_BYTES + 1)]
)
def test_unreadable_imports_are_refused(text):
    with pytest.raises(serializers.ValidationError):
        lib.parse_import(text)


def test_import_cannot_smuggle_a_credential():
    with pytest.raises(serializers.ValidationError):
        lib.clean_document(lib.parse_import("all:\n  hosts:\n    h:\n      ansible_password: x\n"))


# --- API -------------------------------------------------------------------------------------------------------------------------------


def test_create_list_get_update_delete_with_audit(world):
    c = client_for(world["designer"])
    r = create(c)
    assert r.status_code == 201 and r.json()["slug"] == "prod" and len(r.json()["hosts"]) == 4
    listing = c.get(f"{BASE}/inventories/").json()
    assert listing == [{**listing[0], "slug": "prod", "name": "Production", "hosts": 4, "groups": 3}]
    got = {g["name"]: g for g in c.get(f"{BASE}/inventories/prod/").json()["groups"]}
    assert got["france"]["children"] == ["db", "web"]
    body = {**DOC, "name": "Prod 2", "hosts": [{"name": "unique"}], "groups": []}
    up = c.put(f"{BASE}/inventories/prod/", body, format="json")
    assert (
        up.status_code == 200
        and up.json()["name"] == "Prod 2"
        and [h["name"] for h in up.json()["hosts"]] == ["unique"]
    )
    assert c.delete(f"{BASE}/inventories/prod/").status_code == 204
    assert c.get(f"{BASE}/inventories/prod/").status_code == 404
    events = list(AuditEvent.objects.filter(action__startswith="inventory.").order_by("id"))
    assert [e.action for e in events] == ["inventory.create", "inventory.update", "inventory.delete"]
    assert events[0].data == {"hosts": 4, "groups": 3} and events[0].actor_label == "dana"


def test_a_viewer_can_read_but_not_write(world):
    create(client_for(world["designer"]))
    v = client_for(world["viewer"])
    assert v.get(f"{BASE}/inventories/").status_code == 200 and v.get(f"{BASE}/inventories/prod/").status_code == 200
    assert v.get(f"{BASE}/inventories/prod/rendered/").status_code == 200
    assert create(v, slug="autre").status_code == 403
    assert v.put(f"{BASE}/inventories/prod/", DOC, format="json").status_code == 403
    assert v.delete(f"{BASE}/inventories/prod/").status_code == 403
    assert v.post(f"{BASE}/inventories/import/", {"text": "a: {}"}, format="json").status_code == 403
    assert Inventory.objects.filter(slug="autre").count() == 0 and Inventory.objects.get(slug="prod").hosts.count() == 4


def test_an_inventory_is_invisible_from_another_workspace(world):
    create(client_for(world["designer"]))
    o = client_for(world["outsider"])
    # un espace dont on n'est pas membre « n'existe pas » (404, comme partout dans l'API)
    assert o.get(f"{BASE}/inventories/").status_code == 404 and o.get(f"{BASE}/inventories/prod/").status_code == 404
    assert (
        o.get("/api/v1/workspaces/team-b/inventories/prod/").status_code == 404
    )  # même identifiant, autre espace : rien
    assert o.delete(f"{BASE}/inventories/prod/").status_code == 404
    assert Inventory.objects.filter(slug="prod").count() == 1


def test_the_same_slug_is_possible_in_two_workspaces(world):
    create(client_for(world["designer"]))
    r = client_for(world["outsider"]).post("/api/v1/workspaces/team-b/inventories/", DOC, format="json")
    assert r.status_code == 201


def test_duplicate_slug_and_bad_requests_are_refused(world):
    c = client_for(world["designer"])
    assert create(c).status_code == 201
    assert create(c).status_code == 400
    assert create(c, slug="Pas Un Slug").status_code == 400
    assert create(c, slug="x", name="").status_code == 400
    assert create(c, slug="y", hosts=[{"name": "h", "variables": {"ansible_password": "x"}}]).status_code == 400
    assert Inventory.objects.filter(workspace=world["ws_a"]).count() == 1  # rien d'enregistré à moitié


def test_a_failing_update_changes_nothing(world):
    c = client_for(world["designer"])
    create(c)
    bad = c.put(
        f"{BASE}/inventories/prod/",
        {**DOC, "groups": [{"name": "a", "children": ["b"]}, {"name": "b", "children": ["a"]}]},
        format="json",
    )
    assert bad.status_code == 400
    assert (
        Inventory.objects.get(slug="prod").hosts.count() == 4 and Inventory.objects.get(slug="prod").groups.count() == 3
    )
    assert not AuditEvent.objects.filter(action="inventory.update").exists()


def test_rendered_endpoint_and_import_endpoint(world):
    c = client_for(world["designer"])
    create(c)
    rendered = c.get(f"{BASE}/inventories/prod/rendered/").json()
    assert rendered["all"]["children"]["web"]["hosts"] == {"web1.exemple.fr": {"ansible_host": "10.0.0.5"}}
    imp = c.post(f"{BASE}/inventories/import/", {"text": YAML_ALL}, format="json")
    assert imp.status_code == 200 and {h["name"] for h in imp.json()["hosts"]} == {"seul", "web1", "web2", "db1"}
    assert Inventory.objects.filter(workspace=world["ws_a"]).count() == 1  # l'import ne crée rien
    assert c.post(f"{BASE}/inventories/import/", {"text": "a: [oups"}, format="json").status_code == 400


def test_the_number_of_inventories_is_bounded(world, monkeypatch):
    from catalog import inventory_views

    monkeypatch.setattr(inventory_views, "MAX_INVENTORIES", 2)
    c = client_for(world["designer"])
    assert create(c, slug="a").status_code == 201 and create(c, slug="b").status_code == 201
    assert create(c, slug="c").status_code == 400


# --- `inventory_ref` résolu à la prise en charge ---------------------------------------------------------------------------------------


def ansible_execution(world, inputs, ws=None):
    ws = ws or world["ws_a"]
    action = Action.objects.create(workspace=ws, slug=f"pb-{uuid.uuid4().hex[:6]}", name="Playbook")
    v = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        runtime="ansible",
        source="- hosts: all\n  gather_facts: false\n  tasks: []\n",
        input_schema=schema(),
        output_schema={"type": "object"},
        required_tags=["ansible"],
    )
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=v,
        user=world["designer"],
        inputs=inputs,
    )
    return ex


def test_inventory_ref_becomes_the_rendered_inventory(world):
    inv = Inventory.objects.create(workspace=world["ws_a"], slug="prod", name="Production")
    lib.save_document(inv, lib.clean_document(DOC))
    ex = ansible_execution(world, {"inventory_ref": "prod", "limit": "web"})
    out = effective_inputs(ex)
    assert "inventory_ref" not in out and out["limit"] == "web"
    assert out["inventory"] == lib.render(inv)
    assert "inventory" not in ex.inputs  # la liste des machines n'est pas recopiée dans l'exécution enregistrée


def test_inventory_is_read_at_claim_time_not_at_enqueue_time(world):
    inv = Inventory.objects.create(workspace=world["ws_a"], slug="prod", name="Production")
    lib.save_document(inv, lib.clean_document({"hosts": [{"name": "avant"}]}))
    ex = ansible_execution(world, {"inventory_ref": "prod"})
    lib.save_document(inv, lib.clean_document({"hosts": [{"name": "apres"}]}))
    assert list(effective_inputs(ex)["inventory"]["all"]["hosts"]) == ["apres"]


@pytest.mark.parametrize(
    ("inputs", "code"),
    [
        ({"inventory_ref": "absent"}, "INVENTORY_NOT_FOUND"),
        ({"inventory_ref": 12}, "INVENTORY_NOT_FOUND"),
        ({"inventory_ref": "prod", "inventory": {"all": {}}}, "INVENTORY_CONFLICT"),
        ({"inventory_ref": "vide"}, "INVENTORY_EMPTY"),
        ({"inventory_ref": "tout-desactive"}, "INVENTORY_EMPTY"),
    ],
)
def test_inventory_ref_errors(world, inputs, code):
    for slug, doc in (("prod", DOC), ("vide", {}), ("tout-desactive", {"hosts": [{"name": "h", "enabled": False}]})):
        inv = Inventory.objects.create(workspace=world["ws_a"], slug=slug, name=slug)
        lib.save_document(inv, lib.clean_document(doc))
    ex = ansible_execution(world, inputs)
    with pytest.raises(InventoryError) as e:
        effective_inputs(ex)
    assert e.value.code == code


def test_an_inventory_of_another_workspace_is_never_found(world):
    other = Inventory.objects.create(workspace=world["ws_b"], slug="secret-b", name="B")
    lib.save_document(other, lib.clean_document({"hosts": [{"name": "interne-b"}]}))
    with pytest.raises(InventoryError) as e:
        effective_inputs(ansible_execution(world, {"inventory_ref": "secret-b"}))
    assert e.value.code == "INVENTORY_NOT_FOUND" and "interne-b" not in e.value.message


def test_other_runtimes_ignore_inventory_ref(world):
    ws = world["ws_a"]
    action = Action.objects.create(workspace=ws, slug="py", name="py")
    v = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        source="def run(i): return i",
        input_schema=schema(),
        output_schema={"type": "object"},
    )
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=v,
        user=world["designer"],
        inputs={"inventory_ref": "x"},
    )
    assert effective_inputs(ex) == {"inventory_ref": "x"}


def test_the_worker_fails_the_execution_clearly_when_the_inventory_is_missing(world, settings):
    from runs.runners import SubprocessRunner
    from runs.worker import process_one

    settings.ORCH_WORKER_TAGS = ["ansible"]
    ex = ansible_execution(world, {"inventory_ref": "absent"})
    assert process_one("w", SubprocessRunner())
    ex.refresh_from_db()
    assert (
        ex.status == "failed" and ex.error["code"] == "INVENTORY_NOT_FOUND" and "Inventaires" in ex.error["next_step"]
    )
