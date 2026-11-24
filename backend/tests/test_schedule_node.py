"""Nœud « Programmer » (ADR 0034) : un workflow programme le lancement d'un autre à une date et une heure ; l'orchestrateur le lance à l'heure dite."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from audit.models import AuditEvent
from runs import scheduled_launch, step_schedule
from runs.models import ScheduledLaunch, WorkflowRun
from tenancy.models import Membership, Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import STR, drain, launch, node, publish, schema
from workflows.validation import validate

BASE = "/api/v1/workspaces/team-a/scheduled-launches/"
TEXT = schema(["text"], text=STR)
SOON = (timezone.now() + timedelta(days=30)).strftime("%Y-%m-%dT08:30")  # une date fixe, dans l'année qui vient


def future(days=2, hour=8, minute=30) -> str:
    d = (timezone.now() + timedelta(days=days)).date()
    return f"{d:%Y-%m-%d}T{hour:02d}:{minute:02d}"


@pytest.fixture
def target(ws, world):
    return publish(
        ws,
        "rapport",
        [node("a", action="echo", inputs={"value": "{{ inputs.text }}"})],
        [],
        inputs=TEXT,
        user=world["designer"],
    )


def planner(ws, user, **fields):
    cfg = {
        "workflow": "rapport",
        "version": "latest",
        "when": "at",
        "at": future(),
        "inputs": {"text": "bonjour"},
    } | fields
    return publish(ws, "planificateur", [node("p", "schedule", **cfg)], [], user=user)


def go(ws, user, **fields):
    return drain(launch(ws, planner(ws, user, **fields), user))


def out(run):
    return run.steps.get(node_id="p").output


def err(run):
    return run.steps.get(node_id="p").error["code"]


def test_the_step_programs_a_launch_and_the_run_goes_on(ws, world, target):
    run = go(ws, world["designer"], at=SOON, timezone="Europe/Paris")
    assert run.status == "succeeded"
    o = out(run)
    assert o["workflow"] == "rapport" and o["immediate"] is False
    wanted = datetime.fromisoformat(SOON).replace(tzinfo=ZoneInfo("Europe/Paris"))
    assert datetime.fromisoformat(o["run_at"]) == wanted and o["run_at_local"].startswith(SOON)
    assert o["run_at"].endswith("+00:00")  # stocké et rendu en UTC, affiché dans le fuseau demandé
    row = ScheduledLaunch.objects.get(pk=o["id"])
    assert (
        row.status == "pending"
        and row.inputs == {"text": "bonjour"}
        and row.source_run_id == run.pk
        and row.environment.kind == "test"
    )
    assert row.requested_by == world["designer"]
    assert not WorkflowRun.objects.filter(
        workflow_version__workflow__slug="rapport"
    ).exists()  # rien n'est lancé maintenant
    assert AuditEvent.objects.filter(action="scheduled_launch.create", actor_label="dana").exists()
    assert run.events.filter(type="launch_scheduled").exists()


def test_the_launch_is_made_at_the_time_and_only_once(ws, world, target):
    run = go(ws, world["designer"], at=SOON)
    row = ScheduledLaunch.objects.get()
    assert (
        scheduled_launch.fire_due(now=row.run_at - timedelta(seconds=1)) == 0
        and WorkflowRun.objects.filter(workflow_version__workflow__slug="rapport").count() == 0
    )
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(seconds=2)) == 1
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(minutes=1)) == 0
    row.refresh_from_db()
    child = WorkflowRun.objects.get(workflow_version__workflow__slug="rapport")
    assert row.status == "fired" and row.fired_run_id == child.pk and row.note == ""
    assert (
        child.inputs == {"text": "bonjour"}
        and child.requested_by == world["designer"]
        and child.environment.kind == "test"
    )
    assert drain(child).status == "succeeded"
    assert run.status == "succeeded"  # indépendant du run qui l'a programmé
    assert AuditEvent.objects.filter(action="scheduled_launch.fired").count() == 1


def test_a_late_launch_is_made_anyway_and_says_so(ws, world, target):
    go(ws, world["designer"], at=SOON)
    row = ScheduledLaunch.objects.get()
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(hours=3)) == 1
    row.refresh_from_db()
    assert row.status == "fired" and "180 min de retard" in row.note


def test_a_delay_can_be_used_instead_of_a_date(ws, world, target):
    run = go(ws, world["designer"], when="in", delay_seconds=3600)
    row = ScheduledLaunch.objects.get(pk=out(run)["id"])
    assert timedelta(minutes=59) < row.run_at - timezone.now() <= timedelta(hours=1)


def test_the_date_can_come_from_an_input_and_may_carry_its_own_offset(ws, world, target):
    v = publish(
        ws,
        "dyn",
        [node("p", "schedule", workflow="rapport", at="{{ inputs.quand }}", inputs={"text": "x"})],
        [],
        inputs=schema(["quand"], quand=STR),
        user=world["designer"],
    )
    day = (timezone.now() + timedelta(days=60)).strftime("%Y-%m-%d")
    run = drain(launch(ws, v, world["designer"], inputs={"quand": f"{day}T10:00:00+02:00"}))
    assert out(run)["run_at"].startswith(f"{day}T08:00:00")
    bad = drain(launch(ws, v, world["designer"], inputs={"quand": "demain matin"}))
    assert bad.status == "failed" and err(bad) == "SCHEDULE_WHEN"


def test_a_date_in_the_past_fails_or_launches_right_away_on_request(ws, world, target):
    failed = go(ws, world["designer"], at="2020-01-01T08:00")
    assert failed.status == "failed" and err(failed) == "SCHEDULE_PAST" and not ScheduledLaunch.objects.exists()
    now_run = go(ws, world["designer"], at="2020-01-01T08:00", on_past="now")
    assert now_run.status == "succeeded" and out(now_run)["immediate"] is True
    assert scheduled_launch.fire_due() == 1  # prêt au prochain tour de l'orchestrateur


def test_the_horizon_is_bounded_to_a_year(ws, world, target):
    far = (timezone.now() + timedelta(days=400)).strftime("%Y-%m-%dT08:00")
    run = go(ws, world["designer"], at=far)
    assert run.status == "failed" and err(run) == "SCHEDULE_HORIZON"
    edge = go(ws, world["designer"], at=(timezone.now() + timedelta(days=365)).strftime("%Y-%m-%dT08:00"))
    assert edge.status == "succeeded"


def test_the_inputs_are_checked_when_designing_and_again_when_programming(ws, world, target):
    me = world["designer"]
    missing = {
        "inputs": schema(),
        "nodes": [node("p", "schedule", workflow="rapport", at=future(), inputs={})],
        "edges": [],
        "outputs": {},
    }
    assert any(i["code"] == "missing_param" for i in validate(missing, ws, "autre") if i["level"] == "error")
    # une valeur calculée n'est connue qu'à l'exécution : le serveur la revérifie au moment de programmer
    v = publish(
        ws,
        "calcule",
        [node("p", "schedule", workflow="rapport", at=future(), inputs={"text": "{{ inputs.v }}"})],
        [],
        inputs=schema(["v"], v={}),
        user=me,
    )
    bad = drain(launch(ws, v, me, inputs={"v": 3}))
    assert bad.status == "failed" and err(bad) == "SCHEDULE_INPUTS" and not ScheduledLaunch.objects.exists()
    assert drain(launch(ws, v, me, inputs={"v": "ok"})).status == "succeeded"


def test_a_workflow_cannot_program_itself(ws, world, target):
    errors = validate(
        {
            "inputs": schema(),
            "nodes": [node("p", "schedule", workflow="rapport", at=future(), inputs={"text": "x"})],
            "edges": [],
            "outputs": {},
        },
        ws,
        "rapport",
    )
    assert any(i["code"] == "recursion" for i in errors if i["level"] == "error")


def test_replaying_the_step_never_programs_twice(ws, world, target):
    run = go(ws, world["designer"], at=SOON)
    cfg = node("p", "schedule", workflow="rapport", when="at", at=SOON, inputs={"text": "bonjour"})
    again = step_schedule.apply(run, cfg, {"inputs": {}, "steps": {}})
    assert again["id"] == out(run)["id"] and ScheduledLaunch.objects.count() == 1


def test_the_number_of_pending_launches_is_bounded(ws, world, target, monkeypatch):
    monkeypatch.setattr("workflows.schedule_spec.MAX_PENDING", 1)
    assert go(ws, world["designer"]).status == "succeeded"
    other = publish(
        ws,
        "autre",
        [node("p", "schedule", workflow="rapport", at=future(3), inputs={"text": "x"})],
        [],
        user=world["designer"],
    )
    run = drain(launch(ws, other, world["designer"]))
    assert run.status == "failed" and err(run) == "SCHEDULE_QUOTA"


def test_the_governance_of_the_space_is_checked_before_and_after(ws, world, target):
    from tenancy.models import Environment, Promotion

    prod = Environment.objects.get(workspace=ws, kind="prod")
    me = world["designer"]
    version = planner(ws, me)  # le planificateur est promu en production, pas le workflow cible
    Promotion.objects.create(
        workspace=ws, workflow_version=version, environment=prod, status="approved", requested_by=me
    )
    run = drain(launch(ws, version, me, env="prod"))
    assert (
        run.status == "failed"
        and err(run) == "SCHEDULE_REFUSED"
        and "promue" in run.steps.get(node_id="p").error["message"]
    )
    assert not ScheduledLaunch.objects.exists()
    # promue : la programmation est acceptée, et la politique est revérifiée à l'heure dite
    Promotion.objects.create(
        workspace=ws, workflow_version=target, environment=prod, status="approved", requested_by=me
    )
    ok = drain(launch(ws, version, me, env="prod"))
    assert ok.status == "succeeded"
    row = ScheduledLaunch.objects.get()
    Promotion.objects.filter(workflow_version=target).delete()  # la promotion a été retirée entre-temps
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(seconds=1)) == 0
    row.refresh_from_db()
    assert row.status == "refused" and "promue" in row.note


def test_the_right_to_run_is_checked_again_at_the_time(ws, world, target):
    go(ws, world["designer"], at=SOON)
    row = ScheduledLaunch.objects.get()
    Membership.objects.filter(user=world["designer"], workspace=ws).update(role=Role.VIEWER)
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(seconds=1)) == 0
    row.refresh_from_db()
    assert row.status == "refused" and "n'a plus le droit" in row.note and row.fired_run is None
    assert AuditEvent.objects.filter(action="scheduled_launch.refused").exists()


def test_an_archived_workspace_is_left_alone(ws, world, target):
    go(ws, world["designer"], at=SOON)
    row = ScheduledLaunch.objects.get()
    ws.archived_at = datetime.now(UTC)
    ws.save()
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(seconds=1)) == 0
    row.refresh_from_db()
    assert row.status == "pending"


def test_a_workflow_without_a_published_version_any_more_is_refused_not_retried(ws, world, target):
    go(ws, world["designer"], at=SOON)
    row = ScheduledLaunch.objects.get()
    target.workflow.versions.all().delete()
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(seconds=1)) == 0
    row.refresh_from_db()
    assert row.status == "refused" and "version publiée" in row.note
    assert scheduled_launch.fire_due(now=row.run_at + timedelta(minutes=5)) == 0  # pas de boucle


# ───────── API : voir et annuler ─────────


def test_list_and_cancel_pending_launches(ws, world, target):
    go(ws, world["designer"], at=SOON)
    c = client_for(world["designer"])
    body = c.get(BASE).json()
    row = body["results"][0]
    assert (
        body["count"] == 1
        and row["workflow"] == "rapport"
        and row["status"] == "pending"
        and row["source_workflow"] == "planificateur"
    )
    assert row["requested_by"] == "dana" and "inputs" not in row
    assert c.get(BASE + "?status=fired").json()["count"] == 0 and c.get(BASE + "?workflow=rapport").json()["count"] == 1
    assert c.delete(f"{BASE}{row['id']}/").status_code == 204
    assert ScheduledLaunch.objects.get().status == "cancelled"
    assert scheduled_launch.fire_due(now=timezone.now() + timedelta(days=4000)) == 0  # annulé : jamais lancé
    assert c.delete(f"{BASE}{row['id']}/").status_code == 400  # plus en attente
    assert AuditEvent.objects.filter(action="scheduled_launch.cancel", actor_label="dana").exists()


def test_rights_and_isolation_of_the_launch_api(ws, world, target):
    go(ws, world["designer"], at=SOON)
    pk = ScheduledLaunch.objects.get().pk
    viewer = client_for(world["viewer"])
    assert viewer.get(BASE).status_code == 200  # un lecteur voit ce qui est programmé…
    assert viewer.delete(f"{BASE}{pk}/").status_code == 403  # … mais ne l'annule pas
    outsider = client_for(world["outsider"])
    assert outsider.get(BASE).status_code == 404 and outsider.delete(f"{BASE}{pk}/").status_code == 404
    assert ScheduledLaunch.objects.get().status == "pending"
    assert client_for(make_user("anon-no-space")).get(BASE).status_code == 404


# ───────── validation de la configuration ─────────


@pytest.mark.parametrize(
    "fields,needle",
    [
        ({"when": "plus-tard"}, "Quand"),
        ({"at": ""}, "date et l'heure"),
        ({"at": "2030-01-15"}, "aussi l'heure"),
        ({"at": "15/01/2030 08:30"}, "illisible"),
        ({"timezone": "Mars/Olympus"}, "Fuseau"),
        ({"when": "in", "delay_seconds": 0}, "Délai"),
        ({"when": "in", "delay_seconds": "1h"}, "Délai"),
        ({"on_past": "peut-etre"}, "date est passée"),
        ({"workflow": "inconnu"}, "introuvable"),
    ],
)
def test_validation_rejects_bad_configuration(ws, world, target, fields, needle):
    cfg = {"workflow": "rapport", "when": "at", "at": future(), "inputs": {"text": "x"}} | fields
    graph = {"inputs": schema(), "nodes": [node("p", "schedule", **cfg)], "edges": [], "outputs": {}}
    messages = " ".join(i["message"] for i in validate(graph, ws, "autre") if i["level"] == "error")
    assert needle in messages, messages


def test_the_outputs_are_declared_for_the_following_steps(ws, world, target):
    graph = {
        "inputs": schema(),
        "nodes": [
            node("p", "schedule", workflow="rapport", at=future(), inputs={"text": "x"}),
            node("c", "cache", operation="write", key="derniere", value="{{ steps.p.output.run_at }}"),
        ],
        "edges": [{"from": "p", "to": "c"}],
        "outputs": {},
    }
    assert [i for i in validate(graph, ws, "autre") if i["level"] == "error"] == []
    graph["nodes"][1]["value"] = "{{ steps.p.output.inconnu }}"
    assert any(i["level"] == "error" for i in validate(graph, ws, "autre"))


def test_the_design_guide_documents_the_node_with_a_valid_example(ws, world, target):
    from workflows.mcp_design_guide import OTHER_NODES, guide

    example = {
        **OTHER_NODES["programmer (lancer un autre workflow à une date et une heure)"],
        "workflow": "rapport",
        "inputs": {"text": "x"},
    }
    graph = {
        "inputs": schema(["date_rappel"], date_rappel=STR),
        "nodes": [example],
        "edges": [],
        "outputs": {},
    }
    assert [i for i in validate(graph, ws, "autre") if i["level"] == "error"] == []
    text = str(guide("programmer"))
    assert "PROGRAMMER UN LANCEMENT" in text and "366" in text and "essai à blanc" in text


def test_a_dry_run_simulates_the_step_and_programs_nothing(ws, world, target):
    from ai import dryrun

    kind, message = dryrun._FIXED_STEPS["schedule"]
    assert callable(kind) and "aucun lancement" in message
    out = kind({"workflow": "rapport", "at": "2030-01-01T08:00"})
    assert out["workflow"] == "rapport" and out["immediate"] is False and not ScheduledLaunch.objects.exists()
