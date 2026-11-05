"""AR8.1 : la pagination est plafonnée (un client ne lit plus une table entière en une requête) ; sans `limit`, la page reste de 50."""

from core.pagination import MAX_PAGE
from tests.conftest import client_for
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


def test_limit_is_capped_and_default_page_is_unchanged(world):
    Workflow.objects.bulk_create(
        [Workflow(workspace=world["ws_a"], name=f"w{i}", slug=f"w-pag-{i}") for i in range(MAX_PAGE + 5)]
    )
    c = client_for(world["designer"])
    page = c.get(f"{BASE}/workflows/?limit=1000000").json()
    assert page["count"] >= MAX_PAGE + 5
    assert len(page["results"]) == MAX_PAGE and f"limit={MAX_PAGE}" in page["next"]
    assert len(c.get(f"{BASE}/workflows/").json()["results"]) == 50
    # la lecture complète (comme `apiAll` du front) reste possible en suivant `next`
    rest = c.get(page["next"].split("testserver", 1)[-1]).json()
    assert len(rest["results"]) == page["count"] - MAX_PAGE
