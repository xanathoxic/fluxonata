"""Runner imposé à un workflow, et à ses sous-workflows si demandé : attribution exclusive, héritage et surcharge, refus clairs, jamais de bascule silencieuse."""

import hashlib

import pytest
from django.utils import timezone

from audit.models import AuditEvent
from runs import runner_pin, services
from runs.models import Execution, Runner
from tests.conftest import client_for
from tests.wf_helpers import STR, launch, node, publish, schema
from workflows.models import Workflow

WS = "/api/v1/workspaces/team-a"
ALL = ["python", "node", "typescript", "shell", "powershell"]


def runner(name, scope="shared", runtimes=None, tags=None, **kw):
    token = f"orr_{name}" + "0" * 40
    return Runner.objects.create(
        name=name,
        scope=scope,
        runtimes=runtimes or ALL,
        tags=tags or [],
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        token_prefix=token[:12],
        last_contact_at=timezone.now(),
        **kw,
    )


def set_runner(name, r=None, force=False):
    Workflow.objects.filter(slug=name).update(runner=r, runner_force_children=force)


def echo(i="e"):
    return node(i, action="echo", inputs={"value": "{{ inputs.v }}"})


def child_and_parent(ws, world, kind="subworkflow", child_runner=None, parent_runner=None, force=False):
    user = world["designer"]
    publish(ws, "enfant", [echo()], [], inputs=schema(["v"], v=STR), outputs={}, user=user)
    set_runner("enfant", child_runner)
    if kind == "subworkflow":
        step = node("c", "subworkflow", workflow="enfant", inputs={"v": "x"})
    elif kind == "foreach":
        step = node("c", "foreach", workflow="enfant", items="inputs.items", item_input="v", max_iterations=3)
    else:
        step = node("c", "while", workflow="enfant", condition="iteration >= 1", max_iterations=2, inputs={"v": "x"})
    version = publish(ws, "parent", [step], [], inputs=schema(items={"type": "array"}), outputs={}, user=user)
    set_runner("parent", parent_runner, force)
    return version


def advance_all(run_id):
    from runs import engine
    from runs.models import WorkflowRun

    engine.advance(run_id)
    for other in WorkflowRun.objects.exclude(pk=run_id):
        engine.advance(other.pk)
    engine.advance(run_id)


def queued(ws=None):
    return list(Execution.objects.filter(status="queued"))


# --- attribution exclusive -------------------------------------------------------------------------------------------------------------------
def test_a_pinned_execution_is_taken_by_its_runner_only(ws, world):
    mine, other = runner("mine"), runner("other")
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    set_runner("w", mine)
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    (ex,) = queued()
    assert ex.pinned and ex.pinned_runner_id == mine.pk
    assert services.claim_next("worker-intégré") is None  # un worker intégré ne la prend pas
    assert services.claim_next("runner:other", other) is None  # ni un autre runner, même partagé et compatible
    got = services.claim_next("runner:mine", mine)
    assert got is not None and got[0].pk == ex.pk
    assert run.definition["runner"]["name"] == "mine" and run.definition["runner"]["inherited"] is False


def test_without_a_pinned_runner_nothing_changes(ws, world):
    other = runner("other")
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    (ex,) = queued()
    assert not ex.pinned and ex.pinned_runner_id is None and "runner" not in run.definition
    assert services.claim_next("w1") is not None or services.claim_next("r", other) is not None


def test_the_effective_runner_is_frozen_at_launch(ws, world):
    first, second = runner("first"), runner("second")
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    set_runner("w", first)
    run = launch(ws, version, world["designer"], {"v": "a"})
    set_runner("w", second)  # réglage changé APRÈS le lancement : ce run reste sur « first »
    from runs import engine

    engine.advance(run.pk)
    assert queued()[0].pinned_runner_id == first.pk
    assert launch(ws, version, world["designer"], {"v": "b"}).definition["runner"]["name"] == "second"


