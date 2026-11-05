"""ADR 0025 — l'utilisateur n'a pas à connaître le nom d'un outil : le serveur annonce des invites guidées et des instructions qui renvoient au guide."""

import pytest

from audit.models import AuditEvent
from tests.conftest import client_for
from workflows.mcp_prompts import MAX_NEED, PROMPTS

DESIGN = "/api/v1/workspaces/team-a/mcp/design/"
EXEC = "/api/v1/workspaces/team-a/mcp/"


def rpc(c, method, params=None, url=DESIGN):
    return c.post(
        url,
        {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})},
        format="json",
    ).json()


def test_initialize_announces_prompts_and_points_to_the_guide(world):
    out = rpc(
        client_for(world["designer"]), "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}}
    )["result"]
    assert "prompts" in out["capabilities"] and "tools" in out["capabilities"]
    assert out["instructions"].startswith("AVANT TOUTE CRÉATION, appelez get_authoring_guide")


def test_the_execution_profile_is_unchanged_and_has_no_prompts(world):
    c = client_for(world["designer"])
    out = rpc(c, "initialize", {"protocolVersion": "2025-06-18"}, url=EXEC)["result"]
    assert "prompts" not in out["capabilities"]
    assert rpc(c, "prompts/list", url=EXEC)["error"]["code"] == -32601


def test_prompts_list_describes_arguments(world):
    prompts = {p["name"]: p for p in rpc(client_for(world["viewer"]), "prompts/list")["result"]["prompts"]}
    assert (
        set(prompts) == set(PROMPTS) == {"concevoir_workflow", "creer_formulaire", "proposer_action", "faire_le_point"}
    )
    assert prompts["concevoir_workflow"]["arguments"] == [
        {"name": "besoin", "description": "Ce que vous voulez automatiser, en langage courant.", "required": True}
    ]
    assert prompts["faire_le_point"]["arguments"] == []


def test_a_prompt_carries_the_method_and_the_users_need(world):
    need = "Accueillir un nouveau collaborateur dans 6 pays"
    res = rpc(
        client_for(world["designer"]), "prompts/get", {"name": "concevoir_workflow", "arguments": {"besoin": need}}
    )["result"]
    [msg] = res["messages"]
    text = msg["content"]["text"]
    assert msg["role"] == "user" and need in text
    for step in (
        "get_authoring_guide",
        "RÉUTILISE avant de créer",
        "action réutilisable ou script",
        "Ne publie, ne lance et n'active rien",
        "récapitulatif",
        "start_dry_run",
        "ne les invente pas",
    ):
        assert step in text, step
    assert (
        "get_authoring_guide"
        in rpc(client_for(world["designer"]), "prompts/get", {"name": "proposer_action", "arguments": {"besoin": "x"}})[
            "result"
        ]["messages"][0]["content"]["text"]
    )


def test_a_prompt_without_its_argument_or_unknown_is_a_clean_error(world):
    c = client_for(world["designer"])
    assert rpc(c, "prompts/get", {"name": "concevoir_workflow", "arguments": {}})["error"]["code"] == -32602
    assert (
        rpc(c, "prompts/get", {"name": "concevoir_workflow", "arguments": {"besoin": "   "}})["error"]["code"] == -32602
    )
    assert "Invite inconnue" in rpc(c, "prompts/get", {"name": "publier_tout"})["error"]["message"]


def test_the_need_is_bounded_and_nothing_is_executed_by_a_prompt(world):
    from catalog.models import Action
    from runs.models import WorkflowRun
    from workflows.models import Workflow

    c = client_for(world["designer"])
    before = Action.objects.count()
    text = rpc(c, "prompts/get", {"name": "concevoir_workflow", "arguments": {"besoin": "x" * (MAX_NEED * 3)}})[
        "result"
    ]["messages"][0]["content"]["text"]
    assert "x" * MAX_NEED in text and "x" * (MAX_NEED + 1) not in text  # borné
    rpc(c, "prompts/get", {"name": "faire_le_point"})
    assert (Workflow.objects.count(), WorkflowRun.objects.count()) == (
        0,
        0,
    ) and Action.objects.count() == before  # un prompt n'écrit rien
    ev = AuditEvent.objects.filter(action="mcp.design.prompt")
    assert {e.data["prompt"] for e in ev} == {"concevoir_workflow", "faire_le_point"} and all(
        e.data["external_assistant"] for e in ev
    )


@pytest.mark.parametrize(
    "tool,topic",
    [
        ("create_workflow", "workflow"),
        ("update_workflow", "workflow"),
        ("create_form", "form"),
        ("update_form", "form"),
        ("propose_action", "action"),
        ("propose_skill", "skill"),
    ],
)
def test_creation_tools_point_to_the_guide_in_their_description(world, tool, topic):
    tools = {t["name"]: t for t in rpc(client_for(world["designer"]), "tools/list")["result"]["tools"]}
    assert f"get_authoring_guide (topic={topic})" in tools[tool]["description"]


def test_a_refusal_points_to_the_guide(world):
    r = rpc(
        client_for(world["designer"]),
        "tools/call",
        {"name": "create_workflow", "arguments": {"slug": "pas un slug", "name": "N"}},
    )["result"]
    assert r["isError"] and "get_authoring_guide" in r["content"][0]["text"]
