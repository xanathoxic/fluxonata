"""La version épinglée d'un workflow appelé (étapes `subworkflow`, `foreach`, `while`) : ce que l'éditeur enregistre est ce que le serveur conserve.

Régression : l'éditeur affichait « (v1.2.0) » — la dernière version publiée de l'enfant — alors que le brouillon et la version publiée du parent
référençaient 1.1.0. Ici : la version écrite dans le brouillon est conservée telle quelle (jamais migrée d'office), la réponse de l'enregistrement
rend la valeur réellement stockée, la publication la fige, et l'exécution appelle exactement cette version.
"""

import pytest

from tests.conftest import client_for
from tests.wf_helpers import STR, drain, launch, node, publish, schema
from workflows.models import Workflow, WorkflowVersion

WS = "/api/v1/workspaces/team-a"


def child_versions(ws, user):
    """Enfant publié en 1.0.0 puis 1.1.0 : la même entrée, une sortie qui dit quelle version a répondu."""
    for label in ("ancienne", "récente"):
        publish(
            ws,
            "child",
            [node("e", action="echo", inputs={"value": "{{ inputs.v }}"})],
            [],
            inputs=schema(["v"], v=STR),
            outputs={"v": "{{ steps.e.output.value }}", "which": label},
            user=user,
        )


def step_node(kind, **extra):
    if kind == "subworkflow":
        return node("c", "subworkflow", workflow="child", inputs={"v": "x"}, **extra)
    if kind == "foreach":
        return node("c", "foreach", workflow="child", items="inputs.items", item_input="v", max_iterations=3, **extra)
    return node(
        "c", "while", workflow="child", condition="iteration >= 1", max_iterations=2, inputs={"v": "x"}, **extra
    )


OUT = {
    "subworkflow": "{{ steps.c.output.which }}",
    "foreach": "{{ steps.c.output.results }}",
    "while": "{{ steps.c.output.last.which }}",
}


def put(c, slug, graph, revision):
    return c.put(f"{WS}/workflows/{slug}/draft/", {"graph": graph, "revision": revision}, format="json")


def detail(c, slug):
    return c.get(f"{WS}/workflows/{slug}/").json()


def parent_graph(kind, **extra):
    return {
        "inputs": schema(items={"type": "array"}),
        "nodes": [step_node(kind, **extra)],
        "edges": [],
        "outputs": {"r": OUT[kind]},
    }


def which(kind, run):
    r = run.outputs["r"]
    return r[0]["which"] if kind == "foreach" else r


@pytest.fixture
def parent(ws, world):
    child_versions(ws, world["designer"])
    Workflow.objects.create(workspace=ws, slug="parent", name="parent", draft_graph={})
    return client_for(world["designer"])


@pytest.mark.parametrize("kind", ["subworkflow", "foreach", "while"])
def test_choosing_a_newer_child_version_is_stored_returned_published_and_run(ws, world, parent, kind):
    c = parent
    # 1. le parent référence 1.0.0 ; l'enfant a une 1.1.0 plus récente
    first = put(c, "parent", parent_graph(kind, version="1.0.0"), revision=1)
    assert first.status_code == 200 and first.json()["revision"] == 2
    shown = detail(c, "parent")["draft_graph"]["nodes"][0]
    assert shown["version"] == "1.0.0"  # ni mise à jour automatique, ni valeur déduite du catalogue
    # 2. la personne choisit 1.1.0 : la révision augmente et la réponse porte la valeur RÉELLEMENT stockée
    chosen = put(c, "parent", parent_graph(kind, version="1.1.0"), revision=2)
    body = chosen.json()
    assert chosen.status_code == 200 and body["revision"] == 3 and body["graph"]["nodes"][0]["version"] == "1.1.0"
    assert Workflow.objects.get(slug="parent").draft_graph["nodes"][0]["version"] == "1.1.0"  # la base
    assert (
        detail(c, "parent")["draft_graph"]["nodes"][0]["version"] == "1.1.0"
    )  # l'API après « actualisation de la page »
    # 3. la publication suivante la conserve (version immuable)
    published = c.post(f"{WS}/workflows/parent/publish/", {"bump": "minor"}, format="json")
    assert published.status_code == 201, published.content
    version = WorkflowVersion.objects.get(workflow__slug="parent", version=published.json()["version"])
    assert version.graph["nodes"][0]["version"] == "1.1.0"
    # 4. l'exécution appelle réellement cette version
    run = drain(launch(ws, version, world["designer"], {"items": ["a"]}), max_rounds=200)
    assert run.status == "succeeded", run.error
    assert which(kind, run) == "récente"


