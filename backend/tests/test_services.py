from datetime import timedelta

import pytest
from django.utils import timezone

from catalog.demo_actions import SLOW
from runs import services
from runs.models import Attempt, Execution
from tenancy.models import Environment
from tests.conftest import add_action


def new_exec(world, version=None):
    env = Environment.objects.get(workspace=world["ws_a"], kind="test")
    ex, _ = services.create_execution(
        workspace=world["ws_a"],
        environment=env,
        action_version=version or world["version_a"],
        user=world["designer"],
        inputs={"text": "a b"},
    )
    return ex


def test_claim_complete_happy_path(world):
    ex = new_exec(world)
    claimed, attempt = services.claim_next("w1")
    assert claimed.pk == ex.pk and attempt == 1
    assert services.claim_next("w2") is None  # nothing else queued
    assert services.complete_attempt(ex.pk, 1, ok=True, result={"characters": 3})
    ex.refresh_from_db()
    assert ex.status == "succeeded" and ex.result == {"characters": 3}
    seqs = list(ex.events.values_list("seq", flat=True))
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))


def test_illegal_transition_is_refused(world):
    ex = new_exec(world)
    with pytest.raises(ValueError):
        services._transition(ex, "succeeded")


def test_expired_lease_requeues_and_fences_late_answer(world):
    ex = new_exec(world)
    services.claim_next("w1")
    Execution.objects.filter(pk=ex.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert services.reconcile_expired() == 1
    ex.refresh_from_db()
    assert ex.status == "queued"
    _, attempt2 = services.claim_next("w2")
    assert attempt2 == 2
    # the old worker answers late: must not overwrite the new attempt
    assert services.complete_attempt(ex.pk, 1, ok=True, result={"stale": True}) is False
    ex.refresh_from_db()
    assert ex.status == "running" and ex.result is None
    assert services.complete_attempt(ex.pk, 2, ok=True, result={"fresh": True})
    ex.refresh_from_db()
    assert ex.result == {"fresh": True}
    assert list(Attempt.objects.filter(execution=ex).values_list("status", flat=True)) == ["lost", "succeeded"]


def test_lost_attempt_with_external_side_effect_is_not_retried(world):
    v = add_action(world["ws_b"], SLOW, side_effects="external", idempotent=False)
    ws_b_env = Environment.objects.get(workspace=world["ws_b"], kind="test")
    ex, _ = services.create_execution(
        workspace=world["ws_b"], environment=ws_b_env, action_version=v, user=world["outsider"], inputs={"text": "x"}
    )
    services.claim_next("w1")
    Execution.objects.filter(pk=ex.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    services.reconcile_expired()
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "OUTCOME_UNKNOWN"


def test_attempts_exhausted(world, settings):
    settings.ORCH_MAX_ATTEMPTS = 2
    ex = new_exec(world)
    for _ in range(2):
        services.claim_next("w")
        Execution.objects.filter(pk=ex.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        services.reconcile_expired()
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "ATTEMPTS_EXHAUSTED"


def test_heartbeat_extends_only_live_attempt(world):
    ex = new_exec(world)
    services.claim_next("w1")
    assert services.heartbeat(ex.pk, 1) is True
    assert services.heartbeat(ex.pk, 99) is False