# --- sous-workflows : héritage et surcharge ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["subworkflow", "foreach", "while"])
def test_with_the_override_the_children_use_the_parents_runner_even_if_assigned_another(ws, world, kind):
    big, small = runner("big"), runner("small")
    version = child_and_parent(ws, world, kind, child_runner=small, parent_runner=big, force=True)
    run = launch(ws, version, world["designer"], {"items": ["a"]})
    advance_all(run.pk)
    pinned = {e.pinned_runner_id for e in queued()}
    assert pinned == {big.pk}, (
        kind
    )  # le sous-workflow a son propre runner « small », mais la surcharge du parent l'emporte
    from runs.models import WorkflowRun

    child = WorkflowRun.objects.exclude(pk=run.pk).first()
    assert child.definition["runner"] == {**run.definition["runner"], "inherited": True}


@pytest.mark.parametrize("kind", ["subworkflow", "foreach", "while"])
def test_without_the_override_each_child_keeps_its_own_runner(ws, world, kind):
    big, small = runner("big"), runner("small")
    version = child_and_parent(ws, world, kind, child_runner=small, parent_runner=big, force=False)
    run = launch(ws, version, world["designer"], {"items": ["a"]})
    advance_all(run.pk)
    assert {e.pinned_runner_id for e in queued()} == {small.pk}


def test_a_child_without_a_runner_stays_free_unless_the_parent_forces_its_own(ws, world):
    big = runner("big")
    version = child_and_parent(ws, world, "subworkflow", child_runner=None, parent_runner=big, force=False)
    run = launch(ws, version, world["designer"], {"items": ["a"]})
    advance_all(run.pk)
    (ex,) = queued()
    assert not ex.pinned  # choix habituel pour l'enfant
    Execution.objects.all().delete()


def test_the_override_reaches_grandchildren(ws, world):
    big, small = runner("big"), runner("small")
    user = world["designer"]
    publish(ws, "petit-enfant", [echo()], [], inputs=schema(["v"], v=STR), user=user)
    set_runner("petit-enfant", small)
    publish(
        ws,
        "enfant",
        [node("g", "subworkflow", workflow="petit-enfant", inputs={"v": "x"})],
        [],
        inputs=schema(["v"], v=STR),
        user=user,
    )
    parent = publish(ws, "parent", [node("c", "subworkflow", workflow="enfant", inputs={"v": "x"})], [], user=user)
    set_runner("parent", big, force=True)
    run = launch(ws, parent, user)

    for _ in range(4):
        advance_all(run.pk)
    assert {e.pinned_runner_id for e in queued()} == {big.pk}  # trois niveaux plus bas, toujours « big »


def test_a_child_that_forces_its_runner_only_forces_its_own_subtree(ws, world):
    big, small = runner("big"), runner("small")
    user = world["designer"]
    publish(ws, "petit-enfant", [echo()], [], inputs=schema(["v"], v=STR), user=user)
    publish(
        ws,
        "enfant",
        [node("g", "subworkflow", workflow="petit-enfant", inputs={"v": "x"})],
        [],
        inputs=schema(["v"], v=STR),
        user=user,
    )
    set_runner("enfant", small, force=True)
    parent = publish(ws, "parent", [node("c", "subworkflow", workflow="enfant", inputs={"v": "x"})], [], user=user)
    set_runner("parent", big, force=False)
    run = launch(ws, parent, user)
    for _ in range(4):
        advance_all(run.pk)
    assert {e.pinned_runner_id for e in queued()} == {
        small.pk
    }  # « small » (l'enfant) impose le sien à son petit-enfant ; le parent n'imposait rien


# --- refus clairs ----------------------------------------------------------------------------------------------------------------------------
def failed_step(run, nid="e"):
    sr = run.steps.get(node_id=nid)
    return sr


