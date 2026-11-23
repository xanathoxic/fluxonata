"""MCP de conception et cache nommé (ADR 0032) : l'assistant sait concevoir des étapes « Cache », les voit simulées dans l'essai à blanc,
lit les clés existantes (jamais les valeurs) et ne peut pas régler la publication automatique (acte humain)."""

import json

import pytest
from django.utils import timezone

from ai import dryrun
from runs.models import CacheEntry
from runs.runners import SubprocessRunner
from tenancy.models import Environment
from tests.conftest import client_for
from tests.test_mcp_design import rpc
from tests.test_mcp_design_write import call, error_text
from tests.wf_helpers import ACTIONS, make_action, node, schema
from workflows.mcp_design_guide import OTHER_NODES, guide
from workflows.models import Workflow
from workflows.validation import validate

SECRETISH = "valeur-metier-confidentielle-4242"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def graph_of(*nodes, edges=()):
    return {"inputs": schema(), "nodes": list(nodes), "edges": list(edges), "outputs": {}}


def seed(ws, key="inventaire-serveurs", value=None, **extra):
    env = Environment.objects.get(workspace=ws, kind="test")
    return CacheEntry.objects.create(
        workspace=ws,
        environment=env,
        key=key,
        value=SECRETISH if value is None else value,
        version=2,
        size_bytes=30,
        updated_at=timezone.now(),
        **extra,
    )


# ---- le guide
def test_the_guide_has_a_cache_topic_listing_the_operations_and_the_human_only_setting():
    text = guide("cache")["guide"]
    for must in (
        "read",
        "write",
        "delete",
        "rebuild",
        "on_missing",
        "ttl_seconds",
        "sensitive",
        "list_caches",
        "PERSONNE",
    ):
        assert must in text, must
    assert "cache" in guide("workflow")["guide"]  # `cache` figure dans la liste des types, tirée du code


def test_the_cache_examples_of_the_guide_pass_the_validator(ws):
    examples = [v for k, v in OTHER_NODES.items() if k.startswith("cache")]
    assert {e["operation"] for e in examples} == {"read", "write"}
    for node_ in examples:
        g = {"inputs": schema(lignes={"type": "array"}), "nodes": [node_], "edges": [], "outputs": {}}
        assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == [], (node_["id"], validate(g, ws, "x"))


# ---- création par l'assistant : un brouillon, validé comme pour tout le monde
def test_an_assistant_creates_a_workflow_with_cache_steps_as_a_draft(ws, world):
    c = client_for(world["designer"])
    reader = {**OTHER_NODES["cache : lire (reconstruit la valeur si elle manque)"]}
    _, out = call(c, "create_workflow", {"slug": "lecteur", "name": "Lecteur", "graph": graph_of(reader)})
    assert out["status"] == "brouillon" and [i for i in out["issues"] if i["level"] == "error"] == []
    stored = Workflow.objects.get(workspace=ws, slug="lecteur")
    assert (
        stored.draft_graph["nodes"][0]["on_missing"] == "rebuild" and stored.versions.count() == 0
    )  # jamais publié par l'assistant


def test_an_invalid_cache_step_is_refused_with_a_usable_error(ws, world):
    c = client_for(world["designer"])
    bad = node("c", "cache", operation="read", key="a b", on_missing="boom")
    res, out = call(c, "create_workflow", {"slug": "mauvais", "name": "M", "graph": graph_of(bad)})
    codes = {i["code"] for i in (out or {}).get("issues", [])} if out else set()
    assert "bad_cache" in codes or "bad_cache" in error_text(res)


# ---- lecture : clés et producteurs, jamais les valeurs
def test_list_caches_gives_keys_and_producers_but_never_values(ws, world):
    seed(ws)
    seed(ws, key="secret-rh", sensitive=True)
    Workflow.objects.create(workspace=ws, slug="inventaire", name="Inventaire", cache_key="inventaire-serveurs")
    _, out = call(client_for(world["designer"]), "list_caches", {})
    rows = {r["key"]: r for r in out["results"]}
    assert rows["inventaire-serveurs"]["producer"] == "inventaire" and rows["inventaire-serveurs"]["version"] == 2
    assert rows["secret-rh"]["sensitive"] is True and rows["secret-rh"]["producer"] is None
    assert SECRETISH not in json.dumps(out) and all("value" not in r for r in rows.values())
    _, filtered = call(client_for(world["designer"]), "list_caches", {"query": "inventaire"})
    assert [r["key"] for r in filtered["results"]] == ["inventaire-serveurs"]


def test_list_caches_is_isolated_per_workspace(ws, world):
    seed(ws)
    other = world["ws_b"]
    seed(other, key="autre-espace")
    _, out = call(client_for(world["designer"]), "list_caches", {})
    assert [r["key"] for r in out["results"]] == ["inventaire-serveurs"]


def test_get_workflow_shows_the_publication_setting_read_only(ws, world):
    Workflow.objects.create(workspace=ws, slug="p", name="P", cache_key="k", cache_ttl_seconds=60, cache_sensitive=True)
    Workflow.objects.create(workspace=ws, slug="q", name="Q")
    c = client_for(world["designer"])
    assert call(c, "get_workflow", {"slug": "p"})[1]["cache"] == {"key": "k", "ttl_seconds": 60, "sensitive": True}
    assert call(c, "get_workflow", {"slug": "q"})[1]["cache"] is None


def test_the_assistant_cannot_change_the_automatic_publication(ws, world):
    Workflow.objects.create(workspace=ws, slug="p", name="P")
    c = client_for(world["designer"])
    tools = {t["name"] for t in rpc(c, "tools/list").json()["result"]["tools"]}
    assert not any("cache" in t and t != "list_caches" for t in tools)  # aucun outil d'écriture ni d'invalidation
    res, _ = call(c, "update_item_info", {"kind": "workflow", "slug": "p", "cache_key": "pirate"})
    assert Workflow.objects.get(slug="p").cache_key == ""  # champ inconnu : ignoré ou refusé, jamais appliqué
    assert res is not None


# ---- l'essai à blanc simule, ne touche pas au vrai cache
def test_the_dry_run_simulates_cache_steps_and_never_touches_the_real_cache(ws, world):
    seed(ws)
    before = list(CacheEntry.objects.values_list("pk", "version", "value"))
    nodes = [
        node("r", "cache", operation="read", key="inventaire-serveurs", on_missing="rebuild"),
        node("w", "cache", operation="write", key="inventaire-serveurs", value="{{ steps.r.output.found }}"),
        node("d", "cache", operation="delete", key="inventaire-serveurs"),
        node(
            "t",
            "transform",
            op="set",
            fields={"trouve": "{{ steps.r.output.found }}", "v": "{{ steps.w.output.version }}"},
        ),
    ]
    edges = [{"from": "r", "to": "w"}, {"from": "w", "to": "d"}, {"from": "d", "to": "t"}]
    result = dryrun.dry_run(graph_of(*nodes, edges=edges), ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    steps = {s["id"]: s for sc in result["scenarios"] for s in sc["steps"]}
    assert all(steps[i]["state"] == "simulated" for i in "rwd")
    assert "Cache simulé" in json.dumps(result, ensure_ascii=False, default=str)
    assert list(CacheEntry.objects.values_list("pk", "version", "value")) == before  # le vrai cache est intact
    assert not [p for p in result.get("problems", []) if p.get("level") == "error"]
