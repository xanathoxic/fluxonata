"""AR1.6 — le nombre de requêtes SQL d'une liste ne dépend pas du nombre de lignes.

Mesuré sur la base de démonstration avant correction : formulaires publics 756 requêtes pour 287 lignes, workflows 59 pour 50, secrets 45 pour 42,
exécutions 56 pour 50, approbations 56 pour 50, promotions 40 pour 17. Chaque test ajoute des lignes et exige EXACTEMENT le même nombre de requêtes
(pas un seuil : un seuil laisse revenir une requête par ligne tant qu'on reste dessous).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from runs.models import Approval
from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import ACTIONS, drain, edge, launch, make_action, node, publish
from vault.models import Connection, Secret

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def measure(client, url):
    """(requêtes SQL, lignes) d'un GET. Une première requête réchauffe les caches de processus (types de contenu, etc.)."""
    assert client.get(url).status_code == 200
    with CaptureQueriesContext(connection) as q:
        r = client.get(url)
    assert r.status_code == 200
    body = r.json()
    return len(q), len(body["results"] if isinstance(body, dict) else body)


def assert_flat(client, url, add_rows, small=2, large=6):
    for i in range(small):
        add_rows(i)
    q_small, n_small = measure(client, url)
    for i in range(small, large):
        add_rows(i)
    q_large, n_large = measure(client, url)
    assert n_large >= n_small + (large - small), f"le test n'a pas ajouté de lignes ({n_small} → {n_large})"
    assert q_large == q_small, (
        f"{url} : {q_small} requêtes pour {n_small} lignes, {q_large} pour {n_large} — une requête par ligne est revenue"
    )


def simple_workflow(ws, world, slug):
    return publish(ws, slug, [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])


def test_workflow_list(ws, world):
    assert_flat(client_for(world["designer"]), f"{BASE}/workflows/", lambda i: simple_workflow(ws, world, f"wf-{i}"))


def test_secret_list(ws, world):
    admin = make_user("ada", ws, Role.ADMIN)

    def add(i):
        s = Secret(workspace=ws, name=f"secret-{i}")
        s.set_value("x")
        s.save()
        for j in range(
            2
        ):  # plusieurs connexions par secret : le décompte « utilisé par » ne doit pas coûter une requête chacune
            Connection.objects.create(workspace=ws, name=f"c-{i}-{j}", label="C", kind="postgres", secret=s, config={})

    assert_flat(client_for(admin), f"{BASE}/secrets/", add)


def test_workflow_run_list(ws, world):
    v = simple_workflow(ws, world, "runs")
    assert_flat(client_for(world["designer"]), f"{BASE}/workflow-runs/", lambda i: launch(ws, v, world["designer"]))


def test_approval_list(ws, world):
    designer = world["designer"]
    v = publish(
        ws,
        "appr",
        [node("ap", "approval", title="ok"), node("e", action="echo", inputs={"value": "go"})],
        [edge("ap", "e")],
        user=designer,
    )

    def add(i):
        drain(launch(ws, v, designer), until=lambda r: Approval.objects.filter(step_run__run=r).exists())

    assert_flat(client_for(designer), f"{BASE}/approvals/", add)


def test_promotion_list(ws, world):
    designer = client_for(world["designer"])

    def add(i):
        v = simple_workflow(ws, world, f"promo-{i}")
        r = designer.post(
            f"{BASE}/workflows/promo-{i}/versions/{v.version}/promote/", {"environment": "prod"}, format="json"
        )
        assert r.status_code == 201, r.content

    assert_flat(designer, f"{BASE}/promotions/", add)


def test_public_form_list(ws, world):
    simple_workflow(ws, world, "demande")
    designer = client_for(world["designer"])

    def add(i):
        r = designer.post(
            f"{BASE}/forms/",
            {"name": f"Demande {i}", "title": f"Demande {i}", "workflow": "demande", "environment": "test"},
            format="json",
        )
        assert r.status_code == 201, r.content

    assert_flat(designer, f"{BASE}/forms/", add)


def test_the_actions_list_stays_flat_too(ws, world):
    designer = client_for(world["designer"])

    def add(i):
        r = designer.post(
            f"{BASE}/library/actions/",
            {
                "slug": f"flat-{i}",
                "name": "Plate",
                "description": "Action de test qui ne fait rien et renvoie un résultat vide.",
                "version": {
                    "source": "def run(i):\n    return {}\n",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "description": "Cette action n'a aucune entrée.",
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {},
                        "description": "Cette action n'a aucune sortie.",
                    },
                },
            },
            format="json",
        )
        assert r.status_code == 201, r.content

    assert_flat(designer, f"{BASE}/actions/", add)
