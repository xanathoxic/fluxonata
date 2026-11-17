"""Suppression d'une action ou d'un workflow AVEC son historique (`with_history`) ou ses déclencheurs (`with_triggers`), sur demande expresse seulement :
rien n'est emporté par défaut, une exécution en cours bloque toujours, les fichiers d'artefacts partent du stockage, les droits ne changent pas."""

import pytest

from audit.models import AuditEvent
from catalog.models import Action
from runs.artifacts import get_store, store_artifacts
from runs.models import Artifact, Execution, StepRun, WorkflowRun
from tenancy.models import Environment
from tests.conftest import client_for
from tests.wf_helpers import drain, launch, node, publish, schema
from triggers.models import PollTrigger, PublicForm, Schedule, Webhook
from workflows.models import Workflow

WS = "/api/v1/workspaces/team-a"


@pytest.fixture
def c(world):
    return client_for(world["designer"])


@pytest.fixture(autouse=True)
def store(settings, tmp_path):
    settings.ORCH_ARTIFACTS_DIR = tmp_path / "artefacts"
    settings.ORCH_ARTIFACTS_STORE = "fs"


def echo(i="e"):
    return node(i, action="echo", inputs={"value": "x"})


def executed(ws, world, slug="w", nodes=None):
    version = publish(ws, slug, nodes or [echo()], [], user=world["designer"])
    run = drain(launch(ws, version, world["designer"]))
    assert run.status == "succeeded"
    return run


def add_triggers(ws, slug="w"):
    wf = Workflow.objects.get(workspace=ws, slug=slug)
    env = Environment.objects.get(workspace=ws, kind="test")
    common = {"workspace": ws, "workflow": wf, "environment": env}
    Schedule.objects.create(**common, name="matin", cron="0 7 * * *", inputs={})
    Webhook.objects.create(**common, name="gh", provider="github")
    PublicForm.objects.create(**common, name="pub", title="Demande")
    PollTrigger.objects.create(**common, name="tickets", request={"connection": "svc", "path": "/x"}, id_path="id")


# --- workflow : historique ----------------------------------------------------------------------------------------------------------------------
def test_a_workflow_with_history_is_kept_by_default_and_deleted_with_it_on_request(ws, world, c):
    run = executed(ws, world)
    execution = Execution.objects.get(step_run__run=run)
    key = store_artifacts(execution, [("a.txt", "text/plain", b"contenu")])[0].storage_key
    assert get_store().get(key) == b"contenu"
    refused = c.delete(f"{WS}/workflows/w/delete/")
    assert refused.status_code == 400 and "déjà été exécuté" in refused.content.decode()
    assert Workflow.objects.filter(slug="w").exists() and WorkflowRun.objects.exists()
    assert c.delete(f"{WS}/workflows/w/delete/?with_history=1").status_code == 204
    assert not Workflow.objects.filter(slug="w").exists()
    assert not WorkflowRun.objects.exists() and not StepRun.objects.exists() and not Execution.objects.exists()
    assert not Artifact.objects.exists()
    with pytest.raises(FileNotFoundError):
        get_store().get(key)  # le fichier est parti du stockage, pas seulement la ligne de la base
    event = AuditEvent.objects.filter(action="workflow.delete").latest("id")
    assert event.data["history_deleted"] == 1


def test_sub_runs_started_by_the_deleted_workflow_go_with_it_but_the_child_workflow_stays(ws, world, c):
    publish(ws, "enfant", [echo()], [], inputs=schema(), user=world["designer"])
    executed(ws, world, "parent", [node("c", "subworkflow", workflow="enfant", inputs={})])
    own_child_run = drain(launch(ws, Workflow.objects.get(slug="enfant").latest_version(), world["designer"]))
    assert WorkflowRun.objects.count() == 3  # parent, sous-exécution de l'enfant, exécution directe de l'enfant
    assert c.delete(f"{WS}/workflows/parent/delete/?with_history=1").status_code == 204
    assert Workflow.objects.filter(slug="enfant").exists()
    assert list(WorkflowRun.objects.values_list("pk", flat=True)) == [
        own_child_run.pk
    ]  # l'exécution directe de l'enfant n'est pas touchée


def test_a_running_execution_blocks_the_deletion_even_with_history(ws, world, c):
    version = publish(ws, "w", [node("p", "wait", seconds=3600)], [], user=world["designer"])
    run = launch(ws, version, world["designer"])
    assert run.status == "running"
    r = c.delete(f"{WS}/workflows/w/delete/?with_history=1")
    assert r.status_code == 400 and "en cours" in r.content.decode()
    assert Workflow.objects.filter(slug="w").exists() and WorkflowRun.objects.filter(pk=run.pk).exists()


def test_the_flag_never_bypasses_the_usage_check_or_the_rights(ws, world, c):
    executed(ws, world, "enfant")
    publish(ws, "parent", [node("c", "subworkflow", workflow="enfant", inputs={})], [], user=world["designer"])
    r = c.delete(f"{WS}/workflows/enfant/delete/?with_history=1&with_triggers=1")
    assert (
        r.status_code == 409 and Workflow.objects.filter(slug="enfant").exists()
    )  # un autre workflow l'appelle : jamais supprimé
    assert client_for(world["viewer"]).delete(f"{WS}/workflows/parent/delete/?with_history=1").status_code == 403
    assert client_for(world["outsider"]).delete(f"{WS}/workflows/parent/delete/?with_history=1").status_code in (
        403,
        404,
    )
    assert Workflow.objects.filter(slug="parent").exists()


