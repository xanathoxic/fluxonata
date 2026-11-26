"""Contrat d'usage d'un rôle Ansible : à quoi il sert, ses variables, ses résultats, un exemple. Validé à la publication, immuable avec la version, jamais remis au runner."""

from catalog import ansible_roles as lib
from tests.conftest import client_for
from tests.test_ansible_roles import BASE, WEB, body

CONTRACT = {
    "summary": "Installe la page web du laboratoire.",
    "description": "Écrit la page d'accueil puis signale le changement.",
    "variables": [
        {
            "name": "web_message",
            "type": "str",
            "required": False,
            "default": "par défaut",
            "description": "Le texte affiché.",
        },
        {"name": "web_port", "type": "int", "required": True, "description": "Le port d'écoute."},
    ],
    "outputs": [{"name": "web_page", "description": "Chemin de la page écrite."}],
    "example": "- hosts: web\n  roles: [web]\n",
}


def create(c, contract=None, name="web"):
    return c.post(
        f"{BASE}/ansible-roles/", body(name, **({"contract": contract} if contract is not None else {})), format="json"
    )


def test_a_contract_is_stored_with_the_version_and_summarised_in_the_list(world):
    c = client_for(world["designer"])
    assert create(c, CONTRACT).status_code == 201
    row = c.get(f"{BASE}/ansible-roles/").json()[0]
    assert row["summary"] == CONTRACT["summary"] and row["variables"] == 2 and row["contract_ok"] is True
    v = c.get(f"{BASE}/ansible-roles/web/versions/1.0.0/").json()
    assert v["contract"]["variables"][1] == {
        "name": "web_port",
        "type": "int",
        "required": True,
        "description": "Le port d'écoute.",
    }
    assert v["contract"]["outputs"] == CONTRACT["outputs"] and v["contract"]["example"].startswith("- hosts: web")


def test_a_complete_contract_is_ok_and_an_incomplete_one_says_what_is_missing(world):
    c = client_for(world["designer"])
    create(c, CONTRACT)
    assert c.get(f"{BASE}/ansible-roles/").json()[0]["contract_ok"] is True
    bare = {"variables": [{"name": "x", "type": "str"}]}
    c.post(f"{BASE}/ansible-roles/web/versions/", {"version": "1.1.0", "files": WEB, "contract": bare}, format="json")
    v = c.get(f"{BASE}/ansible-roles/web/versions/1.1.0/").json()
    assert v["contract_problems"] == ["Dites en une phrase à quoi sert le rôle.", "Décrivez la variable « x »."]
    assert c.get(f"{BASE}/ansible-roles/").json()[0]["contract_ok"] is False  # la dernière version fait foi


def test_a_role_without_contract_still_works_and_is_flagged(world):
    c = client_for(world["designer"])
    assert create(c).status_code == 201
    row = c.get(f"{BASE}/ansible-roles/").json()[0]
    assert row["summary"] == "" and row["variables"] == 0 and row["contract_ok"] is False
    assert c.get(f"{BASE}/ansible-roles/web/versions/1.0.0/").json()["contract"] == {}


def test_an_invalid_contract_is_refused(world):
    c = client_for(world["designer"])
    bad = [
        {"variables": [{"name": "1mauvais", "type": "str"}]},
        {"variables": [{"name": "a", "type": "texte"}]},
        {"variables": [{"name": "a"}, {"name": "a"}]},
        {"summary": "x" * 201},
        {"outputs": "pas une liste"},
        {"summary": "clé -----BEGIN OPENSSH PRIVATE KEY----- collée"},  # scan:allow - faux secret de test
        "texte",
    ]
    for contract in bad:
        r = create(c, contract)
        assert r.status_code == 400 and "contract" in r.json()["error"]["details"], contract
    assert c.get(f"{BASE}/ansible-roles/").json() == []


def test_the_contract_is_part_of_the_immutable_version_and_not_sent_to_the_runner(world):
    c = client_for(world["designer"])
    create(c, CONTRACT)
    c.post(f"{BASE}/ansible-roles/web/versions/", {"version": "1.0.1", "files": WEB}, format="json")
    assert c.get(f"{BASE}/ansible-roles/web/versions/1.0.0/").json()["contract"]["summary"] == CONTRACT["summary"]
    assert (
        c.get(f"{BASE}/ansible-roles/web/versions/1.0.1/").json()["contract"] == {}
    )  # une nouvelle version repart de ce qu'on lui donne
    assert "contract" not in "".join(WEB)  # le contrat n'est pas un fichier du rôle : rien ne part vers les machines


def test_a_draft_is_derived_from_defaults_and_needs_the_right_to_publish(world):
    c = client_for(world["designer"])
    files = {
        "tasks/main.yml": "- ansible.builtin.debug: {msg: x}\n",
        "defaults/main.yml": "web_port: 80\nweb_name: site\nactif: true\nliste: [a]\n_prive: 1\n",
    }
    r = c.post(f"{BASE}/ansible-roles/contract-draft/", {"files": files}, format="json")
    assert r.status_code == 200
    rows = {v["name"]: v for v in r.json()["variables"]}
    assert rows["web_port"] == {"name": "web_port", "type": "int", "required": False, "description": "", "default": 80}
    assert rows["actif"]["type"] == "bool" and rows["liste"]["type"] == "list" and rows["web_name"]["default"] == "site"
    assert (
        c.post(
            f"{BASE}/ansible-roles/contract-draft/", {"files": {"defaults/main.yml": "a: 1\n"}}, format="json"
        ).status_code
        == 400
    )  # tasks/main.yml obligatoire
    assert (
        client_for(world["viewer"])
        .post(f"{BASE}/ansible-roles/contract-draft/", {"files": files}, format="json")
        .status_code
        == 403
    )
    assert lib.draft_contract({"defaults/main.yml": "[1, 2"}) == {}  # YAML illisible : pas d'ébauche, pas d'erreur
