"""Cache nommé, lot 3 (ADR 0032) : reconstruction automatique par le workflow producteur, verrou de reconstruction, valeurs sensibles."""

import pytest

from audit.models import AuditEvent  # noqa: F401  (l'audit ne contient jamais de valeur : voir test_cache_manage)
from runs import engine, step_cache
from runs.models import CacheEntry, WorkflowRun
from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import STR, drain, launch, node, publish, schema
from workflows.models import Workflow
from workflows.validation import validate

KEY = "inventaire"


def producer(ws, user, slug="producteur", key=KEY, nodes=None, inputs=None, outputs=None, **wf):
    version = publish(
        ws,
        slug,
        nodes or [node("m", "transform", op="set", fields={"serveurs": ["a", "b"]})],
        [],
        inputs=inputs,
        outputs={"serveurs": "{{ steps.m.output.serveurs }}"} if outputs is None else outputs,
        user=user,
    )
    Workflow.objects.filter(workspace=ws, slug=slug).update(cache_key=key, **wf)
    return version


def reader(ws, user, slug="lecteur", **extra):
    return publish(
        ws, slug, [node("r", "cache", operation="read", key=KEY, on_missing="rebuild", **extra)], [], user=user
    )


def out(run, nid="r"):
    return run.steps.get(node_id=nid).output


def error(run, nid="r"):
    return run.steps.get(node_id=nid).error["code"]


def test_a_missing_value_is_rebuilt_by_the_producer_then_read(ws, world):
    user = world["designer"]
    producer(ws, user)
    run = drain(launch(ws, reader(ws, user), user))
    assert run.status == "succeeded"
    assert out(run)["found"] is True and out(run)["rebuilt"] is True and out(run)["value"] == {"serveurs": ["a", "b"]}
    build = WorkflowRun.objects.get(workflow_version__workflow__slug="producteur")
    assert build.parent_step.run_id == run.pk and build.status == "succeeded"
    assert run.events.filter(type="cache_rebuild").exists()


def test_a_fresh_value_is_read_without_starting_the_producer(ws, world):
    user = world["designer"]
    prod = producer(ws, user)
    drain(launch(ws, prod, user))
    run = drain(launch(ws, reader(ws, user), user))
    assert out(run)["found"] is True and out(run)["rebuilt"] is False
    assert WorkflowRun.objects.filter(workflow_version__workflow__slug="producteur").count() == 1


def test_a_value_older_than_the_reader_accepts_is_rebuilt(ws, world):
    from datetime import timedelta

    from django.utils import timezone

    user = world["designer"]
    prod = producer(ws, user)
    drain(launch(ws, prod, user))
    CacheEntry.objects.update(updated_at=timezone.now() - timedelta(seconds=500))
    run = drain(launch(ws, reader(ws, user, max_age_seconds=100), user))
    assert out(run)["rebuilt"] is True and out(run)["version"] == 2


def test_two_readers_share_one_rebuild(ws, world):
    user = world["designer"]
    producer(ws, user)
    version = reader(ws, user)
    a, b = launch(ws, version, user), launch(ws, version, user)
    engine.advance(a.pk)  # a trouve la valeur absente et lance le producteur
    engine.advance(b.pk)  # b la trouve absente aussi : il rejoint, il ne relance rien
    assert WorkflowRun.objects.filter(workflow_version__workflow__slug="producteur").count() == 1
    drain(a)
    drain(b)
    assert WorkflowRun.objects.filter(workflow_version__workflow__slug="producteur").count() == 1
    for run in (a, b):
        run.refresh_from_db()
        assert run.status == "succeeded" and out(run)["found"] is True and out(run)["rebuilt"] is True
    assert "rejoint" in b.events.get(type="cache_rebuild").message


def test_rebuild_inputs_are_passed_to_the_producer(ws, world):
    user = world["designer"]
    producer(
        ws,
        user,
        nodes=[node("m", "transform", op="set", fields={"site": "{{ inputs.site }}"})],
        inputs=schema(["site"], site=STR),
        outputs={"site": "{{ steps.m.output.site }}"},
    )
    v = reader(ws, user, rebuild_inputs={"site": "paris"})
    assert out(drain(launch(ws, v, user)))["value"] == {"site": "paris"}


def test_a_failing_producer_fails_the_reader_with_a_clear_code(ws, world):
    user = world["designer"]
    producer(ws, user, nodes=[node("t", "throw", message="boom")], outputs={})
    run = drain(launch(ws, reader(ws, user), user))
    assert run.status == "failed" and error(run) == "CACHE_REBUILD_FAILED"


