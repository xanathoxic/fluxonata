"""API store (extension `apistore`) : souscription, clés, point d'application unique, audit sans valeurs d'entrée."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apistore import service
from apistore.models import ApiKey, Product, ProductWorkflow, UsageEvent
from audit.models import AuditEvent
from tenancy.models import ServiceAccount
from tests.apistore_helpers import BASE, active_key, call  # noqa: F401
from tests.conftest import make_workspace
from tests.test_engine_start import raw_version
from tests.wf_helpers import node
from workflows.models import Workflow


def test_an_automatic_plan_activates_at_once_and_a_manual_one_waits_for_someone_else(store):
    auto = service.subscribe(store["product"], store["auto"], consumer="a", user=store["asker"])
    manual = service.subscribe(store["product"], store["manual"], consumer="b", user=store["asker"])
    assert auto.status == "active" and manual.status == "pending"
    with pytest.raises(service.Refused) as own:
        service.decide(manual, store["asker"], approve=True)
    assert own.value.status == 403
    assert service.decide(manual, store["owner"], approve=False).status == "rejected"
    with pytest.raises(service.Refused):
        service.decide(manual, store["owner"], approve=True)  # déjà traitée


def test_no_subscription_to_an_unpublished_product(store):
    store["product"].status = Product.Status.DRAFT
    store["product"].save()
    with pytest.raises(service.Refused):
        service.subscribe(store["product"], store["auto"], consumer="a", user=store["asker"])


def test_the_execution_identity_must_belong_to_the_workspace_of_the_product(store):
    foreign = ServiceAccount.objects.create(
        user=get_user_model().objects.create_user("svc.other.bot", password=None),
        workspace=make_workspace("other"),
        name="bot",
    )
    with pytest.raises(service.Refused):
        service.set_product_run_as(store["product"], foreign)
    service.set_product_run_as(store["product"], store["bot"])


def test_a_product_is_publishable_only_with_identity_plan_and_published_workflows(store):
    p = store["product"]
    service.check_publishable(p)
    p.run_as = None
    with pytest.raises(service.Refused):
        service.check_publishable(p)
    p.run_as = store["bot"]
    ProductWorkflow.objects.create(
        product=p, workflow=Workflow.objects.create(workspace=p.workspace, slug="vide", name="v")
    )
    with pytest.raises(service.Refused):
        service.check_publishable(p)


def test_only_the_hash_of_a_key_is_stored_and_it_authenticates(store):
    _, key, raw = active_key(store)
    assert raw.startswith("fxs_") and raw not in (key.key_hash, key.prefix) and len(key.key_hash) == 64
    assert service.authenticate(raw).pk == key.pk


@pytest.mark.parametrize("bad", ["", "fxs_inconnue", "orch_pas-une-cle-de-store"])
def test_an_unknown_key_is_refused(store, bad):
    assert call(bad).status_code == 401


def test_an_expired_or_revoked_key_or_inactive_subscription_is_refused(store):
    sub, key, raw = active_key(store)
    assert call(raw).status_code == 202
    ApiKey.objects.filter(pk=key.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert call(raw).status_code == 401
    ApiKey.objects.filter(pk=key.pk).update(expires_at=timezone.now() + timedelta(days=1))
    service.set_status(sub, store["owner"], "suspended")
    assert call(raw).status_code == 403
    service.set_status(sub, store["owner"], "active")
    assert call(raw).status_code == 202
    service.revoke_key(key, store["owner"])
    assert call(raw).status_code == 401


def test_revoking_a_subscription_revokes_its_keys_for_good(store):
    sub, key, raw = active_key(store)
    service.set_status(sub, store["owner"], "revoked")
    key.refresh_from_db()
    assert key.revoked_at is not None and call(raw).status_code == 401
    # Réactivation depuis l'archive : l'ancienne clé reste morte, une nouvelle clé est nécessaire.
    service.set_status(sub, store["owner"], "active")
    assert call(raw).status_code == 401
    _, fresh = service.issue_key(sub, store["owner"])
    assert call(fresh).status_code == 202


def test_the_call_runs_the_workflow_as_the_service_account_and_returns_the_run(store):
    _, _, raw = active_key(store)
    r = call(raw)
    assert r.status_code == 202 and r.json()["status"] in ("queued", "running", "succeeded")
    from runs.models import WorkflowRun

    run = WorkflowRun.objects.get(pk=r.json()["run_id"])
    assert run.requested_by.username == "svc.store.bot" and run.environment.kind == "test"


def test_a_workflow_outside_the_product_or_another_product_gives_the_same_answer(store):
    raw_version(store["ws"], "secret", [node("j", "join")], [], store["owner"])
    _, _, raw = active_key(store)
    a = call(raw, workflow="secret")
    b = call(raw, product="autre")
    c = call(raw, ws="ailleurs")
    assert (a.status_code, b.status_code, c.status_code) == (404, 404, 404)
    assert a.json()["code"] == b.json()["code"] == c.json()["code"] == "forbidden"


def test_the_plan_limits_the_environments(store):
    _, _, raw = active_key(store)  # plan libre : test seulement
    assert call(raw, {"environment": "prod", "inputs": {}}).status_code == 403
    assert call(raw, {"environment": "test", "inputs": {}}).status_code == 202


def test_the_minute_quota_refuses_the_next_call_and_does_not_count_refusals(store):
    sub, _, raw = active_key(store, per_minute=2)
    assert [call(raw).status_code for _ in range(2)] == [202, 202]
    assert call(raw).status_code == 429 and call(raw).status_code == 429
    assert UsageEvent.objects.filter(subscription=sub, outcome="accepted").count() == 2
    assert UsageEvent.objects.filter(subscription=sub, outcome="rate_limited").count() == 2
    UsageEvent.objects.filter(subscription=sub).update(created_at=timezone.now() - timedelta(minutes=2))
    assert call(raw).status_code == 202  # la fenêtre a glissé


def test_the_daily_quota_applies_and_a_null_limit_is_unlimited(store):
    sub, _, raw = active_key(store, per_minute=None, per_day=1)
    assert call(raw).status_code == 202 and call(raw).status_code == 429
    store["auto"].per_day = None
    store["auto"].save()
    assert call(raw).status_code == 202


def test_a_replayed_idempotency_key_does_not_consume_quota_or_start_a_second_run(store):
    sub, _, raw = active_key(store, per_minute=1)
    first = call(raw, HTTP_IDEMPOTENCY_KEY="abc")
    again = call(raw, HTTP_IDEMPOTENCY_KEY="abc")
    assert first.status_code == 202 and again.status_code == 200 and again.json()["replayed"] is True
    assert first.json()["run_id"] == again.json()["run_id"]
    assert UsageEvent.objects.filter(subscription=sub, outcome="accepted").count() == 1


def test_invalid_inputs_are_refused_with_a_400_and_logged(store):
    _, _, raw = active_key(store)
    r = call(raw, {"environment": "test", "inputs": "pas un objet"})
    assert r.status_code == 400
    r = call(raw, {"environment": "inconnu", "inputs": {}})
    assert r.status_code == 400


def test_audit_and_usage_never_keep_input_values_or_the_key(store):
    _, _, raw = active_key(store)
    secret = "valeur-sensible-123"
    call(raw, {"environment": "test", "inputs": {"champ": secret}})
    call(raw, {"environment": "prod", "inputs": {"champ": secret}})  # refusé : environnement
    dump = " ".join(str(e.data) + e.action for e in AuditEvent.objects.all()) + " ".join(
        str(u.__dict__) for u in UsageEvent.objects.all()
    )
    assert secret not in dump and raw not in dump
    assert AuditEvent.objects.filter(action="apistore.call_refused").exists()
    assert AuditEvent.objects.filter(action="apistore.key_issued").exists()


def test_a_consumer_reads_only_the_runs_of_its_own_subscription(store):
    sub1, _, raw1 = active_key(store)
    sub2 = service.subscribe(store["product"], store["auto"], consumer="autre", user=store["asker"])
    _, raw2 = service.issue_key(sub2, store["owner"])
    run_id = call(raw1).json()["run_id"]
    c = APIClient()
    url = f"{BASE}/store/p1/runs/{run_id}/"
    assert c.get(url, HTTP_AUTHORIZATION=f"Bearer {raw1}").status_code == 200
    assert c.get(url, HTTP_AUTHORIZATION=f"Bearer {raw2}").status_code == 404
    assert c.get(url).status_code == 401


def test_a_governance_refusal_is_reported_and_not_counted(store):
    from tenancy.models import PolicyVersion

    ws = store["ws"]
    PolicyVersion.objects.create(
        workspace=ws, version=1, rules={"prod_requires_promotion": True}, created_by=store["owner"]
    )
    sub, _, raw = active_key(store, store["manual"])
    r = call(raw, {"environment": "prod", "inputs": {}})
    assert r.status_code == 403 and r.json()["code"] == "governance"
    assert UsageEvent.objects.filter(subscription=sub, outcome="accepted").count() == 0


def test_plan_environments_are_validated():
    assert service.validate_plan_environments(["prod", "test", "test"]) == ["prod", "test"]
    for bad in ([], ["lune"], "test", None):
        with pytest.raises(ValueError):
            service.validate_plan_environments(bad)


def test_an_idempotency_key_is_never_shared_between_two_subscriptions(store):
    _, _, raw1 = active_key(store)
    sub2 = service.subscribe(store["product"], store["auto"], consumer="autre", user=store["asker"])
    _, raw2 = service.issue_key(sub2, store["owner"])
    a = call(raw1, HTTP_IDEMPOTENCY_KEY="meme-cle")
    b = call(raw2, HTTP_IDEMPOTENCY_KEY="meme-cle")
    assert b.status_code == 202 and a.json()["run_id"] != b.json()["run_id"]
