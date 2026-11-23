"""Nœud « Cache » (ADR 0032) : un workflow écrit une valeur, un autre la lit ; TTL, âge maximal, version, bornes, isolation."""

from datetime import timedelta

import pytest
from django.utils import timezone

from runs import step_cache
from runs.models import CacheEntry
from tenancy.models import Environment
from tests.wf_helpers import STR, drain, launch, node, publish, schema
from workflows.validation import validate

TEXT = schema([], text=STR)


def writer(ws, user, value="{{ inputs.text }}", **extra):
    return publish(
        ws,
        "producteur",
        [node("w", "cache", operation="write", key="inventaire", value=value, **extra)],
        [],
        user=user,
        inputs=TEXT,
    )


def reader(ws, user, slug="consommateur", **extra):
    return publish(ws, slug, [node("r", "cache", operation="read", key="inventaire", **extra)], [], user=user)


def output(run, nid):
    return run.steps.get(node_id=nid).output


def run_it(ws, version, user, env="test", inputs=None):
    return drain(launch(ws, version, user, inputs=inputs, env=env))


def test_one_workflow_writes_another_reads_it(ws, world):
    user = world["designer"]
    w = run_it(ws, writer(ws, user, value={"serveurs": ["a", "b"], "n": 2}), user)
    assert w.status == "succeeded" and output(w, "w")["version"] == 1 and output(w, "w")["written"] is True
    r = run_it(ws, reader(ws, user), user)
    assert output(r, "r")["found"] is True and output(r, "r")["value"] == {"serveurs": ["a", "b"], "n": 2}
    assert output(r, "r")["version"] == 1 and output(r, "r")["age_seconds"] >= 0
    entry = CacheEntry.objects.get(workspace=ws, key="inventaire")
    assert entry.written_by_workflow == "producteur" and entry.size_bytes > 0 and entry.written_by_run_id == w.pk


def test_every_write_increments_version_and_replaces_value(ws, world):
    user = world["designer"]
    version = writer(ws, user, value="{{ inputs.text }}")
    run_it(ws, version, user, inputs={"text": "un"})
    again = run_it(ws, version, user, inputs={"text": "deux"})
    assert output(again, "w")["version"] == 2
    assert CacheEntry.objects.filter(workspace=ws).count() == 1
    assert output(run_it(ws, reader(ws, user), user), "r")["value"] == "deux"


def test_missing_key_is_empty_by_default_and_fails_on_request(ws, world):
    user = world["designer"]
    r = run_it(ws, reader(ws, user), user)
    assert r.status == "succeeded" and output(r, "r") == {
        "found": False,
        "value": None,
        "version": 0,
        "age_seconds": None,
        "key": "inventaire",
        "rebuilt": False,
    }
    strict = run_it(ws, reader(ws, user, slug="strict", on_missing="fail"), user)
    assert strict.status == "failed" and strict.steps.get(node_id="r").error["code"] == "CACHE_MISS"


def test_expired_entry_is_absent(ws, world):
    user = world["designer"]
    run_it(ws, writer(ws, user, value="x", ttl_seconds=60), user)
    CacheEntry.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert output(run_it(ws, reader(ws, user), user), "r")["found"] is False


def test_ttl_sets_expiry_and_no_ttl_never_expires(ws, world):
    user = world["designer"]
    w = run_it(ws, writer(ws, user, value="x", ttl_seconds=3600), user)
    assert output(w, "w")["expires_at"] is not None
    run_it(ws, writer(ws, user, value="y"), user)
    assert CacheEntry.objects.get(workspace=ws).expires_at is None


def test_max_age_ignores_a_value_older_than_the_reader_accepts(ws, world):
    user = world["designer"]
    run_it(ws, writer(ws, user, value="x"), user)
    CacheEntry.objects.update(updated_at=timezone.now() - timedelta(seconds=500))
    assert output(run_it(ws, reader(ws, user, max_age_seconds=1000), user), "r")["found"] is True
    assert output(run_it(ws, reader(ws, user, slug="frais", max_age_seconds=100), user), "r")["found"] is False


def test_delete_removes_the_entry(ws, world):
    user = world["designer"]
    run_it(ws, writer(ws, user, value="x"), user)
    d = publish(ws, "nettoyage", [node("d", "cache", operation="delete", key="inventaire")], [], user=user)
    assert output(run_it(ws, d, user), "d") == {"deleted": True, "key": "inventaire"}
    assert output(run_it(ws, d, user), "d")["deleted"] is False
    assert not CacheEntry.objects.exists()


