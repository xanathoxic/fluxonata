import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from runs.models import WorkflowRun
from tenancy.models import Environment, Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import STR, make_action, node, publish, schema
from triggers import scheduler
from triggers.models import Schedule, Webhook

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    w = world["ws_a"]
    make_action(w, "echo")
    publish(
        w,
        "wf1",
        [node("a", action="echo", inputs={"value": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v=STR),
        user=world["designer"],
    )
    return w


def mk_schedule(world, ws, **kw):
    sch = Schedule.objects.create(
        workspace=ws,
        workflow=ws.workflows.get(slug="wf1"),
        environment=Environment.objects.get(workspace=ws, kind="test"),
        name="s",
        cron="*/5 * * * *",
        timezone="UTC",
        inputs={"v": "x"},
        created_by=world["designer"],
        **kw,
    )
    sch.next_run_at = timezone.now() - timedelta(seconds=10)
    sch.save()
    return sch


def test_schedule_fires_once_and_advances(ws, world):
    sch = mk_schedule(world, ws)
    assert scheduler.fire_due() == 1
    sch.refresh_from_db()
    assert sch.last_run is not None and sch.next_run_at > timezone.now()
    assert scheduler.fire_due() == 0  # nothing due, no duplicate
    assert WorkflowRun.objects.count() == 1


def test_missed_occurrences_are_skipped_or_run_once(ws, world):
    sch = mk_schedule(world, ws, missed_policy="skip")
    Schedule.objects.filter(pk=sch.pk).update(next_run_at=timezone.now() - timedelta(hours=3))
    assert scheduler.fire_due() == 0
    sch.refresh_from_db()
    assert sch.last_note.startswith("missed_skipped") and sch.next_run_at > timezone.now()
    sch2 = mk_schedule(world, ws, missed_policy="run_once")
    Schedule.objects.filter(pk=sch2.pk).update(next_run_at=timezone.now() - timedelta(hours=3))
    assert scheduler.fire_due() == 1 and WorkflowRun.objects.count() == 1  # 36 missed slots coalesced into one
    assert AuditEvent.objects.filter(action="schedule.missed_skipped").exists()


def test_overlap_policy(ws, world):
    sch = mk_schedule(world, ws, overlap_policy="skip")
    scheduler.fire_due()
    Schedule.objects.filter(pk=sch.pk).update(next_run_at=timezone.now() - timedelta(seconds=5))
    assert scheduler.fire_due() == 0  # previous run still running -> skipped
    sch.refresh_from_db()
    assert sch.last_note.startswith("overlap_skipped")
    Schedule.objects.filter(pk=sch.pk).update(next_run_at=timezone.now() - timedelta(seconds=5), overlap_policy="allow")
    assert scheduler.fire_due() == 1 and WorkflowRun.objects.count() == 2


def test_schedule_is_disabled_when_owner_loses_the_right(ws, world):
    sch = mk_schedule(world, ws)
    from tenancy.models import Membership

    Membership.objects.filter(user=world["designer"], workspace=ws).update(role=Role.VIEWER)
    assert scheduler.fire_due() == 0
    sch.refresh_from_db()
    assert sch.enabled is False and "plus le droit" in sch.last_note


def test_schedule_respects_governance_in_prod(ws, world):
    sch = mk_schedule(world, ws)
    Schedule.objects.filter(pk=sch.pk).update(environment=Environment.objects.get(workspace=ws, kind="prod"))
    assert scheduler.fire_due() == 0
    sch.refresh_from_db()
    assert sch.last_note.startswith("refused") and sch.enabled is True and WorkflowRun.objects.count() == 0


def test_schedule_api_validation_and_preview(ws, world):
    c = client_for(world["designer"])
    ok = c.post(
        f"{BASE}/schedules/",
        {
            "name": "Quotidien",
            "workflow": "wf1",
            "environment": "test",
            "cron": "0 8 * * *",
            "timezone": "Europe/Paris",
            "inputs": {"v": "a"},
        },
        format="json",
    )
    assert ok.status_code == 201 and len(ok.json()["next_runs"]) == 3
    assert (
        c.post(
            f"{BASE}/schedules/",
            {"name": "x", "workflow": "wf1", "cron": "99 * * * *", "timezone": "UTC", "inputs": {"v": "a"}},
            format="json",
        ).status_code
        == 400
    )
    assert (
        c.post(
            f"{BASE}/schedules/",
            {"name": "x", "workflow": "wf1", "cron": "* * * * *", "timezone": "Mars/Base", "inputs": {"v": "a"}},
            format="json",
        ).status_code
        == 400
    )
    assert (
        c.post(
            f"{BASE}/schedules/",
            {"name": "x", "workflow": "wf1", "cron": "* * * * *", "timezone": "UTC", "inputs": {}},
            format="json",
        ).status_code
        == 400
    )
    pr = c.post(f"{BASE}/schedules/preview/", {"cron": "0 8 * * *", "timezone": "Europe/Paris"}, format="json").json()
    assert len(pr["next"]) == 5 and pr["local"][0].endswith("08:00")
    assert client_for(world["viewer"]).post(f"{BASE}/schedules/", {}, format="json").status_code == 403
    assert client_for(world["outsider"]).get(f"{BASE}/schedules/").status_code == 404


# ------------------------------------------------------------------ webhooks
def create_hook(world):
    r = client_for(world["designer"]).post(
        f"{BASE}/webhooks/", {"name": "crm", "workflow": "wf1", "environment": "test"}, format="json"
    )
    assert r.status_code == 201, r.content
    return r.json()


def signed(secret, body: dict | bytes, ts=None, delivery=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    ts = str(ts or int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    headers = {"HTTP_X_FLUXONATA_TIMESTAMP": ts, "HTTP_X_FLUXONATA_SIGNATURE": f"v1={sig}"}
    if delivery:
        headers["HTTP_X_FLUXONATA_DELIVERY"] = delivery
    return raw, headers


def post(hook, raw, headers):
    return APIClient().post(hook["url"], data=raw, content_type="application/json", **headers)


def test_webhook_valid_signature_starts_a_run(ws, world):
    hook = create_hook(world)
    assert hook["secret"].startswith("whsec_") and "plus jamais" in hook["notice"]
    raw, h = signed(hook["secret"], {"v": "bonjour"})
    r = post(hook, raw, h)
    assert r.status_code == 202 and WorkflowRun.objects.get().inputs == {"v": "bonjour"}
    assert WorkflowRun.objects.get().definition["governance"]["trigger"] == "webhook"


def test_webhook_rejects_bad_signature_stale_timestamp_and_replays(ws, world):
    hook = create_hook(world)
    raw, h = signed(hook["secret"], {"v": "a"})
    bad = dict(h, HTTP_X_FLUXONATA_SIGNATURE="v1=" + "0" * 64)
    assert post(hook, raw, bad).status_code == 401
    assert post(hook, raw, {}).status_code == 401  # headers absent
    old_raw, old = signed(hook["secret"], {"v": "a"}, ts=int(time.time()) - 3600)
    assert post(hook, old_raw, old).status_code == 401  # outside the time window
    other_raw, other = signed("whsec_wrong", {"v": "a"})
    assert post(hook, other_raw, other).status_code == 401
    assert WorkflowRun.objects.count() == 0
    assert post(hook, raw, h).status_code == 202
    replay = post(hook, raw, h)  # exact same delivery
    assert replay.status_code == 200 and replay.json()["status"] == "duplicate" and WorkflowRun.objects.count() == 1


def test_webhook_tampered_body_and_invalid_payloads(ws, world):
    hook = create_hook(world)
    raw, h = signed(hook["secret"], {"v": "a"})
    assert post(hook, raw.replace(b'"a"', b'"b"'), h).status_code == 401  # body changed after signing
    r2, h2 = signed(hook["secret"], {"wrong": 1})
    assert post(hook, r2, h2).status_code == 400  # fails the workflow input contract
    r3, h3 = signed(hook["secret"], b"not json")
    assert post(hook, r3, h3).status_code == 400


def test_disabled_webhook_unknown_id_and_secret_rotation(ws, world):
    hook = create_hook(world)
    raw, h = signed(hook["secret"], {"v": "a"})
    c = client_for(world["designer"])
    rotated = c.post(f"{BASE}/webhooks/{hook['id']}/").json()["secret"]
    assert post(hook, raw, h).status_code == 401  # old secret invalidated
    raw2, h2 = signed(rotated, {"v": "a"})
    assert post(hook, raw2, h2).status_code == 202
    Webhook.objects.filter(pk=hook["id"]).update(enabled=False)
    raw3, h3 = signed(rotated, {"v": "a"}, delivery="d3")
    assert post(hook, raw3, h3).status_code == 401
    assert (
        APIClient()
        .post("/api/v1/hooks/00000000-0000-0000-0000-000000000000/", data=b"{}", content_type="application/json")
        .status_code
        == 401
    )


def test_webhook_secret_is_encrypted_at_rest_and_not_in_audit(ws, world):
    hook = create_hook(world)
    w = Webhook.objects.get(pk=hook["id"])
    assert hook["secret"] not in w.secret_ciphertext and w.reveal_secret() == hook["secret"]
    assert not any(hook["secret"] in json.dumps(e.data) for e in AuditEvent.objects.all())
    assert hook["secret"] not in client_for(world["designer"]).get(f"{BASE}/webhooks/").content.decode()


def test_webhook_is_rate_limited(ws, world, monkeypatch):
    from rest_framework.throttling import ScopedRateThrottle

    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"webhook": "3/min", "login": "10/min"})
    hook = create_hook(world)
    codes = [post(hook, *signed(hook["secret"], {"v": "a"}, delivery=f"d{i}")).status_code for i in range(5)]
    assert 429 in codes


def test_webhook_management_is_permissioned(ws, world):
    assert (
        client_for(world["viewer"])
        .post(f"{BASE}/webhooks/", {"name": "x", "workflow": "wf1"}, format="json")
        .status_code
        == 403
    )
    hook = create_hook(world)
    assert client_for(world["outsider"]).delete(f"{BASE}/webhooks/{hook['id']}/").status_code == 404
    assert make_user  # keep import used