def test_a_producer_that_publishes_nothing_is_reported(ws, world, monkeypatch):
    user = world["designer"]
    producer(ws, user)
    monkeypatch.setattr(step_cache, "MAX_VALUE_BYTES", 5)
    assert error(drain(launch(ws, reader(ws, user), user))) == "CACHE_REBUILD_EMPTY"


def test_no_producer_and_ambiguous_producers_are_refused(ws, world):
    user = world["designer"]
    assert error(drain(launch(ws, reader(ws, user), user))) == "CACHE_NO_PRODUCER"
    producer(ws, user, slug="un")
    producer(ws, user, slug="deux")
    assert (
        error(
            drain(
                launch(
                    ws,
                    reader(
                        ws,
                        user,
                    ),
                    user,
                )
            )
        )
        == "CACHE_AMBIGUOUS_PRODUCER"
    )


def test_a_producer_waiting_for_its_own_key_fails_instead_of_deadlocking(ws, world):
    user = world["designer"]
    producer(ws, user, nodes=[node("r", "cache", operation="read", key=KEY, on_missing="rebuild")], outputs={})
    run = drain(launch(ws, reader(ws, user), user))
    assert run.status == "failed"
    inner = WorkflowRun.objects.get(workflow_version__workflow__slug="producteur")
    assert error(inner) == "CACHE_REBUILD_LOOP" and error(run) == "CACHE_REBUILD_FAILED"


def test_a_finished_rebuild_never_holds_the_key(ws, world):
    user = world["designer"]
    producer(ws, user, nodes=[node("t", "throw", message="boom")], outputs={})
    drain(launch(ws, reader(ws, user), user))
    producer(ws, user)  # corrigé : le verrou du lancement échoué ne retient plus rien
    assert (
        out(
            drain(
                launch(
                    ws,
                    reader(
                        ws,
                        user,
                    ),
                    user,
                )
            )
        )["rebuilt"]
        is True
    )


def test_sensitive_entries_are_masked_for_non_administrators(ws, world):
    user = world["designer"]
    admin = make_user("ada", ws, Role.ADMIN)
    w = publish(
        ws,
        "ecrit",
        [node("w", "cache", operation="write", key="secret-ish", value={"a": 1}, sensitive=True)],
        [],
        user=user,
    )
    drain(launch(ws, w, user))
    entry = CacheEntry.objects.get(key="secret-ish")
    assert entry.sensitive is True
    url = f"/api/v1/workspaces/{ws.slug}/caches/{entry.pk}/"
    hidden = client_for(user).get(url).json()
    assert hidden["masked"] is True and hidden["value"] is None and hidden["sensitive"] is True
    shown = client_for(admin).get(url).json()
    assert shown["masked"] is False and shown["value"] == {"a": 1}
    assert client_for(user).get(f"/api/v1/workspaces/{ws.slug}/caches/").json()["results"][0]["sensitive"] is True


def test_a_workflow_can_publish_its_result_as_sensitive(ws, world):
    user = world["designer"]
    prod = producer(ws, user, cache_sensitive=True)
    drain(launch(ws, prod, user))
    assert CacheEntry.objects.get(key=KEY).sensitive is True
    base = f"/api/v1/workspaces/{ws.slug}/workflows/producteur/meta/"
    c = client_for(user)
    assert c.patch(base, {"cache_sensitive": False}, format="json").json()["cache_sensitive"] is False
    assert c.patch(base, {"cache_sensitive": "oui"}, format="json").status_code == 400


@pytest.mark.parametrize(
    "fields",
    [
        {"operation": "read", "key": "k", "on_missing": "boom"},
        {"operation": "read", "key": "k", "on_missing": "rebuild", "rebuild_inputs": "x"},
        {"operation": "write", "key": "k", "value": "x", "sensitive": "oui"},
    ],
)
def test_validation_rejects_bad_rebuild_and_sensitive_options(ws, fields):
    graph = {"inputs": schema(), "nodes": [node("c", "cache", **fields)], "edges": [], "outputs": {}}
    assert "bad_cache" in {i["code"] for i in validate(graph, ws, "v") if i["level"] == "error"}


def test_rebuild_is_a_valid_configuration(ws):
    graph = {
        "inputs": schema(),
        "nodes": [
            node("c", "cache", operation="read", key="k", on_missing="rebuild", rebuild_inputs={"a": "{{ inputs.x }}"})
        ],
        "edges": [],
        "outputs": {},
    }
    errors = [i for i in validate(graph, ws, "v") if i["level"] == "error"]
    assert [e["code"] for e in errors] == ["unknown_input"]  # seule la référence à une entrée inexistante est refusée
