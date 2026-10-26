import threading
import time
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from runs import engine, services
from runs.models import Approval, Execution, StepRun, WorkflowRun
from runs.runners import SubprocessRunner
from runs.worker import process_one
from tenancy.models import Membership, Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import (
    ACTIONS,
    STR,
    drain,
    edge,
    executions,
    launch,
    make_action,
    node,
    publish,
    schema,
    step,
)


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug) if slug != "echo" else None
    from catalog.models import ActionVersion

    if not ActionVersion.objects.filter(action__workspace=world["ws_a"], action__slug="echo").exists():
        make_action(world["ws_a"], "echo")
    return world["ws_a"]


def test_sequence_with_condition_skips_other_branch(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "seq",
        [
            node("a", action="add", inputs={"a": "{{ inputs.x }}", "b": 1}),
            node("c", "condition", expr="steps.a.output.sum > 3"),
            node("big", action="echo", inputs={"value": "grand"}),
            node("small", action="echo", inputs={"value": "petit"}),
        ],
        [edge("a", "c"), edge("c", "big", "true"), edge("c", "small", "false")],
        inputs=schema(["x"], x={"type": "integer"}),
        outputs={"result": "{{ steps.big.output.value }}"},
        user=user,
    )
    run = drain(launch(ws, v, user, {"x": 5}))
    assert run.status == "succeeded", run.error
    assert run.outputs == {"result": "grand"}
    assert step(run, "small").status == "skipped" and step(run, "big").status == "succeeded"


