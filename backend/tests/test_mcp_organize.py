"""MCP de conception : ranger (dossiers) et renommer les workflows, formulaires, contrôles, actions et skills. Le contenu n'est jamais touché ici."""

import pytest

from ai.models import AiSkill
from catalog.models import Action
from formbuilder.models import Control, Form
from tenancy.models import Folder
from tests.conftest import client_for
from tests.test_mcp_design_write import call, error_text
from tests.wf_helpers import ACTIONS, make_action
from workflows.mcp_design_guide import CONTROL_EXAMPLES
from workflows.mcp_design_write import ASSISTANT_TAG
from workflows.models import FolderAccess, FolderGrant, Workflow


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


@pytest.fixture
def c(world):
    return client_for(world["designer"])


def items(ws, owner):
    Workflow.objects.create(workspace=ws, owner=owner, slug="wf", name="WF", draft_graph={"nodes": []})
    Form.objects.create(workspace=ws, owner=owner, slug="fm", name="FM")
    Control.objects.create(workspace=ws, owner=owner, slug="ct", name="CT", draft_spec=CONTROL_EXAMPLES["group"])
    AiSkill.objects.create(workspace=ws, owner=owner, slug="sk", name="SK")


def test_folders_are_created_with_their_parents_and_listed(ws, c):
    res, data = call(c, "create_folder", {"kind": "workflow", "path": " Réseau / Fabric/V2 "})
    assert not res["isError"] and data["path"] == "Réseau/Fabric/V2" and len(data["created"]) == 3
    assert call(c, "list_folders", {"kind": "workflow"})[1]["folders"] == [
        "Réseau",
        "Réseau/Fabric",
        "Réseau/Fabric/V2",
    ]
    assert call(c, "list_folders", {"kind": "form"})[1]["folders"] == []  # un arbre par type d'élément
    assert "existe déjà" in error_text(call(c, "create_folder", {"kind": "workflow", "path": "Réseau"})[0])
    assert "Type inconnu" in error_text(call(c, "list_folders", {"kind": "secret"})[0])
    assert "Indiquez" in error_text(call(c, "create_folder", {"kind": "form", "path": " / "})[0])


@pytest.mark.parametrize("kind,slug", [("workflow", "wf"), ("form", "fm"), ("control", "ct"), ("ai_skill", "sk")])
def test_an_item_is_moved_renamed_and_marked_without_touching_its_content(ws, world, c, kind, slug):
    items(ws, world["designer"])
    res, data = call(
        c,
        "update_item_info",
        {"kind": kind, "slug": slug, "folder": "Infra/Réseau", "name": "Nouveau nom", "tags": ["a"]},
    )
    assert not res["isError"], res
    assert data["folder"] == "Infra/Réseau" and data["moved_from"] == "" and ASSISTANT_TAG in data["tags"]
    assert Folder.objects.filter(
        workspace=ws, kind=kind, path="Infra"
    ).exists()  # le dossier d'arrivée existe dans l'arbre
    back = call(c, "update_item_info", {"kind": kind, "slug": slug, "folder": ""})[1]
    assert back["folder"] == "" and back["moved_from"] == "Infra/Réseau" and back["name"] == "Nouveau nom"


def test_an_action_is_moved_but_its_code_and_versions_are_untouched(ws, c):
    action = Action.objects.get(workspace=ws, slug="echo")
    n = action.versions.count()
    res, data = call(
        c, "update_item_info", {"kind": "action", "slug": "echo", "folder": "Outils", "description": "Écho"}
    )
    assert not res["isError"] and data["folder"] == "Outils"
    action.refresh_from_db()
    assert action.folder == "Outils" and action.description == "Écho" and action.versions.count() == n


def test_nothing_to_change_unknown_item_and_templates_are_refused(ws, world, c):
    items(ws, world["designer"])
    assert "au moins un changement" in error_text(call(c, "update_item_info", {"kind": "form", "slug": "fm"})[0])
    assert "Introuvable" in error_text(call(c, "update_item_info", {"kind": "form", "slug": "nope", "name": "X"})[0])
    Form.objects.filter(slug="fm").update(is_template=True)
    assert "modèle" in error_text(call(c, "update_item_info", {"kind": "form", "slug": "fm", "name": "X"})[0])
    too_deep = "/".join("d" * 3 for _ in range(9))
    assert "niveaux" in error_text(
        call(c, "update_item_info", {"kind": "workflow", "slug": "wf", "folder": too_deep})[0]
    )


def test_rights_and_private_folders_apply_to_both_the_origin_and_the_target(ws, world, c):
    items(ws, world["designer"])
    viewer = client_for(world["viewer"])
    assert "modifier" in error_text(call(viewer, "update_item_info", {"kind": "form", "slug": "fm", "name": "X"})[0])
    assert "modifier" in error_text(call(viewer, "create_folder", {"kind": "form", "path": "A"})[0])
    acc = FolderAccess.objects.create(workspace=ws, folder="RH")
    FolderGrant.objects.create(access=acc, user=world["viewer"], level="view")  # le concepteur n'a pas accès à « RH »
    refused = call(c, "update_item_info", {"kind": "workflow", "slug": "wf", "folder": "RH"})[
        0
    ]  # dossier d'arrivée privé
    assert refused["isError"] and Workflow.objects.get(slug="wf").folder == ""
    assert call(c, "create_folder", {"kind": "workflow", "path": "RH/Paie"})[0]["isError"]
    Folder.objects.create(workspace=ws, kind="workflow", path="RH")
    assert call(c, "list_folders", {"kind": "workflow"})[1]["folders"] == []  # un dossier privé n'est pas nommé
    Workflow.objects.filter(slug="wf").update(folder="RH")  # origine privée
    assert call(c, "update_item_info", {"kind": "workflow", "slug": "wf", "folder": ""})[0]["isError"]
