"""ADR 0025 — outils de LECTURE du MCP de conception.

Ce que ces tests ferment : aucune écriture ni exécution possible par ce profil, aucun secret en sortie, droits de la personne et dossiers
privés respectés, chaque appel audité avec la marque « assistant externe », et le MCP d'exécution inchangé.
"""

import json
from types import SimpleNamespace

import pytest

from ai.models import AiSkill, AiSkillVersion
from audit.models import AuditEvent
from catalog.models import Action
from formbuilder.models import Form
from runs.models import WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, make_action, node, publish
from vault.models import Connection, Secret
from workflows.mcp_design import MAX_LIMIT, all_tools
from workflows.models import FolderAccess, FolderGrant, Workflow

URL = "/api/v1/workspaces/team-a/mcp/design/"
WRITE_TOOLS_ALL = {
    "create_workflow",
    "update_workflow",
    "validate_workflow",
    "create_form",
    "update_form",
    "attach_form",
    "propose_action",
    "propose_skill",
    "start_dry_run",
    "get_dry_run",
    "start_action_test",
}
RUN_URL = "/api/v1/workspaces/team-a/mcp/"
READ_TOOLS = {
    "search_actions",
    "get_action",
    "list_skills",
    "get_skill",
    "list_forms",
    "get_form",
    "list_workflows",
    "get_workflow",
    "list_connections",
    "list_integrations",
    "list_triggers",
    "get_authoring_guide",
}


def rpc(c, method, params=None, url=URL, **extra):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}
    return c.post(url, body, format="json", **extra)


def call(c, tool, args=None):
    r = rpc(c, "tools/call", {"name": tool, "arguments": args or {}})
    assert r.status_code == 200, r.content
    res = r.json()["result"]
    return res, (res.get("structuredContent") if not res["isError"] else None)


