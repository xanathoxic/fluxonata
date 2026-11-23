"""Cache nommé, lot 2 (ADR 0032) : publication automatique du résultat d'un workflow, API de gestion (liste, détail, invalidation), droits."""

from datetime import timedelta

from django.utils import timezone

from audit.models import AuditEvent
from runs import step_cache
from runs.models import CacheEntry
from tenancy.models import Environment
from tests.conftest import client_for
from tests.wf_helpers import drain, launch, node, publish
from workflows.models import Workflow


def producer(ws, user, slug="inventaire", outputs=None, **wf):
    version = publish(
        ws,
        slug,
        [node("m", "transform", op="set", fields={"serveurs": ["a", "b"], "n": 2})],
        [],
        outputs=outputs or {"serveurs": "{{ steps.m.output.serveurs }}"},
        user=user,
    )
    Workflow.objects.filter(workspace=ws, slug=slug).update(**wf)
    return version


def run_it(ws, version, user, env="test"):
    return drain(launch(ws, version, user, env=env))


def test_a_successful_run_publishes_its_outputs_under_the_workflow_key(ws, world):
    user = world["designer"]
    version = producer(ws, user, cache_key="inventaire-serveurs", cache_ttl_seconds=3600)
    run = run_it(ws, version, user)
    assert run.status == "succeeded"
    entry = CacheEntry.objects.get(workspace=ws, key="inventaire-serveurs")
    assert entry.value == {"serveurs": ["a", "b"]} and entry.version == 1 and entry.expires_at is not None
    assert (
        entry.environment.kind == "test"
        and entry.written_by_workflow == "inventaire"
        and entry.written_by_run_id == run.pk
    )
    assert run.events.filter(type="cache_published").exists()
    run_it(ws, version, user)
    assert CacheEntry.objects.get(workspace=ws, key="inventaire-serveurs").version == 2


def test_nothing_is_published_without_a_key_or_when_the_run_fails(ws, world):
    user = world["designer"]
    assert run_it(ws, producer(ws, user), user).status == "succeeded"
    assert not CacheEntry.objects.exists()
    bad = publish(ws, "casse", [node("t", "throw", message="boom")], [], user=user)
    Workflow.objects.filter(workspace=ws, slug="casse").update(cache_key="casse")
    assert run_it(ws, bad, user).status == "failed"
    assert not CacheEntry.objects.exists()


def test_a_value_too_big_for_the_cache_never_fails_the_run(ws, world, monkeypatch):
    user = world["designer"]
    monkeypatch.setattr(step_cache, "MAX_VALUE_BYTES", 5)
    run = run_it(ws, producer(ws, user, cache_key="gros"), user)
    assert run.status == "succeeded" and not CacheEntry.objects.exists()
    assert "non publié" in run.events.get(type="cache_published").message


def test_published_result_is_read_by_another_workflow(ws, world):
    user = world["designer"]
    run_it(ws, producer(ws, user, cache_key="inventaire-serveurs"), user)
    reader = publish(ws, "lecteur", [node("r", "cache", operation="read", key="inventaire-serveurs")], [], user=user)
    out = run_it(ws, reader, user).steps.get(node_id="r").output
    assert out["found"] is True and out["value"] == {"serveurs": ["a", "b"]}


