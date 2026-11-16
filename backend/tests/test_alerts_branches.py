"""Alertes : filtres des règles, évaluation du taux d'échec, livraison (succès, nouvelle tentative avec attente croissante, refus définitif,
limite de tentatives). Complète test_ops.py et test_alert_rules.py par les branches de refus et d'échec."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core import netsec
from runs import alerts
from runs.models import AlertDelivery, AlertRule, WorkflowRun
from tests.wf_helpers import drain, launch, node, publish


def make_rule(ws, user, **kw):
    rule = AlertRule(workspace=ws, name="r", event=kw.pop("event", "run_failed"), created_by=user, **kw)
    rule.set_url("http://127.0.0.1:9/alerte")
    rule.save()
    return rule


@pytest.fixture
def failing(ws, world):
    return publish(ws, "casse", [node("s", action="boom")], [], user=world["designer"])


def test_a_failed_run_queues_one_alert_per_matching_rule_with_its_facts(ws, world, failing):
    make_rule(ws, world["designer"], format="json")
    run = drain(launch(ws, failing, world["designer"]))
    delivery = AlertDelivery.objects.get()
    assert delivery.payload["event"] == "run_failed" and delivery.payload["workflow"] == "casse"
    assert (
        delivery.payload["run_id"] == str(run.id)
        and delivery.payload["environment"] == "test"
        and delivery.payload["step"] == "s"
    )
    assert "Exécution échouée : casse (test)" in delivery.payload["text"]


def test_a_rule_for_another_environment_or_workflow_or_a_disabled_one_stays_silent(ws, world, failing):
    other = publish(ws, "autre", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    make_rule(ws, world["designer"], environment="prod")
    make_rule(ws, world["designer"], workflow=other.workflow)
    make_rule(ws, world["designer"], enabled=False)
    drain(launch(ws, failing, world["designer"]))
    assert AlertDelivery.objects.count() == 0
    make_rule(ws, world["designer"], environment="test", workflow=failing.workflow)
    drain(launch(ws, failing, world["designer"]))
    assert AlertDelivery.objects.count() == 1


def test_a_failure_of_a_sub_workflow_does_not_alert_on_its_own(ws, world):
    user = world["designer"]
    publish(ws, "enfant", [node("s", action="boom")], [], user=user)
    parent = publish(ws, "parent", [node("c", "subworkflow", workflow="enfant", inputs={})], [], user=user)
    make_rule(ws, user)
    drain(launch(ws, parent, user))
    assert AlertDelivery.objects.count() == 1  # the top-level run only, not the child run too


def _finish_runs(ws, user, version, failed, ok):
    for status, n in (("failed", failed), ("succeeded", ok)):
        for _ in range(n):
            run = drain(launch(ws, version, user))
            WorkflowRun.objects.filter(pk=run.pk).update(status=status, finished_at=timezone.now())


def test_the_failure_rate_needs_enough_runs_and_respects_environment_and_workflow(ws, world, failing):
    user = world["designer"]
    _finish_runs(ws, user, failing, failed=3, ok=1)
    make_rule(ws, user, event="failure_rate", threshold_percent=50, min_runs=5)
    assert alerts.evaluate_rates() == 0  # 4 runs: not enough to judge
    AlertRule.objects.update(min_runs=4, environment="prod")
    assert alerts.evaluate_rates() == 0  # nothing ran in production
    other = publish(ws, "autre", [node("e", action="echo", inputs={"value": "x"})], [], user=user)
    AlertRule.objects.update(environment="test", workflow=other.workflow)
    assert alerts.evaluate_rates() == 0  # that workflow has no run
    AlertRule.objects.update(workflow=failing.workflow)
    assert alerts.evaluate_rates() == 1
    assert AlertDelivery.objects.get().payload["text"].startswith("⚠️ Taux d'échec 75 % (3/4)")


def test_the_failure_rate_stays_below_its_threshold_without_firing(ws, world, failing):
    user = world["designer"]
    _finish_runs(ws, user, failing, failed=1, ok=3)
    make_rule(ws, user, event="failure_rate", threshold_percent=50, min_runs=4)
    assert alerts.evaluate_rates() == 0 and AlertDelivery.objects.count() == 0


def _queue(ws, user):
    rule = make_rule(ws, user)
    AlertDelivery.objects.create(
        rule=rule, payload={"text": "x"}, next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    return AlertDelivery.objects.get()


def test_a_delivery_that_succeeds_is_marked_delivered(ws, world, monkeypatch):
    sent = []
    monkeypatch.setattr(
        netsec, "safe_request", lambda method, url, **kw: sent.append((method, url, kw["body"])) or (204, {}, b"")
    )
    _queue(ws, world["designer"])
    assert alerts.deliver_due() == 1
    d = AlertDelivery.objects.get()
    assert d.status == "delivered" and d.attempts == 1 and d.last_error == ""
    assert sent[0][0] == "POST" and sent[0][1] == "http://127.0.0.1:9/alerte" and b'"text"' in sent[0][2]


@pytest.mark.parametrize("failure", [lambda *a, **k: (500, {}, b""), None])
def test_a_transient_failure_is_retried_later_with_a_growing_delay(ws, world, monkeypatch, failure):
    def boom(*a, **k):
        raise OSError("réseau")

    monkeypatch.setattr(netsec, "safe_request", failure or boom)
    d = _queue(ws, world["designer"])
    before = timezone.now()
    assert alerts.deliver_due() == 0
    d.refresh_from_db()
    assert d.status == "pending" and d.attempts == 1 and d.next_attempt_at >= before + timedelta(seconds=29)
    assert d.last_error.startswith(("HTTP 500", "Réseau : OSError"))
    d.next_attempt_at = timezone.now() - timedelta(seconds=1)
    d.save()
    alerts.deliver_due()
    d.refresh_from_db()
    assert d.attempts == 2 and d.next_attempt_at >= before + timedelta(seconds=59)  # 30 s, then 60 s


def test_a_refused_destination_fails_at_once_without_retry(ws, world, monkeypatch):
    def refuse(*a, **k):
        raise netsec.UnsafeURL("adresse interne")

    monkeypatch.setattr(netsec, "safe_request", refuse)
    d = _queue(ws, world["designer"])
    alerts.deliver_due()
    d.refresh_from_db()
    assert d.status == "failed" and d.attempts == 1 and "Adresse refusée : adresse interne" in d.last_error


def test_the_last_allowed_attempt_ends_in_failure(ws, world, monkeypatch):
    monkeypatch.setattr(netsec, "safe_request", lambda *a, **k: (503, {}, b""))
    d = _queue(ws, world["designer"])
    AlertDelivery.objects.filter(pk=d.pk).update(attempts=alerts.MAX_ATTEMPTS - 1)
    alerts.deliver_due()
    d.refresh_from_db()
    assert d.status == "failed" and d.attempts == alerts.MAX_ATTEMPTS and d.last_error == "HTTP 503"


def test_a_delivery_not_yet_due_or_already_taken_is_left_alone(ws, world, monkeypatch):
    monkeypatch.setattr(netsec, "safe_request", lambda *a, **k: pytest.fail("aucun envoi attendu"))
    d = _queue(ws, world["designer"])
    AlertDelivery.objects.filter(pk=d.pk).update(next_attempt_at=timezone.now() + timedelta(hours=1))
    assert alerts.deliver_due() == 0
    AlertDelivery.objects.filter(pk=d.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1), status="delivered"
    )
    assert alerts.deliver_due() == 0
