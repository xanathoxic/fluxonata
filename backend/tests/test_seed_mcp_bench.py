"""La bibliothèque d'essai du banc MCP : installée à l'identique, retirable sans toucher au reste, et elle donne les usages annoncés."""

from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog.models import Action
from tests.conftest import client_for
from tests.wf_helpers import make_action
from workflows.models import Workflow

URL = "/api/v1/workspaces/team-a/mcp/design/"


def usage(world):
    user = world["designer"]
    user._token = SimpleNamespace(
        scopes=["view", "edit"], workspace_id=world["ws_a"].id, workspace=world["ws_a"], environments=[]
    )
    try:
        r = client_for(user).post(
            URL,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "search_actions", "arguments": {"limit": 50}},
            },
            format="json",
        )
    finally:
        del user._token
    return {row["slug"]: row["used_by"] for row in r.json()["result"]["structuredContent"]["results"]}


def test_the_bench_library_has_the_announced_usage_and_is_idempotent(world):
    call_command("seed_mcp_bench", workspace="team-a")
    call_command("seed_mcp_bench", workspace="team-a")  # idempotent
    u = usage(world)
    assert (
        u["normaliser-texte"]["workflows"] == 3
        and u["valider-email"]["workflows"] == 2
        and u["calculer-tva"]["workflows"] == 1
    )
    assert u["valider-iban"]["workflows"] == 0 and u["generer-reference"]["workflows"] == 0
    assert (
        Workflow.objects.filter(slug__startswith="banc-").count() == 3
        and Action.objects.filter(tags__contains=["banc-mcp"]).count() == 5
    )


def test_the_bench_workflows_are_valid_and_the_actions_have_a_complete_contract(world):
    from catalog.contract import problems
    from workflows.validation import validate

    call_command("seed_mcp_bench", workspace="team-a")
    for a in Action.objects.filter(tags__contains=["banc-mcp"]):
        v = a.latest_version()
        assert problems(a.description, v.input_schema, v.output_schema) == [], a.slug
    for wf in Workflow.objects.filter(slug__startswith="banc-"):
        assert [i for i in validate(wf.draft_graph, wf.workspace, wf.slug) if i["level"] == "error"] == [], wf.slug


def test_purge_removes_only_what_the_bench_installed(world):
    make_action(world["ws_a"], "echo")  # une action ordinaire de l'espace
    Workflow.objects.create(
        workspace=world["ws_a"], slug="banc-perso", name="Le mien"
    )  # même préfixe, mais pas installé par le banc
    call_command("seed_mcp_bench", workspace="team-a")
    call_command("seed_mcp_bench", workspace="team-a", purge=True)
    assert not Action.objects.filter(tags__contains=["banc-mcp"]).exists()
    assert Action.objects.filter(slug="echo").exists() and Workflow.objects.filter(slug="banc-perso").exists()
    assert not Workflow.objects.filter(
        slug__in=["banc-onboarding", "banc-note-de-frais", "banc-contact-client"]
    ).exists()


def test_a_wrong_workspace_is_explained(db):
    with pytest.raises(CommandError, match="identifiant"):
        call_command("seed_mcp_bench", workspace="system")