def test_cache_is_isolated_per_environment_and_per_workspace(ws, world):
    user = world["designer"]
    Environment.objects.create(workspace=ws, kind="dev", name="dev")
    run_it(ws, writer(ws, user, value="test-value"), user, env="test")
    # l'écriture faite en « test » n'est pas visible depuis un autre environnement
    assert output(run_it(ws, reader(ws, user), user, env="dev"), "r")["found"] is False
    assert output(run_it(ws, reader(ws, user), user, env="test"), "r")["found"] is True
    other_user = world["outsider"]
    assert output(run_it(world["ws_b"], reader(world["ws_b"], other_user), other_user), "r")["found"] is False


def test_key_can_be_an_expression_and_is_checked_at_run_time(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "dyn",
        [node("w", "cache", operation="write", key="inv:{{ inputs.text }}", value="x")],
        [],
        user=user,
        inputs=TEXT,
    )
    assert output(run_it(ws, v, user, inputs={"text": "paris"}), "w")["key"] == "inv:paris"
    bad = run_it(ws, v, user, inputs={"text": "a b"})
    assert bad.status == "failed" and bad.steps.get(node_id="w").error["code"] == "CACHE_KEY"


def test_value_size_is_bounded(ws, world):
    user = world["designer"]
    big = run_it(ws, writer(ws, user, value="x" * (step_cache.MAX_VALUE_BYTES + 1)), user)
    assert big.status == "failed" and big.steps.get(node_id="w").error["code"] == "CACHE_TOO_BIG"
    edge = run_it(
        ws, writer(ws, user, value="x" * (step_cache.MAX_VALUE_BYTES - 2)), user
    )  # + 2 guillemets JSON = la limite
    assert (
        edge.status == "succeeded" and not CacheEntry.objects.filter(size_bytes__gt=step_cache.MAX_VALUE_BYTES).exists()
    )


def test_entry_quota_per_environment_counts_only_live_entries(ws, world, monkeypatch):
    user = world["designer"]
    monkeypatch.setattr(step_cache, "MAX_ENTRIES", 2)
    env = ws.environments.get(kind="test") if hasattr(ws, "environments") else None
    now = timezone.now()

    env = Environment.objects.get(workspace=ws, kind="test")
    for k, exp in (("a", None), ("vieux", now - timedelta(seconds=5))):
        CacheEntry.objects.create(workspace=ws, environment=env, key=k, value=1, expires_at=exp, updated_at=now)
    assert (
        run_it(ws, writer(ws, user, value="x"), user).status == "succeeded"
    )  # l'entrée expirée est purgée, place faite
    full = publish(ws, "trop", [node("w", "cache", operation="write", key="encore", value="x")], [], user=user)
    failed = run_it(ws, full, user)
    assert failed.status == "failed" and failed.steps.get(node_id="w").error["code"] == "CACHE_QUOTA"


@pytest.mark.parametrize(
    "fields,code",
    [
        ({"operation": "oops", "key": "k"}, "bad_cache"),
        ({"operation": "read", "key": ""}, "bad_cache"),
        ({"operation": "read", "key": "a b"}, "bad_cache"),
        ({"operation": "write", "key": "k"}, "bad_cache"),
        ({"operation": "write", "key": "k", "value": "x", "ttl_seconds": 0}, "bad_cache"),
        ({"operation": "read", "key": "k", "max_age_seconds": "1h"}, "bad_cache"),
        ({"operation": "read", "key": "k", "on_missing": "boom"}, "bad_cache"),
    ],
)
def test_validation_rejects_bad_configuration(ws, fields, code):
    graph = {
        "inputs": {"type": "object", "properties": {}},
        "nodes": [node("c", "cache", **fields)],
        "edges": [],
        "outputs": {},
    }
    assert code in {i["code"] for i in validate(graph, ws, "v") if i["level"] == "error"}


def test_read_output_is_usable_by_later_steps(ws, world):
    user = world["designer"]
    run_it(ws, writer(ws, user, value="x"), user)
    graph = [
        node("r", "cache", operation="read", key="inventaire"),
        node("c", "cache", operation="write", key="copie", value="{{ steps.r.output.value }}-copie"),
    ]
    v = publish(ws, "suite", graph, [{"from": "r", "to": "c"}], user=user)
    run_it(ws, v, user)
    assert CacheEntry.objects.get(workspace=ws, key="copie").value == "x-copie"
