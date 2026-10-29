import pytest
from rest_framework.test import APIClient

from runs import engine
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, make_action, node, publish, schema
from workflows.models import Workflow

URL = "/api/v1/workspaces/team-a/mcp/"


@pytest.fixture
def ws(world, settings):
    settings.ORCH_MCP_WAIT_SECONDS = 0.1
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    publish(
        world["ws_a"],
        "salue",
        [node("e", action="echo", inputs={"value": "Bonjour {{ inputs.nom }}"})],
        [],
        inputs=schema(["nom"], nom={"type": "string"}),
        outputs={"message": "{{ steps.e.output.value }}"},
        user=world["designer"],
    )
    publish(world["ws_a"], "interne", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    Workflow.objects.filter(slug="salue").update(mcp_exposed=True, description="Dit bonjour")
    return world["ws_a"]


def rpc(c, method, params=None, rid=1):
    return c.post(
        URL,
        {"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params is not None else {})},
        format="json",
    )


def test_handshake_listing_only_exposed_workflows(ws, world):
    c = client_for(world["designer"])
    init = rpc(
        c, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}}
    ).data["result"]
    assert (
        init["protocolVersion"] == "2025-03-26"
        and "tools" in init["capabilities"]
        and init["serverInfo"]["name"] == "fluxonata"
    )
    assert c.post(URL, {"jsonrpc": "2.0", "method": "notifications/initialized"}, format="json").status_code == 202
    tools = rpc(c, "tools/list").data["result"]["tools"]
    assert (
        [t["name"] for t in tools] == ["salue"]
        and tools[0]["inputSchema"]["required"] == ["nom"]
        and tools[0]["description"] == "Dit bonjour"
    )
    assert rpc(c, "ping").data["result"] == {}
    assert rpc(c, "inconnue").data["error"]["code"] == -32601
    assert c.post(URL, {"id": 1}, format="json").data["error"]["code"] == -32600


def test_calling_a_tool_runs_the_workflow_with_the_callers_rights(ws, world, monkeypatch):
    c = client_for(world["designer"])
    first = rpc(c, "tools/call", {"name": "salue", "arguments": {"nom": "Ada"}}).data["result"]
    assert (
        first["isError"] is False and '"status": "running"' in first["content"][0]["text"]
    )  # nobody advances it in this test
    original = engine.start_run

    def start_and_finish(**kw):
        run, created = original(**kw)
        drain(run)
        return run, created

    monkeypatch.setattr(engine, "start_run", start_and_finish)
    done = rpc(c, "tools/call", {"name": "salue", "arguments": {"nom": "Ada"}}).data["result"]
    assert done["isError"] is False and done["structuredContent"] == {"message": "Bonjour Ada"}
    bad = rpc(c, "tools/call", {"name": "salue", "arguments": {}}).data["result"]
    assert bad["isError"] is True and "invalides" in bad["content"][0]["text"]
    assert rpc(c, "tools/call", {"name": "interne", "arguments": {}}).data["result"]["isError"] is True  # not exposed
    assert (
        rpc(client_for(world["viewer"]), "tools/call", {"name": "salue", "arguments": {"nom": "x"}}).data["result"][
            "isError"
        ]
        is True
    )
    assert rpc(client_for(world["viewer"]), "tools/list").status_code == 200  # reading the list needs only view rights


def test_unauthenticated_and_foreign_workspace_callers_are_refused(ws, world):
    assert APIClient().post(URL, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, format="json").status_code in (401, 403)
    assert client_for(world["designer"]).post(
        "/api/v1/workspaces/team-b/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, format="json"
    ).status_code in (403, 404)


def test_mcp_client_integration_builds_json_rpc_and_reads_event_streams():
    from catalog import integrations
    from runs import http_node

    _, op = integrations.find("mcp", "call_tool")
    parts = integrations.render(op, {"name": "salue", "arguments": {"nom": "Ada"}})
    assert parts["body"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "salue", "arguments": {"nom": "Ada"}},
    }
    assert integrations.check() == []
    assert http_node.sse_last_json(
        'event: message\ndata: {"result": 1}\n\nevent: message\ndata: {"result": {"ok": true}}\n'
    ) == {"result": {"ok": True}}
    assert http_node.sse_last_json("pas de data") is None