def test_a_runner_that_cannot_run_the_language_fails_the_step_clearly_never_another_runner(ws, world):
    only_node = runner("seul-node", runtimes=["node"])
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    set_runner("w", only_node)
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    run.refresh_from_db()
    sr = run.steps.get(node_id="e")
    assert (
        sr.status == "failed"
        and sr.error["code"] == "RUNNER_INCOMPATIBLE"
        and "python" in sr.error["message"]
        and "seul-node" in sr.error["message"]
    )
    assert not Execution.objects.exists()  # rien n'a été mis en file, donc rien ne peut partir ailleurs


def test_a_missing_required_tag_fails_clearly(ws, world):
    from catalog.models import ActionVersion

    r = runner("sans-gpu")
    ActionVersion.objects.filter(action__slug="echo").update(required_tags=["gpu"])
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    set_runner("w", r)
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    err = run.steps.get(node_id="e").error
    assert err["code"] == "RUNNER_INCOMPATIBLE" and "gpu" in err["message"]


def test_a_dedicated_runner_of_another_space_is_refused(ws, world):
    foreign = runner("etranger", scope="dedicated")
    foreign.workspaces.add(world["ws_b"])
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    Workflow.objects.filter(slug="w").update(
        runner=foreign
    )  # contourne la validation de l'API : le moteur refuse quand même
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    assert run.steps.get(node_id="e").error["code"] == "RUNNER_UNAVAILABLE"


def test_an_offline_or_paused_runner_is_waited_for_not_replaced(ws, world):
    mine, spare = runner("mine"), runner("spare")
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    set_runner("w", mine)
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    Runner.objects.filter(pk=mine.pk).update(paused=True)
    assert services.claim_next("runner:spare", spare) is None and services.claim_next("w") is None
    assert run.steps.get(node_id="e").status == "running" and queued()  # elle attend « mine »
    Runner.objects.filter(pk=mine.pk).update(paused=False)
    assert services.claim_next("runner:mine", Runner.objects.get(pk=mine.pk)) is not None


def test_deleting_the_runner_fails_its_queued_executions_clearly(ws, world):
    mine = runner("mine", scope="dedicated")
    mine.workspaces.add(ws)
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    set_runner("w", mine)
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    ex = queued()[0]
    admin = world["designer"]
    admin.is_superuser = True
    admin.save()
    assert client_for(admin).delete(f"/api/v1/platform/runners/{mine.pk}/").status_code == 204
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "RUNNER_DELETED" and "mine" in ex.error["message"]
    assert services.claim_next("w") is None  # jamais reprise par un autre
    assert AuditEvent.objects.filter(action="runner.delete").latest("id").data["pinned_failed"] == 1
    assert Workflow.objects.get(slug="w").runner_id is None  # le réglage revient à « aucun runner imposé »


# --- API des réglages ------------------------------------------------------------------------------------------------------------------------
def patch(c, slug, body):
    return c.patch(f"{WS}/workflows/{slug}/meta/", body, format="json")


def test_the_settings_are_saved_audited_and_validated(ws, world):
    c = client_for(world["designer"])
    publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    mine = runner("mine")
    r = patch(c, "w", {"runner": str(mine.pk), "runner_force_children": True})
    assert (
        r.status_code == 200
        and r.json()["runner"] == str(mine.pk)
        and r.json()["runner_name"] == "mine"
        and r.json()["runner_force_children"] is True
    )
    assert c.get(f"{WS}/workflows/w/").json()["runner_name"] == "mine"
    assert AuditEvent.objects.filter(action="workflow.update").latest("id").data["runner"] == "mine"
    assert patch(c, "w", {"runner": ""}).json()["runner"] is None  # vide : plus de runner imposé
    foreign = runner("etranger", scope="dedicated")
    foreign.workspaces.add(world["ws_b"])
    bad = patch(c, "w", {"runner": str(foreign.pk)})
    assert bad.status_code == 400 and "non ouvert" in bad.content.decode()
    assert patch(c, "w", {"runner": "pas-un-uuid"}).status_code == 400
    assert (
        client_for(world["viewer"])
        .patch(f"{WS}/workflows/w/meta/", {"runner": str(mine.pk)}, format="json")
        .status_code
        == 403
    )


