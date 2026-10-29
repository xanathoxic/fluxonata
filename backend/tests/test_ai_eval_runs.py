import pytest

from ai.evals import runner
from ai.models import AiEvalRun, AiProvider
from tenancy.models import Role
from tests.conftest import client_for, make_user

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def admin(world):
    ws = world["ws_a"]
    AiProvider.objects.create(workspace=ws, name="faux", kind="fake", model="fake-1")
    return client_for(make_user("ada-admin", ws, Role.ADMIN)), ws


def test_an_evaluation_is_queued_run_by_the_worker_and_scored(admin, world):
    c, ws = admin
    r = c.post(f"{BASE}/ai/evals/", {"provider": "faux", "suite": "search"}, format="json")
    assert r.status_code == 202
    assert (
        c.post(f"{BASE}/ai/evals/", {"provider": "faux", "suite": "search"}, format="json").status_code == 400
    )  # one at a time
    run = runner.claim_next("w1")
    assert run.status == "running" and run.prompts["explain_error"]
    runner.process(run)
    done = c.get(f"{BASE}/ai/evals/").data["results"][0]
    assert done["status"] == "done" and done["total"] > 0 and done["score"] is not None and done["model"] == "fake-1"
    assert "recherche" in c.get(f"{BASE}/ai/evals/{run.pk}/").data["log"]
    assert runner.claim_next("w1") is None


def test_regression_is_flagged_against_the_previous_run_and_input_is_validated(admin, world):
    c, ws = admin
    AiEvalRun.objects.create(workspace=ws, provider="faux", suite="search", status="done", passed=9, total=10)
    AiEvalRun.objects.create(workspace=ws, provider="faux", suite="search", status="done", passed=5, total=10)
    rows = c.get(f"{BASE}/ai/evals/").data["results"]
    assert (
        rows[0]["score"] == 50.0
        and rows[0]["previous_score"] == 90.0
        and rows[0]["regression"] is True
        and rows[1]["regression"] is False
    )
    assert c.post(f"{BASE}/ai/evals/", {"provider": "absent", "suite": "search"}, format="json").status_code == 400
    assert c.post(f"{BASE}/ai/evals/", {"provider": "faux", "suite": "nope"}, format="json").status_code == 400
    assert client_for(world["designer"]).get(f"{BASE}/ai/evals/").status_code == 403


def test_totals_are_read_from_each_suite_output():
    assert runner.parse_totals("x\nTotal : 7/9 critères.\n") == (7, 9)
    assert runner.parse_totals("3/5 cas réussis (contrôles") == (3, 5)
    assert runner.parse_totals("  direct : 4/6 critères · 10 jetons\n  plan : 5/6 critères · 12 jetons\n") == (9, 12)
    assert runner.parse_totals("rien") == (0, 0)