def test_meta_validates_and_audits_the_cache_settings(ws, world):
    producer(ws, world["designer"])
    base = f"/api/v1/workspaces/{ws.slug}/workflows/inventaire/meta/"
    c = client_for(world["designer"])
    r = c.patch(base, {"cache_key": "inventaire-serveurs", "cache_ttl_seconds": 21600}, format="json")
    assert (
        r.status_code == 200
        and r.json()["cache_key"] == "inventaire-serveurs"
        and r.json()["cache_ttl_seconds"] == 21600
    )
    assert c.patch(base, {"cache_key": "a b"}, format="json").status_code == 400
    assert c.patch(base, {"cache_key": "{{ inputs.x }}"}, format="json").status_code == 400
    for bad in (0, -1, "1h", True, step_cache.MAX_TTL_SECONDS + 1):
        assert c.patch(base, {"cache_ttl_seconds": bad}, format="json").status_code == 400
    assert c.patch(base, {"cache_ttl_seconds": None}, format="json").json()["cache_ttl_seconds"] is None
    assert c.patch(base, {"cache_key": ""}, format="json").json()["cache_key"] == ""
    assert client_for(world["viewer"]).patch(base, {"cache_key": "x"}, format="json").status_code in (403, 404)
    assert AuditEvent.objects.filter(action="workflow.update", data__fields__contains=["cache_key"]).exists()


def entries(ws):
    env = Environment.objects.get(workspace=ws, kind="test")
    now = timezone.now()
    return [
        CacheEntry.objects.create(
            workspace=ws, environment=env, key=k, value=v, version=1, size_bytes=10, expires_at=exp, updated_at=now
        )
        for k, v, exp in (
            ("alpha", {"secret-ish": 1}, None),
            ("beta", [1, 2], now - timedelta(seconds=1)),
        )
    ]


def test_list_never_returns_values_and_filters(ws, world):
    entries(ws)
    c = client_for(world["designer"])
    body = c.get(f"/api/v1/workspaces/{ws.slug}/caches/").json()
    assert body["count"] == 2 and [r["key"] for r in body["results"]] == ["alpha", "beta"]
    assert all("value" not in r for r in body["results"])
    assert {r["key"]: r["expired"] for r in body["results"]} == {"alpha": False, "beta": True}
    assert c.get(f"/api/v1/workspaces/{ws.slug}/caches/?q=alp").json()["count"] == 1
    assert c.get(f"/api/v1/workspaces/{ws.slug}/caches/?environment=prod").json()["count"] == 0
    assert c.get(f"/api/v1/workspaces/{ws.slug}/caches/?environment=zzz").status_code == 400
    exact = c.get(f"/api/v1/workspaces/{ws.slug}/caches/?key=alpha").json()
    assert exact["count"] == 1 and exact["results"][0]["key"] == "alpha"
    assert c.get(f"/api/v1/workspaces/{ws.slug}/caches/?key=alp").json()["count"] == 0  # exacte : pas une recherche


def test_detail_returns_the_value_and_delete_invalidates_with_audit_without_the_value(ws, world):
    alpha, _ = entries(ws)
    c = client_for(world["designer"])
    url = f"/api/v1/workspaces/{ws.slug}/caches/{alpha.pk}/"
    assert c.get(url).json()["value"] == {"secret-ish": 1}
    assert c.delete(url).status_code == 204
    assert c.get(url).status_code == 404
    event = AuditEvent.objects.get(action="cache.delete")
    assert event.data["key"] == "alpha" and "secret-ish" not in str(event.data)


def test_only_designers_of_the_workspace_reach_the_cache(ws, world):
    alpha, _ = entries(ws)
    url = f"/api/v1/workspaces/{ws.slug}/caches/"
    for who in ("viewer", "outsider"):
        c = client_for(world[who])
        assert c.get(url).status_code in (403, 404)
        assert c.get(f"{url}{alpha.pk}/").status_code in (403, 404)
        assert c.delete(f"{url}{alpha.pk}/").status_code in (403, 404)
    assert CacheEntry.objects.filter(pk=alpha.pk).exists()
    # une entrée d'un autre espace n'est pas adressable par l'espace courant
    other = world["ws_b"]
    foreign = CacheEntry.objects.create(
        workspace=other,
        environment=Environment.objects.get(workspace=other, kind="test"),
        key="x",
        value=1,
        updated_at=timezone.now(),
    )
    assert client_for(world["designer"]).get(f"{url}{foreign.pk}/").status_code == 404
