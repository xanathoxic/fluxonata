import pytest

from ai.models import AiSkill
from audit.models import AuditEvent
from catalog.models import Action
from tenancy.models import Folder, Role
from tests.conftest import client_for, make_user
from workflows.models import Workflow

URL = "/api/v1/workspaces/team-a/folders/"
KINDS = ["workflow", "action", "ai_skill"]


def item(world, kind, slug, folder):
    ws = world["ws_a"]
    model = {"workflow": Workflow, "action": Action, "ai_skill": AiSkill}[kind]
    return model.objects.create(workspace=ws, slug=slug, name=slug, folder=folder)


def post(c, kind, path):
    return c.post(URL, {"kind": kind, "path": path}, format="json")


def listed(c, kind):
    return set(c.get(URL, {"kind": kind}).json()["results"])


@pytest.mark.parametrize("kind", KINDS)
def test_create_nested_folder_creates_its_ancestors_and_is_audited(world, kind):
    c = client_for(world["designer"])
    r = post(c, kind, " Réseau / DNS / Interne ")
    assert r.status_code == 201 and r.json()["path"] == "Réseau/DNS/Interne"
    assert listed(c, kind) == {"Réseau", "Réseau/DNS", "Réseau/DNS/Interne"}
    assert AuditEvent.objects.filter(action="folder.create", data__kind=kind).exists()
    assert post(c, kind, "Réseau/DNS").status_code == 400  # already there
    other = next(k for k in KINDS if k != kind)
    assert listed(c, other) == set()  # each tree is independent


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "workflow", "path": "  "},
        {"kind": "workflow", "path": "a" * 61},
        {"kind": "workflow", "path": "/".join("abcdefghi")},
        {"kind": "nope", "path": "x"},
        {"path": "x"},
    ],
)
def test_create_validation(world, body):
    assert client_for(world["designer"]).post(URL, body, format="json").status_code == 400


def test_permissions_and_isolation(world):
    assert post(client_for(world["viewer"]), "workflow", "x").status_code == 403
    operator = make_user("opa", world["ws_a"], Role.OPERATOR)
    assert post(client_for(operator), "action", "x").status_code == 403
    assert (
        client_for(world["viewer"]).get(URL, {"kind": "workflow"}).status_code == 200
    )  # anyone who can view reads the tree
    assert post(client_for(world["outsider"]), "workflow", "x").status_code == 404
    assert client_for(world["outsider"]).get(URL, {"kind": "workflow"}).status_code == 404
    assert client_for(world["viewer"]).delete(URL + "?kind=workflow&path=x").status_code == 403


@pytest.mark.parametrize("kind", KINDS)
def test_rename_moves_items_and_subfolders_everywhere(world, kind):
    c = client_for(world["designer"])
    a, b = item(world, kind, "i1", "Infra/Réseau"), item(world, kind, "i2", "Infra/Réseau/DNS")
    item(world, kind, "i3", "Autre")
    post(c, kind, "Infra/Réseau/Vide")  # an empty, explicitly created sub-folder
    r = c.patch(URL, {"kind": kind, "path": "Infra/Réseau", "new_path": "Exploitation/Net"}, format="json")
    assert r.status_code == 200 and r.json()["items_moved"] == 2
    for x in (a, b):
        x.refresh_from_db()
    assert (a.folder, b.folder) == ("Exploitation/Net", "Exploitation/Net/DNS")
    assert listed(c, kind) == {
        "Exploitation",
        "Exploitation/Net",
        "Exploitation/Net/Vide",
        "Infra",
    }  # "Infra" stays (created as an ancestor)
    assert AuditEvent.objects.filter(action="folder.rename", data__items=2).exists()


def test_rename_refusals(world):
    c = client_for(world["designer"])
    item(world, "workflow", "w", "A/B")
    post(c, "workflow", "C")
    patch = lambda p, n: c.patch(URL, {"kind": "workflow", "path": p, "new_path": n}, format="json")  # noqa: E731
    assert patch("A/B", "C").status_code == 400  # name already used
    assert patch("A", "A/B/Z").status_code == 400  # into itself
    assert patch("A/B", "A/B").status_code == 400  # unchanged
    assert patch("Inconnu", "X").status_code == 404
    assert patch("A/B", " ").status_code == 400
    assert Workflow.objects.get(slug="w").folder == "A/B"  # nothing moved on refusal


@pytest.mark.parametrize("kind", KINDS)
def test_delete_only_when_empty_and_removes_empty_descendants(world, kind):
    c = client_for(world["designer"])
    post(c, kind, "Tmp/Sous")
    it = item(world, kind, "x", "Plein/Dedans")
    d = c.delete(URL + f"?kind={kind}&path=Plein")
    assert d.status_code == 400 and "1 élément" in str(d.json()["error"]["details"])
    assert c.delete(URL + f"?kind={kind}&path=Plein/Dedans").status_code == 400
    assert c.delete(URL + f"?kind={kind}&path=Tmp").status_code == 204  # empty: sub-folders go with it
    assert listed(c, kind) == set()
    assert c.delete(URL + f"?kind={kind}&path=Tmp").status_code == 404
    it.folder = ""
    it.save()
    assert (
        c.delete(URL + f"?kind={kind}&path=Plein").status_code == 404
    )  # an implicit folder vanishes with its last item
    assert AuditEvent.objects.filter(action="folder.delete").count() == 1


def test_folder_of_other_workspace_is_untouched(world):
    item(world, "workflow", "b1", "Commun")
    Workflow.objects.filter(slug="b1").update(workspace=world["ws_b"])
    c = client_for(world["designer"])
    assert c.patch(URL, {"kind": "workflow", "path": "Commun", "new_path": "X"}, format="json").status_code == 404
    assert Folder.objects.count() == 0
