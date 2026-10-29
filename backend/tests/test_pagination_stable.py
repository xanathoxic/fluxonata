"""Paginated lists sort on a UNIQUE key: equal names must not make a page skip or repeat an item."""

from catalog.models import Action
from tests.conftest import client_for
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


def walk(client, url, size=25):
    seen, offset = [], 0
    while True:
        page = client.get(f"{url}?limit={size}&offset={offset}").json()
        seen += [r["slug"] for r in page["results"]]
        offset += len(page["results"])
        if not page.get("next") or not page["results"]:
            return seen


def test_workflows_with_the_same_name_are_all_listed_once(world):
    ws = world["ws_a"]
    Workflow.objects.bulk_create([Workflow(workspace=ws, slug=f"w-{i:03d}", name="Même nom") for i in range(130)])
    got = walk(client_for(world["designer"]), f"{BASE}/workflows/")
    mine = [s for s in got if s.startswith("w-")]
    assert len(mine) == 130 and len(set(mine)) == 130


def test_actions_with_the_same_name_are_all_listed_once(world):
    ws = world["ws_a"]
    Action.objects.bulk_create([Action(workspace=ws, slug=f"a-{i:03d}", name="Même nom") for i in range(130)])
    got = walk(client_for(world["designer"]), f"{BASE}/actions/")
    mine = [s for s in got if s.startswith("a-")]
    assert len(mine) == 130 and len(set(mine)) == 130