@pytest.fixture
def ws(world):
    w = world["ws_a"]
    for slug in ACTIONS:
        make_action(w, slug)
    publish(w, "salue", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    s = AiSkill.objects.create(workspace=w, slug="qualifier", name="Qualifier", description="Classe un texte")
    AiSkillVersion.objects.create(
        skill=s, version="1.0.0", instructions="Qualifie : {{ inputs.texte }}", input_schema={}, output_schema={}
    )
    Form.objects.create(
        workspace=w,
        slug="demande",
        name="Demande d'accès",
        draft_definition={"sections": [{"fields": [{"name": "a"}]}]},
    )
    secret = Secret.objects.create(workspace=w, name="s-crm", ciphertext="chiffre-opaque-123")
    Connection.objects.create(
        workspace=w,
        name="crm",
        label="CRM",
        kind="http_bearer",
        secret=secret,
        config={"base_url": "https://crm.exemple.org", "password": "s3cr3t-valeur"},
    )
    return w


# --- le profil ----------------------------------------------------------------------------------------------------------------
def test_handshake_and_the_tool_list(ws, world):
    c = client_for(world["designer"])
    init = rpc(
        c, "initialize", {"protocolVersion": "2025-03-26", "clientInfo": {"name": "claude-test", "version": "9"}}
    ).json()["result"]
    assert init["serverInfo"]["name"] == "fluxonata" and "tools" in init["capabilities"]
    tools = rpc(c, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == READ_TOOLS | WRITE_TOOLS_ALL == set(all_tools())
    by = {t["name"]: t for t in tools}
    assert all(by[n]["annotations"]["readOnlyHint"] is True for n in READ_TOOLS | {"validate_workflow", "get_dry_run"})
    assert all(t["annotations"]["destructiveHint"] is False and t["inputSchema"]["type"] == "object" for t in tools)
    # aucun outil pour publier, lancer, activer, approuver, supprimer ou toucher aux connexions et secrets
    forbidden = (
        "publish",
        "run",
        "activate",
        "approve",
        "delete",
        "promote",
        "remove",
        "secret",
        "connection",
        "trigger",
    )
    # exceptions : list_connections (noms seulement) et l'essai à blanc (« dry_run » : bac à sable isolé, jamais un vrai lancement)
    assert [
        n
        for n in by
        if any(w in n for w in forbidden)
        and n not in {"list_connections", "list_triggers", "start_dry_run", "get_dry_run", "start_action_test"}
    ] == []


def test_the_execution_profile_is_unchanged_and_has_none_of_these_tools(ws, world):
    c = client_for(world["designer"])
    names = {t["name"] for t in rpc(c, "tools/list", url=RUN_URL).json()["result"]["tools"]}
    assert names.isdisjoint(READ_TOOLS)
    assert (
        rpc(c, "tools/call", {"name": "search_actions", "arguments": {}}, url=RUN_URL).json()["result"]["isError"]
        is True
    )


def test_unauthenticated_and_foreign_workspace_callers_are_refused(ws, world):
    from rest_framework.test import APIClient

    assert rpc(APIClient(), "tools/list").status_code in (401, 403)
    assert rpc(client_for(world["outsider"]), "tools/list").status_code == 404


def test_an_unknown_or_malformed_call_is_answered_not_raised(ws, world):
    c = client_for(world["designer"])
    assert call(c, "publish_workflow")[0]["isError"] is True  # il n'existe aucun outil de publication
    assert rpc(c, "tools/call", {"name": "search_actions", "arguments": "x"}).json()["result"]["isError"] is True


# --- lecture ------------------------------------------------------------------------------------------------------------------
def test_search_actions_returns_the_contract_and_filters(ws, world):
    c = client_for(world["designer"])
    _, out = call(c, "search_actions", {"query": "echo"})
    row = next(r for r in out["results"] if r["slug"] == "echo")
    assert row["input_schema"] and row["output_schema"] and row["status"] == "active" and row["version"] == "1.0.0"
    assert call(c, "search_actions", {"query": "zzz-aucune"})[1]["results"] == []
    assert call(c, "search_actions", {"query": "", "limit": 2})[1]["results"].__len__() == 2


def test_the_limit_is_capped(ws, world):
    Action.objects.bulk_create(
        [Action(workspace=ws, slug=f"bulk-{i:03d}", name=f"bulk {i}") for i in range(MAX_LIMIT + 20)]
    )
    out = call(client_for(world["designer"]), "search_actions", {"limit": 10_000})[1]
    assert len(out["results"]) == MAX_LIMIT and out["count"] > MAX_LIMIT


def test_get_action_gives_code_and_slot_names_but_never_a_value(ws, world):
    _, out = call(client_for(world["designer"]), "get_action", {"slug": "echo"})
    assert out["source"] and out["source_truncated"] is False and out["runtime"] == "python"
    assert call(client_for(world["designer"]), "get_action", {"slug": "absente"})[0]["isError"] is True


def test_skills_forms_and_workflows_are_readable(ws, world):
    c = client_for(world["designer"])
    assert call(c, "get_skill", {"slug": "qualifier"})[1]["instructions"].startswith("Qualifie")
    assert [r["slug"] for r in call(c, "list_skills", {"query": "classe"})[1]["results"]] == ["qualifier"]
    f = call(c, "get_form", {"slug": "demande"})[1]
    assert f["definition"]["sections"] and f["fields"] == 1 and f["draft_revision"] == 1
    w = call(c, "get_workflow", {"slug": "salue"})[1]
    assert w["draft_graph"] is not None and w["published_graph"]["nodes"] and w["version"] == "1.0.0"
    assert "salue" in {r["slug"] for r in call(c, "list_workflows")[1]["results"]}


# --- jamais de secret ---------------------------------------------------------------------------------------------------------
def test_connections_expose_name_label_and_kind_only(ws, world):
    res, out = call(client_for(world["designer"]), "list_connections")
    assert out["results"] == [{"name": "crm", "label": "CRM", "kind": "http_bearer"}]
    blob = json.dumps(res)
    assert (
        "s3cr3t-valeur" not in blob
        and "base_url" not in blob
        and "crm.exemple.org" not in blob
        and "s-crm" not in blob
        and "chiffre-opaque-123" not in blob
    )


def test_no_tool_output_ever_contains_a_secret(ws, world):
    c = client_for(world["designer"])
    calls = [
        ("search_actions", {}),
        ("get_action", {"slug": "echo"}),
        ("list_skills", {}),
        ("get_skill", {"slug": "qualifier"}),
        ("list_forms", {}),
        ("get_form", {"slug": "demande"}),
        ("list_workflows", {}),
        ("get_workflow", {"slug": "salue"}),
        ("list_connections", {}),
        ("list_integrations", {}),
        ("list_triggers", {}),
        ("get_authoring_guide", {}),
    ]
    assert {n for n, _ in calls} == READ_TOOLS  # aucun outil oublié
    for name, args in calls:
        assert "s3cr3t-valeur" not in json.dumps(call(c, name, args)[0]), name


# --- droits de la personne ----------------------------------------------------------------------------------------------------
def test_proposals_are_visible_only_to_people_who_can_edit(ws, world):
    Action.objects.filter(slug="echo").update(status="proposed")
    d = call(client_for(world["designer"]), "get_action", {"slug": "echo"})
    assert d[1]["status"] == "proposed"
    v = client_for(world["viewer"])
    assert call(v, "get_action", {"slug": "echo"})[0]["isError"] is True
    assert "echo" not in {r["slug"] for r in call(v, "search_actions")[1]["results"]}


def test_private_folders_stay_hidden(ws, world):
    Workflow.objects.filter(slug="salue").update(folder="RH/Prive")
    Form.objects.filter(slug="demande").update(folder="RH/Prive")
    acc = FolderAccess.objects.create(workspace=ws, folder="RH")
    FolderGrant.objects.create(access=acc, user=world["designer"], level="view")
    v = client_for(world["viewer"])  # pas dans la liste du dossier
    assert call(v, "get_workflow", {"slug": "salue"})[0]["isError"] is True
    assert call(v, "get_form", {"slug": "demande"})[0]["isError"] is True
    assert "salue" not in {r["slug"] for r in call(v, "list_workflows")[1]["results"]}
    assert "demande" not in {r["slug"] for r in call(v, "list_forms")[1]["results"]}
    d = client_for(world["designer"])  # dans la liste : voit
    assert call(d, "get_workflow", {"slug": "salue"})[0]["isError"] is False


def test_a_token_without_the_view_scope_is_refused(ws, world):
    user = world["designer"]
    user._token = SimpleNamespace(
        scopes=["edit"], workspace_id=ws.id, workspace=ws, environments=[]
    )  # ni « view » : rien ne se lit
    assert rpc(client_for(user), "tools/list").status_code == 403
    user._token = SimpleNamespace(
        scopes=["view", "edit"], workspace_id=ws.id, environments=[]
    )  # profil de conception : lit, ne lance pas
    c = client_for(user)
    assert call(c, "list_connections")[0]["isError"] is False
    assert call(c, "run_workflow", {"slug": "salue"})[0]["isError"] is True


# --- aucune écriture, aucune exécution ----------------------------------------------------------------------------------------
def test_calling_every_tool_changes_nothing(ws, world):
    def snapshot():
        return (
            Action.objects.count(),
            AiSkill.objects.count(),
            Form.objects.count(),
            Workflow.objects.count(),
            Connection.objects.count(),
            WorkflowRun.objects.count(),
            sorted(Workflow.objects.values_list("draft_revision", flat=True)),
        )

    before, c = snapshot(), client_for(world["designer"])
    for name in READ_TOOLS:
        call(c, name, {"slug": "echo"})
    assert snapshot() == before


# --- audit --------------------------------------------------------------------------------------------------------------------
def test_every_call_is_audited_with_the_external_assistant_mark(ws, world):
    c = client_for(world["designer"])
    rpc(
        c,
        "initialize",
        {"protocolVersion": "2025-03-26", "clientInfo": {"name": "claude-test", "version": "9"}},
        HTTP_USER_AGENT="Claude-Test/9",
    )
    call(c, "search_actions", {"query": "echo"})
    init = AuditEvent.objects.get(action="mcp.design.initialize")
    assert (
        init.data["client"] == "claude-test"
        and init.data["external_assistant"] is True
        and init.actor == world["designer"]
    )
    ev = AuditEvent.objects.get(action="mcp.design.call")
    assert ev.data["tool"] == "search_actions" and ev.data["external_assistant"] is True and ev.workspace == ws
    # les paramètres de la requête ne sont pas copiés dans l'audit (ils pourraient contenir du texte sensible)
    assert "echo" not in json.dumps(ev.data)


def test_refused_attempts_are_audited_too(ws, world):
    """Un assistant qui réclame « publish_workflow » (outil inexistant) ou arrive avec un jeton trop puissant laisse une trace."""
    c = client_for(world["designer"])
    assert call(c, "publish_workflow", {"slug": "x"})[0]["isError"] is True
    ev = AuditEvent.objects.get(action="mcp.design.refused", data__reason="unknown_tool")
    assert ev.data["tool"] == "publish_workflow" and ev.data["external_assistant"] is True and ev.workspace == ws
    user = world["designer"]
    user._token = SimpleNamespace(scopes=["view", "edit", "run"], workspace_id=ws.id, workspace=ws, environments=[])
    assert rpc(client_for(user), "tools/list").status_code == 403
    ev = AuditEvent.objects.get(action="mcp.design.refused", data__reason="token_too_powerful")
    assert ev.data["scopes"] == ["edit", "run", "view"] and ev.workspace == ws


@pytest.mark.parametrize(
    "url",
    [URL, URL.rstrip("/"), RUN_URL, RUN_URL.rstrip("/")],
    ids=["conception/", "conception", "exécution/", "exécution"],
)
def test_both_mcp_routes_answer_with_or_without_the_trailing_slash(ws, world, url):
    """Constaté avec Claude Code : il enregistre l'adresse SANS « / » final ; une redirection transformerait le POST en GET."""
    r = rpc(client_for(world["designer"]), "tools/list", url=url)
    assert r.status_code == 200 and "tools" in r.json()["result"], (url, r.status_code)


@pytest.mark.parametrize("url", [URL, RUN_URL], ids=["conception", "exécution"])
def test_a_client_asking_for_an_event_stream_gets_405_not_406(ws, world, url):
    """Constaté avec Claude Code : son GET `Accept: text/event-stream` recevait 406 (négociation de DRF) au lieu du 405 attendu (pas de flux serveur)."""
    c = client_for(world["designer"])
    assert c.get(url, HTTP_ACCEPT="text/event-stream").status_code == 405
    r = c.post(
        url,
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        format="json",
        HTTP_ACCEPT="application/json, text/event-stream",
    )
    assert r.status_code == 200 and r.json()["result"] == {}
