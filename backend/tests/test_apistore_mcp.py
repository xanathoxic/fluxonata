"""Point d'accès MCP d'un produit de l'API store : même porte que l'appel REST (clé, plan, quotas, audit)."""

import pytest
from rest_framework.test import APIClient

from apistore.models import UsageEvent
from tests.apistore_helpers import BASE, active_key

URL = f"{BASE}/store/p1/mcp/"


def rpc(raw, method, params=None, url=URL, rid=1):
    return APIClient().post(
        url,
        {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {raw}" if raw else "",
    )


@pytest.fixture(autouse=True)
def _no_wait(settings):
    settings.ORCH_MCP_WAIT_SECONDS = 0  # pas de worker dans ce test : l'appel répond « en cours »


def test_the_product_workflows_are_listed_as_tools_for_a_valid_key(store):
    _, _, raw = active_key(store)
    r = rpc(raw, "tools/list")
    assert r.status_code == 200
    assert [t["name"] for t in r.json()["result"]["tools"]] == ["hello"]
    init = rpc(raw, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert init["capabilities"] == {"tools": {"listChanged": False}}


@pytest.mark.parametrize("bad", ["", "fxs_inconnue", "orch_jeton-d-api"])
def test_no_valid_key_no_tools(store, bad):
    assert rpc(bad, "tools/list").status_code == 401


def test_a_call_is_counted_audited_and_never_exceeds_the_plan(store):
    _, _, raw = active_key(store, per_minute=1)
    first = rpc(raw, "tools/call", {"name": "hello", "arguments": {}}).json()["result"]
    assert first["isError"] is False and "run_id" in first["content"][0]["text"]
    assert UsageEvent.objects.filter(outcome="accepted").count() == 1
    second = rpc(raw, "tools/call", {"name": "hello", "arguments": {}}).json()["result"]
    assert second["isError"] is True and "rate_limited" in second["content"][0]["text"]
    assert UsageEvent.objects.filter(outcome="rate_limited").count() == 1


def test_a_workflow_outside_the_product_is_refused_like_in_rest(store):
    _, _, raw = active_key(store)
    res = rpc(raw, "tools/call", {"name": "autre", "arguments": {}}).json()["result"]
    assert res["isError"] is True and "forbidden" in res["content"][0]["text"]


def test_the_key_of_one_product_opens_nothing_on_another_url(store):
    _, _, raw = active_key(store)
    assert rpc(raw, "tools/list", url=f"{BASE}/store/autre/mcp/").status_code == 404
    assert rpc(raw, "tools/list", url=f"{BASE}/ailleurs/p1/mcp/").status_code == 404


def test_a_revoked_key_and_protocol_misuse(store):
    sub, key, raw = active_key(store)
    assert rpc(raw, "inconnue").json()["error"]["code"] == -32601
    assert (
        APIClient()
        .post(URL, "pas du json-rpc", format="json", HTTP_AUTHORIZATION=f"Bearer {raw}")
        .json()["error"]["code"]
        == -32600
    )
    assert APIClient().get(URL).status_code == 405
    from apistore import service

    service.revoke_key(key, store["owner"])
    assert rpc(raw, "tools/list").status_code == 401


def test_the_arguments_must_be_an_object(store):
    _, _, raw = active_key(store)
    res = rpc(raw, "tools/call", {"name": "hello", "arguments": [1]}).json()["result"]
    assert res["isError"] is True