# --- workflow : déclencheurs --------------------------------------------------------------------------------------------------------------------
def test_triggers_protect_the_workflow_until_their_removal_is_confirmed(ws, world, c):
    Workflow.objects.create(
        workspace=ws,
        slug="w",
        name="W",
        draft_graph={"inputs": schema(), "nodes": [echo()], "edges": [], "outputs": {}},
    )
    add_triggers(ws)
    refused = c.delete(f"{WS}/workflows/w/delete/")
    assert refused.status_code == 400 and "formulaire public" in refused.content.decode()
    assert (
        Schedule.objects.exists()
        and Webhook.objects.exists()
        and PublicForm.objects.exists()
        and PollTrigger.objects.exists()
    )
    assert c.delete(f"{WS}/workflows/w/delete/?with_triggers=1").status_code == 204
    assert not Workflow.objects.filter(slug="w").exists()
    assert not (
        Schedule.objects.exists()
        or Webhook.objects.exists()
        or PublicForm.objects.exists()
        or PollTrigger.objects.exists()
    )
    event = AuditEvent.objects.filter(action="workflow.delete").latest("id")
    assert (
        event.data["triggers_deleted"] == {"schedules": 1, "webhooks": 1, "forms": 1, "polls": 1}
        and event.data["history_deleted"] == 0
    )


def test_history_and_triggers_are_two_separate_confirmations(ws, world, c):
    executed(ws, world)
    add_triggers(ws)
    assert (
        c.delete(f"{WS}/workflows/w/delete/?with_history=1").status_code == 400
    )  # les déclencheurs restent à confirmer
    assert c.delete(f"{WS}/workflows/w/delete/?with_triggers=1").status_code == 400  # l'historique reste à confirmer
    assert Workflow.objects.filter(slug="w").exists() and WorkflowRun.objects.exists()
    assert c.delete(f"{WS}/workflows/w/delete/?with_history=1&with_triggers=1").status_code == 204
    assert (
        not Workflow.objects.filter(slug="w").exists()
        and not WorkflowRun.objects.exists()
        and not Schedule.objects.exists()
    )


def test_the_usage_view_reports_what_would_go_with_the_workflow_and_the_action(ws, world, c):
    run = executed(ws, world)
    add_triggers(ws)
    facts = c.get(f"{WS}/workflows/w/usage/").json()
    assert facts["executions"] == 1 and facts["running"] == 0
    assert facts["triggers"] == {"schedules": 1, "webhooks": 1, "forms": 1, "polls": 1}
    assert facts["total"] == 0
    action = c.get(f"{WS}/actions/echo/usage/").json()
    assert action["executions"] == Execution.objects.filter(action_version__action__slug="echo").count() == 1
    assert action["total"] == 1 and run  # utilisé par le workflow « w » (version publiée)


# --- action : historique ------------------------------------------------------------------------------------------------------------------------
def direct_execution(ws, world, c):
    from catalog.models import ActionVersion
    from runs import services

    action = Action.objects.create(workspace=ws, slug="orpheline", name="Orpheline")
    version = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        source="def run(i): return {}",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        limits={"timeout_seconds": 20, "memory_mb": 64},
    )
    ex = services.enqueue(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=version,
        user=world["designer"],
        inputs={},
    )
    return action, ex


def test_an_executed_action_is_deleted_with_its_history_only_on_request(ws, world, c):
    action, ex = direct_execution(ws, world, c)
    Execution.objects.filter(pk=ex.pk).update(status="succeeded")
    key = store_artifacts(ex, [("r.txt", "text/plain", b"x")])[0].storage_key
    refused = c.delete(f"{WS}/actions/orpheline/delete/")
    assert refused.status_code == 400 and "déjà été exécutée" in refused.content.decode()
    assert c.delete(f"{WS}/actions/orpheline/delete/?with_history=1").status_code == 204
    assert not Action.objects.filter(slug="orpheline").exists() and not Execution.objects.filter(pk=ex.pk).exists()
    with pytest.raises(FileNotFoundError):
        get_store().get(key)
    assert AuditEvent.objects.filter(action="action.delete").latest("id").data["history_deleted"] == 1


def test_a_running_execution_blocks_the_action_deletion_even_with_history(ws, world, c):
    action, ex = direct_execution(ws, world, c)  # en attente : pas terminée
    r = c.delete(f"{WS}/actions/orpheline/delete/?with_history=1")
    assert r.status_code == 400 and "en cours" in r.content.decode()
    assert Action.objects.filter(slug="orpheline").exists() and Execution.objects.filter(pk=ex.pk).exists()


def test_an_action_used_by_a_workflow_is_never_deleted_even_with_history(ws, world, c):
    executed(ws, world)
    r = c.delete(f"{WS}/actions/echo/delete/?with_history=1")
    assert r.status_code == 409 and Action.objects.filter(slug="echo").exists() and Execution.objects.exists()
