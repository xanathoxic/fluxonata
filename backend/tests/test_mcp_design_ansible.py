"""MCP de conception, côté Ansible : lire les rôles (avec leur contrat) et les inventaires, ranger ces éléments, proposer une action Ansible. Jamais créer ni modifier un rôle ou un inventaire."""

from types import SimpleNamespace

import pytest

from ai.models import AiPolicy
from audit.models import AuditEvent
from catalog.models import Action, AnsibleRole, AnsibleRoleVersion, Inventory
from tenancy.models import Folder
from tests.conftest import client_for
from tests.test_mcp_design_write import call, error_text
from workflows import mcp_design_guide as guide
from workflows.mcp_design import TOOLS, all_tools

WEB_FILES = {
    "tasks/main.yml": "- ansible.builtin.debug: {msg: ok}\n",
    "defaults/main.yml": "http_port: 80\nweb_name: site\n",
    "meta/main.yml": "dependencies: []\n",
}
CONTRACT = {
    "summary": "Installe la page web.",
    "description": "",
    "variables": [{"name": "http_port", "type": "int", "required": False, "default": 80, "description": "Le port."}],
    "outputs": [],
    "example": "- hosts: web\n  roles: [web]\n",
}


@pytest.fixture
def ws(world):
    ws = world["ws_a"]
    role = AnsibleRole.objects.create(workspace=ws, name="web", description="Serveur web", folder="Socle")
    AnsibleRoleVersion.objects.create(role=role, version="1.0.0", files=WEB_FILES)
    AnsibleRoleVersion.objects.create(role=role, version="1.1.0", files=WEB_FILES, contract=CONTRACT)
    AnsibleRole.objects.create(workspace=ws, name="vide")  # sans version : visible mais sans contenu
    return ws


@pytest.fixture
def c(world):
    user = world["designer"]
    user._token = SimpleNamespace(
        scopes=["view", "edit"], workspace_id=world["ws_a"].id, workspace=world["ws_a"], environments=[]
    )
    yield client_for(user)
    del user._token


def test_the_ansible_tools_are_read_only_and_listed():
    for name in ("list_ansible_roles", "get_ansible_role", "draft_role_contract", "list_inventories", "get_inventory"):
        spec = TOOLS[name][0]
        assert spec["annotations"]["readOnlyHint"] is True
    # aucun outil ne crée, ne publie ni ne modifie un rôle ou un inventaire
    writing = [
        n for n in all_tools() if ("role" in n or "inventor" in n) and not n.startswith(("list_", "get_", "draft_"))
    ]
    assert writing == []


def test_roles_are_listed_with_their_contract_state_and_filtered(ws, c):
    _, data = call(c, "list_ansible_roles")
    rows = {r["name"]: r for r in data["results"]}
    assert rows["web"]["latest_version"] == "1.1.0" and rows["web"]["versions"] == ["1.1.0", "1.0.0"]
    assert (
        rows["web"]["summary"] == "Installe la page web."
        and rows["web"]["variables"] == ["http_port"]
        and rows["web"]["contract_ok"] is True
    )
    assert rows["vide"]["latest_version"] is None and rows["vide"]["contract_ok"] is False
    assert [r["name"] for r in call(c, "list_ansible_roles", {"query": "page web"})[1]["results"]] == [
        "web"
    ]  # cherche dans la phrase d'usage


def test_a_role_is_read_with_its_contract_and_files_and_a_chosen_version(ws, c):
    _, data = call(c, "get_ansible_role", {"name": "web"})
    assert (
        data["version"] == "1.1.0"
        and data["contract"]["summary"] == "Installe la page web."
        and "tasks/main.yml" in data["files"]
    )
    assert 'ansible_roles: [{"role": "web", "version": "1.1.0"}]' in data["usage"] and data["contract_problems"] == []
    old = call(c, "get_ansible_role", {"name": "web", "version": "1.0.0"})[1]
    assert old["contract"] == {} and old["contract_problems"] == ["Dites en une phrase à quoi sert le rôle."]
    assert error_text(call(c, "get_ansible_role", {"name": "absent"})[0]) == "Introuvable."
    assert error_text(call(c, "get_ansible_role", {"name": "web", "version": "9.9.9"})[0]) == "Introuvable."


def test_a_contract_draft_comes_from_defaults_and_creates_nothing(ws, c):
    before = AnsibleRoleVersion.objects.count()
    _, data = call(c, "draft_role_contract", {"files": WEB_FILES})
    assert [v["name"] for v in data["variables"]] == ["http_port", "web_name"] and data["variables"][0]["default"] == 80
    assert error_text(call(c, "draft_role_contract", {"files": {"defaults/main.yml": "a: 1\n"}})[0]).startswith(
        "Refusé"
    )
    assert AnsibleRoleVersion.objects.count() == before


def test_inventories_are_read_without_any_credential(ws, c):
    inv = Inventory.objects.create(workspace=ws, slug="lab", name="Laboratoire", folder="Prod")
    inv.hosts.create(name="web1", variables={"ansible_host": "10.0.0.5"}, enabled=True)
    _, data = call(c, "list_inventories")
    assert data["results"] == [
        {"slug": "lab", "name": "Laboratoire", "description": "", "folder": "Prod", "hosts": 1, "groups": 0}
    ]
    one = call(c, "get_inventory", {"slug": "lab"})[1]
    assert one["document"]["hosts"][0]["name"] == "web1" and 'inventory_ref: "lab"' in one["usage"]
    assert error_text(call(c, "get_inventory", {"slug": "absent"})[0]) == "Introuvable."


