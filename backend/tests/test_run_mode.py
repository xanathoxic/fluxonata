"""Mode d'exécution d'un workflow : concurrent (défaut) ou séquentiel (un seul lancement actif à la fois, les suivants attendent)."""

import pytest

from runs import engine, queueing
from runs.models import WorkflowRun
from tenancy.models import Environment
from tests.conftest import client_for
from tests.wf_helpers import drain, launch, node, publish
from triggers.scheduler import ACTIVE
from workflows.models import Workflow


@pytest.fixture
def version(ws, world):
    return publish(ws, "seq", [node("j", "join")], [], user=world["designer"])


def sequential(ws):
    Workflow.objects.filter(workspace=ws, slug="seq").update(run_mode="sequential")


def statuses(*runs):
    return [WorkflowRun.objects.get(pk=r.pk).status for r in runs]


def test_concurrent_by_default_every_launch_runs_at_once(ws, world, version):
    a, b = launch(ws, version, world["designer"]), launch(ws, version, world["designer"])
    assert statuses(a, b) == ["running", "running"]


def test_sequential_second_launch_waits_and_is_not_advanced(ws, world, version):
    sequential(ws)
    a, b = launch(ws, version, world["designer"]), launch(ws, version, world["designer"])
    assert statuses(a, b) == ["running", "queued"]
    assert not engine.advance(b.pk)  # l'orchestrateur ne touche pas à un lancement en attente
    assert b.events.filter(type="queued").exists() and not b.events.filter(type="started").exists()
    assert not b.steps.exists()


def test_sequential_runs_start_in_arrival_order_one_at_a_time(ws, world, version):
    sequential(ws)
    a, b, c = (launch(ws, version, world["designer"]) for _ in range(3))
    assert queueing.promote_waiting() == 0  # le premier est encore actif
    drain(a)
    assert statuses(a, b, c) == ["succeeded", "queued", "queued"]
    assert queueing.promote_waiting() == 1
    assert statuses(a, b, c) == ["succeeded", "running", "queued"]
    assert queueing.promote_waiting() == 0  # b est actif : c attend encore
    drain(b)
    assert queueing.promote_waiting() == 1
    assert statuses(b, c) == ["succeeded", "running"]
    assert drain(c).status == "succeeded"
    assert (
        b.events.filter(type="queued").exists()
        and WorkflowRun.objects.get(pk=b.pk).events.filter(type="dequeued").exists()
    )


def test_cancelling_a_waiting_run_cancels_it_at_once_and_does_not_block_the_queue(ws, world, version):
    sequential(ws)
    a, b, c = (launch(ws, version, world["designer"]) for _ in range(3))
    engine.request_run_cancel(b.pk, world["designer"])
    assert statuses(a, b, c) == ["running", "cancelled", "queued"]
    drain(a)
    queueing.promote_waiting()
    assert statuses(a, b, c) == ["succeeded", "cancelled", "running"]


def test_a_failed_or_cancelled_predecessor_also_releases_the_next(ws, world, version):
    sequential(ws)
    a, b = launch(ws, version, world["designer"]), launch(ws, version, world["designer"])
    engine.request_run_cancel(a.pk, world["designer"])
    drain(a)
    assert WorkflowRun.objects.get(pk=a.pk).status == "cancelled"
    queueing.promote_waiting()
    assert statuses(b) == ["running"]


def test_the_queue_is_per_environment(ws, world, version):
    sequential(ws)
    Environment.objects.create(
        workspace=ws, kind="dev", name="dev"
    )  # le jeu de test n'a que « test » et « prod » (celui-ci exige une promotion)
    a = launch(ws, version, world["designer"], env="test")
    b = launch(ws, version, world["designer"], env="dev")
    assert statuses(a, b) == ["running", "running"]


def test_a_draft_run_never_waits(ws, world, version):
    sequential(ws)
    launch(ws, version, world["designer"])
    run, _ = engine.start_run(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        workflow_version=version,
        user=world["designer"],
        inputs={},
        graph_override=version.graph,
    )
    assert run.status == "running"


def test_a_waiting_run_counts_as_active_for_schedules():
    assert "queued" in ACTIVE


def test_api_sets_and_validates_the_run_mode(ws, world, version):
    base = f"/api/v1/workspaces/{ws.slug}/workflows/seq/meta/"
    c = client_for(world["designer"])
    assert c.patch(base, {"run_mode": "sequential"}, format="json").json()["run_mode"] == "sequential"
    assert Workflow.objects.get(workspace=ws, slug="seq").run_mode == "sequential"
    assert c.patch(base, {"run_mode": "n'importe quoi"}, format="json").status_code == 400
    assert client_for(world["viewer"]).patch(base, {"run_mode": "concurrent"}, format="json").status_code in (403, 404)
    assert Workflow.objects.get(workspace=ws, slug="seq").run_mode == "sequential"


def test_children_endpoint_lists_sub_workflows_under_their_parent(ws, world):
    publish(ws, "enfant", [node("j", "join")], [], user=world["designer"])
    parent = publish(
        ws, "parent", [node("sw", "subworkflow", workflow="enfant", inputs={})], [], user=world["designer"]
    )
    run = drain(launch(ws, parent, world["designer"]))
    c = client_for(world["designer"])
    base = f"/api/v1/workspaces/{ws.slug}/workflow-runs"
    top = c.get(f"{base}/").json()["results"]
    assert [r["workflow"]["slug"] for r in top] == ["parent"] and top[0]["has_children"] is True
    kids = c.get(f"{base}/{run.pk}/children/").json()
    assert [k["workflow"]["slug"] for k in kids] == ["enfant"]
    assert kids[0]["step_name"] and kids[0]["has_children"] is False
    assert client_for(world["outsider"]).get(f"{base}/{run.pk}/children/").status_code in (403, 404)
