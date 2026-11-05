import hashlib
import hmac
import json

import pytest
from rest_framework.test import APIClient

from runs.models import WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    publish(
        world["ws_a"],
        "reagit",
        [node("e", action="echo", inputs={"value": "{{ inputs.event }}"})],
        [],
        inputs=schema(["payload"], payload={"type": "object"}, event={"type": "string"}, delivery={"type": "string"}),
        user=world["designer"],
    )
    publish(world["ws_a"], "sans", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    return world["ws_a"]


def _hook(world, provider, **extra):
    r = client_for(world["designer"]).post(
        f"{BASE}/webhooks/",
        {"name": provider, "workflow": "reagit", "environment": "test", "provider": provider, **extra},
        format="json",
    )
    assert r.status_code == 201, r.data
    return r.data["url"], r.data["secret"]


def _sig(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_github_signature_event_filter_and_replay(ws, world):
    url, secret = _hook(world, "github", events=["push"])
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    anon = APIClient()
    h = {"HTTP_X_HUB_SIGNATURE_256": _sig(secret, body), "HTTP_X_GITHUB_EVENT": "push", "HTTP_X_GITHUB_DELIVERY": "d-1"}
    ok = anon.post(url, body, content_type="application/json", **h)
    assert ok.status_code == 202
    run = WorkflowRun.objects.get(workspace=ws, workflow_version__workflow__slug="reagit")
    assert run.inputs == {"payload": {"ref": "refs/heads/main"}, "event": "push", "delivery": "d-1"}
    assert anon.post(url, body, content_type="application/json", **h).data["status"] == "duplicate"
    ignored = anon.post(
        url,
        body,
        content_type="application/json",
        **{**h, "HTTP_X_GITHUB_EVENT": "star", "HTTP_X_GITHUB_DELIVERY": "d-2"},
    )
    assert ignored.data["status"] == "ignored" and WorkflowRun.objects.filter(workspace=ws).count() == 1
    bad = anon.post(
        url,
        body,
        content_type="application/json",
        **{**h, "HTTP_X_HUB_SIGNATURE_256": _sig("autre", body), "HTTP_X_GITHUB_DELIVERY": "d-3"},
    )
    assert bad.status_code == 401


def test_gitlab_token_jira_signature_and_bearer_token(ws, world):
    anon = APIClient()
    body = json.dumps({"object_kind": "push"}).encode()
    url, secret = _hook(world, "gitlab")
    assert anon.post(url, body, content_type="application/json", HTTP_X_GITLAB_TOKEN="faux").status_code == 401
    ok = anon.post(
        url,
        body,
        content_type="application/json",
        HTTP_X_GITLAB_TOKEN=secret,
        HTTP_X_GITLAB_EVENT="Push Hook",
        HTTP_X_GITLAB_EVENT_UUID="u-1",
    )
    assert ok.status_code == 202
    url, secret = _hook(world, "jira")
    jbody = json.dumps({"webhookEvent": "jira:issue_created"}).encode()
    assert (
        anon.post(url, jbody, content_type="application/json", HTTP_X_HUB_SIGNATURE=_sig(secret, jbody)).status_code
        == 202
    )
    assert WorkflowRun.objects.filter(inputs__event="jira:issue_created").exists()
    url, secret = _hook(world, "token")
    assert (
        anon.post(url, body, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {secret}").status_code == 202
    )
    assert anon.post(url, body, content_type="application/json").status_code == 401


def test_a_provider_webhook_needs_a_workflow_that_declares_payload(ws, world):
    r = client_for(world["designer"]).post(
        f"{BASE}/webhooks/", {"name": "x", "workflow": "sans", "provider": "github"}, format="json"
    )
    assert r.status_code == 400 and "payload" in str(r.data)
    r = client_for(world["designer"]).post(
        f"{BASE}/webhooks/", {"name": "x", "workflow": "reagit", "provider": "inconnu"}, format="json"
    )
    assert r.status_code == 400


def test_a_provider_delivery_survives_a_failure_before_the_run_starts(ws, world, monkeypatch):
    """AR0.4 : même garantie que pour les webhooks signés — le service (GitHub…) réessaie la MÊME livraison après une erreur 5xx et obtient son run."""
    from triggers import views as trigger_views

    url, secret = _hook(world, "github", events=["push"])
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    h = {
        "HTTP_X_HUB_SIGNATURE_256": _sig(secret, body),
        "HTTP_X_GITHUB_EVENT": "push",
        "HTTP_X_GITHUB_DELIVERY": "d-crash",
    }
    real, calls = trigger_views.engine.start_run, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("PostgreSQL est indisponible")
        return real(*a, **k)

    monkeypatch.setattr(trigger_views.engine, "start_run", flaky)
    with pytest.raises(RuntimeError):
        APIClient().post(url, body, content_type="application/json", **h)
    assert WorkflowRun.objects.count() == 0
    assert APIClient().post(url, body, content_type="application/json", **h).status_code == 202
    again = APIClient().post(url, body, content_type="application/json", **h)
    assert again.status_code == 200 and again.json()["status"] == "duplicate" and WorkflowRun.objects.count() == 1