def test_roles_and_inventories_are_filed_with_the_rights_of_the_person(ws, world, c):
    Inventory.objects.create(workspace=ws, slug="lab", name="Laboratoire")
    res_folder = call(c, "create_folder", {"kind": "ansible_role", "path": "Socle/Web"})[0]
    assert res_folder["isError"] is False, res_folder
    assert call(c, "list_folders", {"kind": "ansible_role"})[1]["folders"] == ["Socle", "Socle/Web"]
    _, moved = call(
        c, "update_item_info", {"kind": "ansible_role", "slug": "web", "folder": "Socle/Web", "description": "Nouveau"}
    )
    assert moved["folder"] == "Socle/Web" and moved["moved_from"] == "Socle" and moved["slug"] == "web"
    role = AnsibleRole.objects.get(workspace=ws, name="web")
    assert (
        role.folder == "Socle/Web" and role.description == "Nouveau" and role.versions.count() == 2
    )  # les versions ne bougent pas
    _, inv = call(c, "update_item_info", {"kind": "inventory", "slug": "lab", "folder": "Prod", "name": "Labo"})
    assert inv["folder"] == "Prod" and Inventory.objects.get(slug="lab").name == "Labo"
    assert Folder.objects.filter(workspace=ws, kind="inventory", path="Prod").exists()
    assert AuditEvent.objects.filter(action="mcp.design.write", data__tool="update_item_info").count() == 2


def test_a_role_cannot_be_renamed_or_tagged_and_an_inventory_has_no_tags(ws, c):
    Inventory.objects.create(workspace=ws, slug="lab", name="Laboratoire")
    assert "identifiant" in error_text(
        call(c, "update_item_info", {"kind": "ansible_role", "slug": "web", "name": "autre"})[0]
    )
    assert "étiquettes" in error_text(
        call(c, "update_item_info", {"kind": "inventory", "slug": "lab", "tags": ["a"]})[0]
    )
    assert (
        error_text(call(c, "update_item_info", {"kind": "ansible_role", "slug": "absent", "folder": "x"})[0])
        == "Introuvable."
    )


def test_a_viewer_token_cannot_file_a_role(ws, world):
    viewer = world["viewer"]
    viewer._token = SimpleNamespace(scopes=["view"], workspace_id=ws.id, workspace=ws, environments=[])
    try:
        res, _ = call(client_for(viewer), "update_item_info", {"kind": "ansible_role", "slug": "web", "folder": "x"})
        assert res["isError"] is True and AnsibleRole.objects.get(name="web").folder == "Socle"
    finally:
        del viewer._token


def test_an_ansible_action_can_be_proposed_and_stays_a_proposal(ws, c):
    _, out = call(c, "propose_action", guide.ANSIBLE_ACTION_EXAMPLE)
    assert out["status"] == "proposed"
    action = Action.objects.get(workspace=ws, slug="deployer-page-web")
    version = action.latest_version()
    assert version.runtime == "ansible" and version.network_policy == "targets" and version.required_tags == ["ansible"]
    assert version.ansible_roles == [{"role": "web", "version": "1.1.0"}]  # « latest » figé à la dernière version
    assert str(action.status) == "proposed"


def test_an_ansible_proposal_without_runner_tags_is_refused_with_the_reason(ws, c):
    bad = {**guide.ANSIBLE_ACTION_EXAMPLE, "version": {**guide.ANSIBLE_ACTION_EXAMPLE["version"], "required_tags": []}}
    assert "required_tags" in error_text(call(c, "propose_action", bad)[0])
    unknown = {
        **guide.ANSIBLE_ACTION_EXAMPLE,
        "version": {**guide.ANSIBLE_ACTION_EXAMPLE["version"], "ansible_roles": [{"role": "inconnu"}]},
    }
    assert call(c, "propose_action", unknown)[0]["isError"] is True


def test_the_guide_teaches_ansible_and_the_example_passes_the_real_serializers(ws, c):
    from catalog.library import ActionCreateSerializer, require_contract

    ser = ActionCreateSerializer(data={**guide.ANSIBLE_ACTION_EXAMPLE, "ai_generated": True, "reviewed": True})
    assert ser.is_valid(), ser.errors
    require_contract(ser.validated_data["description"], ser.validated_data["version"])
    _, data = call(c, "get_authoring_guide", {"topic": "ansible"})
    text = data["guide"]
    for must in (
        "required_tags",
        "targets",
        "inventory_ref",
        "known_hosts",
        "list_ansible_roles",
        "get_ansible_role",
        "FLX_SSH_KEY_B64",
        "start_action_test refuse",
    ):
        assert must in text, must
    assert data["examples"]["ansible_action"]["version"]["runtime"] == "ansible"
    rangement = call(c, "get_authoring_guide", {"topic": "rangement"})[1]["guide"]
    assert "ansible_role" in rangement and "inventory" in rangement
    assert "ansible" in call(c, "get_authoring_guide", {"topic": "action"})[1]["guide"]


def test_an_ansible_action_is_not_tested_in_the_sandbox(ws, c, settings):
    settings.ORCH_MCP_ACTION_TEST_UNISOLATED = (
        True  # les tests n'ont pas de conteneur : le refus qu'on vérifie est celui du runtime, pas de l'isolement
    )
    AiPolicy.objects.update_or_create(workspace=ws, defaults={"mode": "local_strict", "dry_run": True})
    call(c, "propose_action", guide.ANSIBLE_ACTION_EXAMPLE)
    res, _ = call(c, "start_action_test", {"slug": "deployer-page-web", "cases": [{"label": "x", "inputs": {}}]})
    assert "ne s'essaie pas" in error_text(res) and "get_ansible_role" in error_text(res)
