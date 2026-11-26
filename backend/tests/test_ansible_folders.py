"""Arborescence de dossiers des rôles Ansible et des inventaires : rangement à la création, déplacement, API des dossiers (création, renommage, suppression)."""

from tests.conftest import client_for
from tests.test_ansible_roles import BASE, WEB, body

INV = {"slug": "lab", "name": "Laboratoire", "hosts": [], "groups": [], "variables": {}}


def folders(c, kind):
    return c.get(f"{BASE}/folders/?kind={kind}").json()["results"]


def test_a_role_is_filed_at_creation_and_moved_without_touching_anything_else(world):
    c = client_for(world["designer"])
    r = c.post(f"{BASE}/ansible-roles/", body("web", folder=" Socle/Web/ "), format="json")
    assert r.status_code == 201 and r.json()["folder"] == "Socle/Web"
    assert c.get(f"{BASE}/ansible-roles/").json()[0]["folder"] == "Socle/Web"
    moved = c.patch(f"{BASE}/ansible-roles/web/", {"folder": "Autre"}, format="json")
    assert (
        moved.status_code == 200 and moved.json()["folder"] == "Autre" and moved.json()["description"] == "Serveur web"
    )
    only_desc = c.patch(f"{BASE}/ansible-roles/web/", {"description": "x"}, format="json").json()
    assert only_desc["folder"] == "Autre" and only_desc["description"] == "x"  # changer la description ne déplace pas
    assert c.patch(f"{BASE}/ansible-roles/web/", {"folder": ""}, format="json").json()["folder"] == ""
    assert c.get(f"{BASE}/ansible-roles/web/versions/1.0.0/").json()["files"] == WEB  # le contenu est intact


def test_an_inventory_is_filed_and_a_full_update_does_not_unfile_it(world):
    c = client_for(world["designer"])
    assert c.post(f"{BASE}/inventories/", {**INV, "folder": "Prod/Paris"}, format="json").status_code == 201
    assert c.get(f"{BASE}/inventories/").json()[0]["folder"] == "Prod/Paris"
    # le formulaire de l'éditeur remplace tout le document sans parler du dossier : le rangement reste
    assert c.put(f"{BASE}/inventories/lab/", {**INV, "name": "Labo"}, format="json").status_code == 200
    assert (
        c.get(f"{BASE}/inventories/").json()[0] | {}
        and c.get(f"{BASE}/inventories/").json()[0]["folder"] == "Prod/Paris"
    )
    assert c.patch(f"{BASE}/inventories/lab/", {"folder": "Test"}, format="json").json()["folder"] == "Test"
    assert c.get(f"{BASE}/inventories/").json()[0]["name"] == "Labo"


def test_folder_trees_exist_for_roles_and_inventories_and_rename_moves_the_items(world):
    c = client_for(world["designer"])
    c.post(f"{BASE}/ansible-roles/", body("web", folder="A/B"), format="json")
    c.post(f"{BASE}/inventories/", {**INV, "folder": "X"}, format="json")
    assert c.post(f"{BASE}/folders/", {"kind": "ansible_role", "path": "Vide"}, format="json").status_code == 201
    assert "Vide" in folders(c, "ansible_role") and "Vide" not in folders(c, "inventory")  # un arbre par type
    r = c.patch(f"{BASE}/folders/", {"kind": "ansible_role", "path": "A", "new_path": "Z"}, format="json")
    assert r.status_code == 200
    assert c.get(f"{BASE}/ansible-roles/web/").json()["folder"] == "Z/B"
    assert (
        c.patch(f"{BASE}/folders/", {"kind": "inventory", "path": "X", "new_path": "Y"}, format="json").status_code
        == 200
    )
    assert c.get(f"{BASE}/inventories/").json()[0]["folder"] == "Y"
    assert c.delete(f"{BASE}/folders/?kind=ansible_role&path=Vide").status_code in (200, 204)
    assert c.post(f"{BASE}/folders/", {"kind": "n_importe_quoi", "path": "x"}, format="json").status_code == 400


def test_filing_needs_the_same_rights_as_writing(world):
    c = client_for(world["designer"])
    c.post(f"{BASE}/ansible-roles/", body("web"), format="json")
    c.post(f"{BASE}/inventories/", INV, format="json")
    v = client_for(world["viewer"])
    assert v.patch(f"{BASE}/ansible-roles/web/", {"folder": "x"}, format="json").status_code == 403
    assert v.patch(f"{BASE}/inventories/lab/", {"folder": "x"}, format="json").status_code == 403
    assert v.post(f"{BASE}/folders/", {"kind": "ansible_role", "path": "x"}, format="json").status_code == 403
    assert v.post(f"{BASE}/folders/", {"kind": "inventory", "path": "x"}, format="json").status_code == 403
    o = client_for(world["outsider"])
    assert o.patch(f"{BASE}/ansible-roles/web/", {"folder": "x"}, format="json").status_code == 404