@pytest.mark.parametrize("kind", ["subworkflow", "foreach", "while"])
def test_an_existing_pin_is_never_migrated_to_the_latest_version(ws, world, parent, kind):
    c = parent
    put(c, "parent", parent_graph(kind, version="1.0.0"), revision=1)
    published = c.post(f"{WS}/workflows/parent/publish/", {"bump": "minor"}, format="json")
    assert published.status_code == 201
    # l'enfant publie encore une version : ni le brouillon, ni la version publiée du parent ne bougent
    publish(
        ws,
        "child",
        [node("e", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v=STR),
        outputs={"v": "{{ steps.e.output.value }}", "which": "plus récente"},
        user=world["designer"],
    )
    assert detail(c, "parent")["draft_graph"]["nodes"][0]["version"] == "1.0.0"
    pub = WorkflowVersion.objects.get(workflow__slug="parent", version=published.json()["version"])
    assert pub.graph["nodes"][0]["version"] == "1.0.0"
    run = drain(launch(ws, pub, world["designer"], {"items": ["a"]}), max_rounds=200)
    assert run.status == "succeeded" and which(kind, run) == "ancienne"


@pytest.mark.parametrize("kind", ["subworkflow", "foreach", "while"])
def test_a_step_without_a_pin_follows_the_latest_version_at_publication_and_is_then_pinned(ws, world, parent, kind):
    c = parent
    put(c, "parent", parent_graph(kind), revision=1)
    assert "version" not in detail(c, "parent")["draft_graph"]["nodes"][0]  # le brouillon reste « sans version »
    published = c.post(f"{WS}/workflows/parent/publish/", {"bump": "minor"}, format="json").json()
    pub = WorkflowVersion.objects.get(workflow__slug="parent", version=published["version"])
    assert pub.graph["nodes"][0]["version"] == "1.1.0"  # comportement historique : la dernière, figée à la publication
    assert (
        "version" not in Workflow.objects.get(slug="parent").draft_graph["nodes"][0]
    )  # le brouillon n'a pas été modifié


def test_a_stale_revision_is_refused_with_the_current_one_and_nothing_is_stored(ws, world, parent):
    c = parent
    assert put(c, "parent", parent_graph("subworkflow", version="1.0.0"), revision=1).status_code == 200
    late = put(
        c, "parent", parent_graph("subworkflow", version="1.1.0"), revision=1
    )  # un autre onglet a déjà enregistré
    assert late.status_code == 409 and late.json()["error"]["details"]["current_revision"] == 2
    assert detail(c, "parent")["draft_graph"]["nodes"][0]["version"] == "1.0.0"


def test_an_unknown_version_is_reported_by_the_server_not_silently_replaced(ws, world, parent):
    r = put(parent, "parent", parent_graph("subworkflow", version="9.9.9"), revision=1)
    assert r.status_code == 200
    assert [(i["code"], i["message"]) for i in r.json()["issues"]] == [
        ("version_unavailable", "La version 9.9.9 de child n'existe pas.")
    ]
    assert (
        r.json()["graph"]["nodes"][0]["version"] == "9.9.9"
    )  # conservée telle quelle : l'éditeur la montrera comme introuvable


# --- « latest » : suivre la dernière version, résolue à chaque exécution (jamais figée à la publication) -------------------------------------
@pytest.mark.parametrize("kind", ["subworkflow", "foreach", "while"])
def test_latest_is_kept_by_the_publication_and_follows_the_child_at_every_run(ws, world, parent, kind):
    c = parent
    saved = put(c, "parent", parent_graph(kind, version="latest"), revision=1)
    assert saved.status_code == 200 and saved.json()["issues"] == []  # « latest » est une valeur valide
    assert saved.json()["graph"]["nodes"][0]["version"] == "latest"
    published = c.post(f"{WS}/workflows/parent/publish/", {"bump": "minor"}, format="json")
    assert published.status_code == 201
    pub = WorkflowVersion.objects.get(workflow__slug="parent", version=published.json()["version"])
    assert pub.graph["nodes"][0]["version"] == "latest"  # NON figée : c'est tout l'intérêt
    first = drain(launch(ws, pub, world["designer"], {"items": ["a"]}), max_rounds=200)
    assert first.status == "succeeded", first.error
    assert which(kind, first) == "récente"  # la 1.1.0, dernière à ce moment
    # l'enfant publie une version de plus : la MÊME version publiée du parent l'appelle, sans republication
    publish(
        ws,
        "child",
        [node("e", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v=STR),
        outputs={"v": "{{ steps.e.output.value }}", "which": "encore plus récente"},
        user=world["designer"],
    )
    second = drain(launch(ws, pub, world["designer"], {"items": ["a"]}), max_rounds=200)
    assert second.status == "succeeded", second.error
    assert which(kind, second) == "encore plus récente"
    assert (
        WorkflowVersion.objects.get(pk=pub.pk).graph["nodes"][0]["version"] == "latest"
    )  # la version publiée du parent n'a pas changé


def test_latest_and_an_explicit_pin_coexist_in_one_workflow_and_an_empty_value_still_freezes(ws, world, parent):
    c = parent
    graph = {
        "inputs": schema(),
        "nodes": [
            node("a", "subworkflow", workflow="child", inputs={"v": "x"}, version="latest"),
            node("b", "subworkflow", workflow="child", inputs={"v": "x"}, version="1.0.0"),
            node("d", "subworkflow", workflow="child", inputs={"v": "x"}),
        ],
        "edges": [],
        "outputs": {},
    }
    assert put(c, "parent", graph, revision=1).status_code == 200
    published = c.post(f"{WS}/workflows/parent/publish/", {"bump": "minor"}, format="json").json()
    pub = WorkflowVersion.objects.get(workflow__slug="parent", version=published["version"])
    assert [n["version"] for n in pub.graph["nodes"]] == [
        "latest",
        "1.0.0",
        "1.1.0",
    ]  # suivie, épinglée, figée sur la dernière du moment