def test_shared_runners_disabled_makes_a_shared_runner_unavailable(ws, world):
    c = client_for(world["designer"])
    publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    shared = runner("partage")
    world["ws_a"].use_shared_runners = False
    world["ws_a"].save()
    assert patch(c, "w", {"runner": str(shared.pk)}).status_code == 400


def test_the_runner_choices_list_only_what_the_space_may_use_without_secrets(ws, world):
    c = client_for(world["designer"])
    runner("partage")
    mine, foreign = runner("mine", scope="dedicated"), runner("etranger", scope="dedicated")
    mine.workspaces.add(ws)
    foreign.workspaces.add(world["ws_b"])
    rows = c.get(f"{WS}/runner-choices/").json()["results"]
    assert {r["name"] for r in rows} == {"partage", "mine"}
    assert not any("token" in k for r in rows for k in r) and all(r["status"] == "online" for r in rows)
    assert client_for(world["viewer"]).get(f"{WS}/runner-choices/").status_code == 403


def test_validation_warns_about_a_step_the_runner_cannot_run(ws, world):
    c = client_for(world["designer"])
    only_node = runner("seul-node", runtimes=["node"])
    publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    patch(c, "w", {"runner": str(only_node.pk)})
    wf = Workflow.objects.get(slug="w")
    graph = {"inputs": schema(["v"], v=STR), "nodes": [echo()], "edges": [], "outputs": {}}
    saved = c.put(f"{WS}/workflows/w/draft/", {"graph": graph, "revision": wf.draft_revision}, format="json").json()
    warn = [i for i in saved["issues"] if i["code"] == "runner_incompatible"]
    assert warn and warn[0]["level"] == "warning" and warn[0]["node"] == "e" and "seul-node" in warn[0]["message"]
    assert not [i for i in saved["issues"] if i["level"] == "error"]
    assert (
        c.post(f"{WS}/workflows/w/publish/", {"bump": "minor"}, format="json").status_code == 201
    )  # un avertissement ne bloque pas
    patch(c, "w", {"runner": ""})
    wf.refresh_from_db()
    again = c.put(f"{WS}/workflows/w/draft/", {"graph": graph, "revision": wf.draft_revision}, format="json").json()
    assert not [i for i in again["issues"] if i["code"] == "runner_incompatible"]


def test_the_run_detail_and_the_health_say_which_runner_and_why_it_waits(ws, world):
    mine = runner("mine")
    version = publish(ws, "w", [echo()], [], inputs=schema(["v"], v=STR), user=world["designer"])
    set_runner("w", mine)
    run = launch(ws, version, world["designer"], {"v": "a"})
    from runs import engine

    engine.advance(run.pk)
    c = client_for(world["designer"])
    assert c.get(f"{WS}/workflow-runs/{run.pk}/").json()["runner"] == {"name": "mine", "inherited": False}
    Runner.objects.filter(pk=mine.pk).update(paused=True)
    from runs import health

    items = {i["key"]: i for i in health.summary(ws)}
    assert items["pinned_runners"]["status"] == "warn" and "en pause" in items["pinned_runners"]["message"]
    Runner.objects.filter(pk=mine.pk).update(paused=False, last_contact_at=timezone.now())
    assert {i["key"]: i for i in health.summary(ws)}["pinned_runners"]["status"] == "ok"


def test_unit_refusal_and_resolution_rules(ws, world):
    r = runner("r", runtimes=["python"], tags=["linux"])
    from catalog.models import ActionVersion

    av = ActionVersion.objects.filter(action__slug="echo").first()
    assert runner_pin.refusal(r, "r", ws, av) is None
    assert runner_pin.refusal(None, "r", ws, av).code == "RUNNER_UNAVAILABLE"
    wf = Workflow.objects.create(workspace=ws, slug="x", name="X", runner=r, runner_force_children=True)
    assert runner_pin.resolve_for_start(wf)["force_children"] is True
    plain = Workflow.objects.create(workspace=ws, slug="y", name="Y")
    assert runner_pin.resolve_for_start(plain) is None