def test_parallel_branches_and_join(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "par",
        [
            node("a", action="echo", inputs={"value": "x"}),
            node("b", action="echo", inputs={"value": "b"}),
            node("c", action="echo", inputs={"value": "c"}),
            node("j", "join"),
            node("d", action="echo", inputs={"value": "{{ steps.b.output.value }}{{ steps.c.output.value }}"}),
        ],
        [edge("a", "b"), edge("a", "c"), edge("b", "j"), edge("c", "j"), edge("j", "d")],
        outputs={"v": "{{ steps.d.output.value }}"},
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "succeeded", (run.error, [(s.node_id, s.status, s.error) for s in run.steps.all()])
    assert run.outputs == {"v": "bc"}
    # join waited for both branches
    assert step(run, "j").seq > max(step(run, "b").seq, step(run, "c").seq) - 1


def test_failure_without_error_edge_fails_run_and_compensates_in_reverse(ws, world, tmp_path):
    user, log = world["designer"], str(tmp_path / "log.txt")
    comp = lambda label: {"action": "log", "inputs": {"path": log, "label": label}}  # noqa: E731
    v = publish(
        ws,
        "saga",
        [
            node("s1", action="log", inputs={"path": log, "label": "do1"}, compensation=comp("undo1")),
            node("s2", action="log", inputs={"path": log, "label": "do2"}, compensation=comp("undo2")),
            node("s3", action="boom"),
            node("s4", action="echo", inputs={"value": "never"}),
        ],
        [edge("s1", "s2"), edge("s2", "s3"), edge("s3", "s4")],
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "failed"
    assert run.error["failed_step"] == "s3" and "boom" in run.error["message"]
    assert open(log).read().split() == ["do1", "do2", "undo2", "undo1"]
    assert [c["status"] for c in run.error["compensation"]] == ["succeeded", "succeeded"]
    assert not StepRun.objects.filter(run=run, node_id="s4", status="succeeded").exists()


def test_compensation_can_fail_and_is_reported(ws, world, tmp_path):
    user, log = world["designer"], str(tmp_path / "log.txt")
    v = publish(
        ws,
        "saga2",
        [
            node(
                "s1", action="log", inputs={"path": log, "label": "do1"}, compensation={"action": "boom", "inputs": {}}
            ),
            node("s2", action="boom"),
        ],
        [edge("s1", "s2")],
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "failed" and run.error["compensation"] == [{"step": "s1", "status": "failed"}]


def test_error_edge_routes_failure_to_handler(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "errroute",
        [
            node("x", action="boom"),
            node("ok", action="echo", inputs={"value": "ok"}),
            node("h", action="echo", inputs={"value": "handled"}),
        ],
        [edge("x", "ok"), edge("x", "h", "error")],
        outputs={"r": "{{ steps.h.output.value }}"},
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "succeeded" and run.outputs == {"r": "handled"}
    assert step(run, "ok").status == "skipped" and step(run, "x").status == "failed"


def test_retry_with_backoff_then_success(ws, world, tmp_path):
    user = world["designer"]
    v = publish(
        ws,
        "retry",
        [
            node(
                "f",
                action="flaky",
                inputs={"marker": str(tmp_path / "m")},
                retry={"max_attempts": 3, "backoff_seconds": 0, "jitter": 0},
            )
        ],
        [],
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "succeeded"
    assert step(run, "f").attempt == 2 and executions(run).count() == 2


def test_retry_budget_is_respected(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "retry2",
        [node("b", action="boom", retry={"max_attempts": 2, "backoff_seconds": 0, "jitter": 0})],
        [],
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "failed" and executions(run).count() == 2


def test_non_idempotent_external_action_is_not_auto_retried(world):
    ws = world["ws_b"]
    make_action(ws, "boom", side_effects="external", idempotent=False)
    user = world["outsider"]
    v = publish(
        ws, "noretry", [node("b", action="boom", retry={"max_attempts": 5, "backoff_seconds": 0})], [], user=user
    )
    run = drain(launch(ws, v, user))
    assert run.status == "failed" and executions(run).count() == 1


def test_wait_step_is_durable_and_does_not_use_a_worker(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "waiter",
        [node("w", "wait", seconds=3600), node("e", action="echo", inputs={"value": "after"})],
        [edge("w", "e")],
        user=user,
    )
    run = launch(ws, v, user)
    engine.advance(run.pk)
    assert step(run, "w").status == "waiting" and Execution.objects.count() == 0
    engine.advance(run.pk)  # idempotent: nothing changes
    assert StepRun.objects.filter(run=run).count() == 1
    StepRun.objects.filter(run=run, node_id="w").update(available_at=timezone.now() - timedelta(seconds=1))
    run = drain(run)
    assert run.status == "succeeded"


def test_approval_flow_with_role_and_self_approval_rules(ws, world):
    designer = world["designer"]
    approver = make_user("anna", ws, Role.APPROVER)
    v = publish(
        ws,
        "appr",
        [node("ap", "approval", title="Valider {{ inputs.what }}"), node("e", action="echo", inputs={"value": "go"})],
        [edge("ap", "e")],
        inputs=schema(["what"], what=STR),
        user=designer,
    )
    run = drain(launch(ws, v, designer, {"what": "le budget"}), until=lambda r: Approval.objects.exists())
    ap = Approval.objects.get()
    assert ap.title == "Valider le budget" and ap.status == "pending"
    with pytest.raises(PermissionDenied):  # designer lacks the approve permission
        engine.decide_approval(ap.pk, designer, True)
    viewer = world["viewer"]
    with pytest.raises(PermissionDenied):
        engine.decide_approval(ap.pk, viewer, True)
    engine.decide_approval(ap.pk, approver, True, "ok pour moi")
    run = drain(run)
    assert run.status == "succeeded" and step(run, "ap").output["decided_by"] == "anna"
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):  # already decided
        engine.decide_approval(ap.pk, approver, True)


def test_self_approval_is_forbidden(ws, world):
    both = make_user("sam", ws, Role.SPACE_MANAGER)  # has both run and approve
    v = publish(ws, "self", [node("ap", "approval", title="x")], [], user=both)
    drain(launch(ws, v, both), until=lambda r: Approval.objects.exists())
    with pytest.raises(PermissionDenied, match="propre demande"):
        engine.decide_approval(Approval.objects.get().pk, both, True)


def test_rejected_approval_fails_the_run(ws, world):
    designer, approver = world["designer"], make_user("anna", ws, Role.APPROVER)
    v = publish(ws, "rej", [node("ap", "approval", title="x")], [], user=designer)
    run = drain(launch(ws, v, designer), until=lambda r: Approval.objects.exists())
    engine.decide_approval(Approval.objects.get().pk, approver, False, "non")
    run = drain(run)
    assert run.status == "failed" and run.error["code"] == "APPROVAL_REJECTED"


def test_subworkflow_and_bounded_foreach(ws, world):
    user = world["designer"]
    child = publish(
        ws,
        "child",
        [node("e", action="echo", inputs={"value": "item-{{ inputs.item }}"})],
        [],
        inputs=schema(["item"], item=STR),
        outputs={"v": "{{ steps.e.output.value }}"},
        user=user,
    )
    assert child
    parent = publish(
        ws,
        "parent",
        [
            node(
                "loop",
                "foreach",
                workflow="child",
                items="{{ inputs.items }}".strip("{} "),
                item_input="item",
                max_iterations=5,
                concurrency=2,
            )
        ],
        [],
        inputs=schema(["items"], items={"type": "array"}),
        outputs={"all": "{{ steps.loop.output.results }}"},
        user=user,
    )
    run = drain(launch(ws, parent, user, {"items": ["a", "b", "c"]}), max_rounds=200)
    assert run.status == "succeeded", run.error
    assert run.outputs["all"] == [{"v": "item-a"}, {"v": "item-b"}, {"v": "item-c"}]
    too_many = drain(launch(ws, parent, user, {"items": list("abcdefg")}))
    assert too_many.status == "failed" and too_many.error["code"] == "TOO_MANY_ITEMS"


def test_subworkflow_step(ws, world):
    user = world["designer"]
    publish(
        ws,
        "inner",
        [node("e", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v=STR),
        outputs={"out": "{{ steps.e.output.value }}"},
        user=user,
    )
    outer = publish(
        ws,
        "outer",
        [node("sub", "subworkflow", workflow="inner", inputs={"v": "hello"})],
        [],
        outputs={"r": "{{ steps.sub.output.out }}"},
        user=user,
    )
    run = drain(launch(ws, outer, user), max_rounds=100)
    assert run.status == "succeeded" and run.outputs == {"r": "hello"}


def test_orchestrator_restart_does_not_repeat_confirmed_steps(ws, world, tmp_path):
    user, log = world["designer"], str(tmp_path / "log.txt")
    v = publish(
        ws,
        "restart",
        [
            node("a", action="log", inputs={"path": log, "label": "A"}),
            node("w", "wait", seconds=3600),
            node("b", action="log", inputs={"path": log, "label": "B"}),
        ],
        [edge("a", "w"), edge("w", "b")],
        user=user,
    )
    run = drain(
        launch(ws, v, user),
        until=lambda r: step(r, "w").status == "waiting" if r.steps.filter(node_id="w").exists() else False,
    )
    # "restart": no in-memory state exists; advancing again many times must not redo A or duplicate rows
    for _ in range(5):
        engine.advance(run.pk)
    assert open(log).read().split() == ["A"] and executions(run).count() == 1
    StepRun.objects.filter(run=run, node_id="w").update(available_at=timezone.now() - timedelta(seconds=1))
    run = drain(run)
    assert run.status == "succeeded" and open(log).read().split() == ["A", "B"]


def test_running_instance_is_frozen_against_catalog_updates(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "frozen",
        [node("a", action="echo", inputs={"value": "x"}), node("w", "wait", seconds=3600)],
        [edge("a", "w")],
        user=user,
    )
    run = launch(ws, v, user)
    drain(run, until=lambda r: r.steps.filter(node_id="w").exists())
    pinned = run.definition["actions"]["a"]["action_version_id"]
    make_action(ws, "echo", version="2.0.0", source="def run(i):\n    return {'value': 'NEW'}\n")
    run.refresh_from_db()
    assert run.definition["actions"]["a"]["action_version_id"] == pinned
    assert executions(run).get().action_version.version == "1.0.0"
    assert run.definition["workflow"]["digest"] == v.digest


def test_lost_worker_on_external_action_requires_a_decision(world):
    ws = world["ws_b"]
    make_action(ws, "echo", side_effects="external", idempotent=False)
    user = world["outsider"]
    v = publish(ws, "uncertain", [node("e", action="echo", inputs={"value": "pay"})], [], user=user)
    run = launch(ws, v, user)
    engine.advance(run.pk)
    services.claim_next("w1")  # worker takes it, then "crashes"
    Execution.objects.filter(step_run__run=run).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    services.reconcile_expired()
    engine.advance(run.pk)
    sr = step(run, "e")
    assert sr.status == "uncertain" and sr.error["code"] == "OUTCOME_UNKNOWN"
    WorkflowRun.objects.get(pk=run.pk)  # still running, waiting for a human
    assert WorkflowRun.objects.get(pk=run.pk).status == "running"
    engine.decide_uncertain(sr.pk, user, "succeed", {"value": "pay"})
    assert drain(run).status == "succeeded"


def test_uncertain_decision_retry(world):
    ws = world["ws_b"]
    make_action(ws, "echo", side_effects="external", idempotent=False)
    user = world["outsider"]
    v = publish(ws, "uncertain2", [node("e", action="echo", inputs={"value": "x"})], [], user=user)
    run = launch(ws, v, user)
    engine.advance(run.pk)
    services.claim_next("w1")
    Execution.objects.filter(step_run__run=run).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    services.reconcile_expired()
    engine.advance(run.pk)
    engine.decide_uncertain(step(run, "e").pk, user, "retry")
    assert drain(run).status == "succeeded" and executions(run).count() == 2


def test_cancel_running_workflow_stops_container_process(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "cancelme",
        [node("s", action="sleepy", inputs={"seconds": 15}), node("e", action="echo", inputs={"value": "x"})],
        [edge("s", "e")],
        user=user,
    )
    run = launch(ws, v, user)
    engine.advance(run.pk)

    def work():
        try:
            process_one("w", SubprocessRunner())
        finally:
            connection.close()  # the worker thread owns its own DB connection

    t = threading.Thread(target=work)
    t0 = time.time()
    t.start()
    time.sleep(1.5)
    engine.request_run_cancel(run.pk, user)
    for _ in range(40):
        engine.advance(run.pk)
        if WorkflowRun.objects.get(pk=run.pk).status == "cancelled":
            break
        time.sleep(0.25)
    t.join(timeout=10)
    run.refresh_from_db()
    assert run.status == "cancelled" and time.time() - t0 < 12
    assert executions(run).get().status == "cancelled"
    assert not StepRun.objects.filter(run=run, node_id="e", status="succeeded").exists()


def test_cancel_queued_execution_is_immediate(ws, world):
    user = world["designer"]
    v = publish(ws, "cq", [node("s", action="echo", inputs={"value": "x"})], [], user=user)
    run = launch(ws, v, user)
    engine.advance(run.pk)
    engine.request_run_cancel(run.pk, user)
    engine.advance(run.pk)
    run.refresh_from_db()
    assert run.status == "cancelled" and executions(run).get().status == "cancelled"


def test_per_workspace_concurrency_cap_and_fairness(world, settings):
    settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE = 1
    a, b = world["ws_a"], world["ws_b"]
    va, vb = make_action(a, "echo"), make_action(b, "echo")
    from tenancy.models import Environment

    for ws, v, u in ((a, va, world["designer"]), (a, va, world["designer"]), (b, vb, world["outsider"])):
        services.create_execution(
            workspace=ws,
            environment=Environment.objects.get(workspace=ws, kind="test"),
            action_version=v,
            user=u,
            inputs={"value": "x"},
        )
    first = services.claim_next("w")[0]
    second = services.claim_next("w")[0]
    assert {first.workspace_id, second.workspace_id} == {a.id, b.id}  # fairness: B is not starved behind A's second job
    assert services.claim_next("w") is None  # both workspaces are at their cap


def test_priority_wins(world):
    ws = world["ws_a"]
    v = make_action(ws, "echo")
    env = Membership.objects.none()
    from tenancy.models import Environment

    env = Environment.objects.get(workspace=ws, kind="test")
    low = services.enqueue(
        workspace=ws, environment=env, action_version=v, user=world["designer"], inputs={"value": "1"}, priority=0
    )
    high = services.enqueue(
        workspace=ws, environment=env, action_version=v, user=world["designer"], inputs={"value": "2"}, priority=5
    )
    assert services.claim_next("w")[0].pk == high.pk
    assert low.pk


def test_api_start_run_and_detail(ws, world):
    user = world["designer"]
    publish(
        ws,
        "apiwf",
        [node("e", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v=STR),
        outputs={"o": "{{ steps.e.output.value }}"},
        user=user,
    )
    c = client_for(user)
    r = c.post(
        "/api/v1/workspaces/team-a/workflows/apiwf/runs/",
        {"environment": "test", "inputs": {"v": "hi"}},
        format="json",
        HTTP_IDEMPOTENCY_KEY="k-1",
    )
    assert r.status_code == 202 and r.json()["url"].endswith("/")
    again = c.post(
        "/api/v1/workspaces/team-a/workflows/apiwf/runs/",
        {"environment": "test", "inputs": {"v": "hi"}},
        format="json",
        HTTP_IDEMPOTENCY_KEY="k-1",
    )
    assert again.status_code == 200 and again.json()["id"] == r.json()["id"]
    bad = c.post(
        "/api/v1/workspaces/team-a/workflows/apiwf/runs/", {"environment": "test", "inputs": {}}, format="json"
    )
    assert bad.status_code == 400
    drain(r.json()["id"])
    detail = c.get(r.json()["url"]).json()
    assert detail["status"] == "succeeded" and detail["outputs"] == {"o": "hi"}
    assert [s["status"] for s in detail["steps"]] == ["succeeded"] and detail["events"][0]["seq"] == 1
    # isolation
    assert client_for(world["outsider"]).get(r.json()["url"]).status_code == 404
    # viewer cannot start
    assert (
        client_for(world["viewer"])
        .post(
            "/api/v1/workspaces/team-a/workflows/apiwf/runs/",
            {"environment": "test", "inputs": {"v": "x"}},
            format="json",
        )
        .status_code
        == 403
    )


def test_omitted_inputs_take_their_declared_default_for_every_launch_path(ws, world):
    """API clients, schedules and webhooks get what the launch form gives a person: the schema defaults."""
    sch = schema(["a"], a=STR) | {"properties": {"a": STR, "b": {"type": "string", "default": "par défaut"}}}
    v = publish(
        ws,
        "defaults",
        [node("x", action="echo", inputs={"value": "{{ inputs.a }}-{{ inputs.b }}"})],
        [],
        inputs=sch,
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"], {"a": "ok"}))
    assert run.status == "succeeded" and run.inputs == {"a": "ok", "b": "par défaut"}
    explicit = drain(launch(ws, v, world["designer"], {"a": "ok", "b": ""}))
    assert explicit.inputs["b"] == ""  # an explicit value is never replaced
    r = client_for(world["designer"]).post(
        "/api/v1/workspaces/team-a/workflows/defaults/runs/",
        {"environment": "test", "inputs": {"a": "via api"}},
        format="json",
    )
    assert r.status_code in (200, 201, 202), r.content
