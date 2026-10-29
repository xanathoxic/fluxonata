import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from ai.models import AiCall
from ai.providers import FakeProvider
from ai.runner import AiRunner
from runs import engine
from runs.models import Approval, WorkflowRun
from tests.wf_helpers import drain, launch
from workflows.models import Workflow

User = get_user_model()


def model(gravite="haute"):
    """A scripted 'model' answering each skill from its own instructions (the prompt is data, never obeyed)."""

    def answer(system, user):
        if "Lis le message d'incident" in user:
            return json.dumps(
                {"resume": "Pannes de VM sur prod-db", "gravite": gravite, "vms": 3, "categorie": "reseau"}
            )
        if "communication d'une équipe" in user:
            return json.dumps({"sujet": "Incident en cours", "message": "Nous traitons l'incident."})
        if "ingénieur fiabilité" in user:
            return json.dumps(
                {"hypothese": "Saturation du commutateur", "confiance": 70, "actions": "Basculer le lien"}
            )
        raise AssertionError("prompt inattendu : " + user[:80])

    return answer


@pytest.fixture
def vitrine(db, monkeypatch):
    call_command("seed_showcase", "--fake-ai")
    provider = FakeProvider(script=model())
    monkeypatch.setattr(
        AiRunner,
        "__init__",
        lambda self, execution, p=None: setattr(self, "execution", execution) or setattr(self, "provider", provider),
    )
    ws = Workflow.objects.get(slug="gestion-incident").workspace
    return ws, provider


def start(ws, ips=None, message="Depuis 9h, 3 VM de prod-db ne répondent plus."):
    version = Workflow.objects.get(workspace=ws, slug="gestion-incident").latest_version()
    inputs = {"message": message, **({"ips": ips} if ips is not None else {})}
    return launch(
        ws, version, User.objects.get(username="alice"), inputs | {"audience": "clients"} if ips is None else inputs
    )


def run_until_decision(run):
    return drain(run, max_rounds=600, until=lambda r: Approval.objects.filter(status="pending").exists())


def steps(run):
    return {s.node_id: s.status for s in run.steps.all()}


def test_seed_is_idempotent_and_publishes_a_three_level_scenario(vitrine):
    ws, _ = vitrine
    call_command("seed_showcase", "--fake-ai")  # twice: nothing duplicated
    assert Workflow.objects.filter(workspace=ws).count() == 4
    main = Workflow.objects.get(workspace=ws, slug="gestion-incident").latest_version()
    pinned = {
        n["id"]: n.get("skill_version") or n.get("version")
        for n in main.graph["nodes"]
        if n["type"] in ("ai", "subworkflow")
    }
    assert pinned["qualifier"] == "1.1.0"  # the newest skill version is pinned at publication
    assert pinned["lot"] == "1.0.0" and pinned["cause"] == "1.0.0"


def test_incident_runs_end_to_end_through_three_levels_with_an_approval(vitrine):
    ws, provider = vitrine
    run = run_until_decision(start(ws))
    assert run.status == "running", (
        run.error,
        steps(run),
        [(s.node_id, s.error) for s in run.steps.filter(status="failed")],
    )
    approval = Approval.objects.get(status="pending")  # raised INSIDE the nested "lot" workflow
    assert (
        approval.step_run.run.workflow_version.workflow.slug == "traiter-lot-vms" and "2 machine(s)" in approval.title
    )
    engine.decide_approval(approval.pk, User.objects.get(username="claire"), True, "Go")
    run = drain(run, max_rounds=900)
    assert run.status == "succeeded", run.error
    assert run.outputs["gravite"] == "haute" and run.outputs["categorie"] == "reseau"
    assert run.outputs["cause"] == "Saturation du commutateur" and run.outputs["communication"] == "Incident en cours"
    assert run.outputs["machines"] == "5 machine(s) diagnostiquée(s), 2 en défaut : 10.0.0.11, 10.0.0.15"
    assert run.outputs["lignes_du_rapport"] == 6
    st = steps(run)
    assert (
        st["alerte"] == "succeeded" and st["ticket"] == "skipped" and st["secours"] == "skipped"
    )  # exclusive branches
    assert st["lot"] == "succeeded" and st["bilan"] == "succeeded" and st["notifier"] == "succeeded"
    # 1 incident + 1 lot + 5 diagnostics + 2 restarts = 9 runs, on 3 levels
    runs = WorkflowRun.objects.filter(workspace=ws)
    assert runs.count() == 9 and sorted({r.definition.get("depth", 0) for r in runs}) == [0, 1, 2]
    assert AiCall.objects.filter(workspace=ws, use_case="ai_step").count() == 3
    assert "Pannes" not in provider.calls[0][0]  # the instructions travel as the task, the message as data
    assert all(r.status == "succeeded" for r in runs)


def test_low_severity_creates_a_ticket_and_a_healthy_lot_needs_no_approval(vitrine, monkeypatch):
    ws, provider = vitrine
    provider.script = model("faible")
    run = drain(start(ws, ips="10.0.0.16, 10.0.0.17"), max_rounds=900)
    assert run.status == "succeeded", run.error
    st = steps(run)
    assert st["ticket"] == "succeeded" and st["alerte"] == "skipped"
    assert not Approval.objects.exists()
    assert run.outputs["machines"].startswith("2 machine(s) diagnostiquée(s), 0 en défaut")
    assert WorkflowRun.objects.filter(workspace=ws).count() == 4  # incident + lot + 2 diagnostics, no restart


def test_refused_approval_restarts_nothing_and_the_incident_still_completes(vitrine):
    ws, _ = vitrine
    run = run_until_decision(start(ws))
    engine.decide_approval(
        Approval.objects.get(status="pending").pk, User.objects.get(username="claire"), False, "Pas maintenant"
    )
    run = drain(run, max_rounds=900)
    lot = WorkflowRun.objects.get(workflow_version__workflow__slug="traiter-lot-vms")
    assert run.status == "succeeded", (run.error, lot.status, lot.error, steps(lot))
    assert WorkflowRun.objects.filter(workspace=ws).count() == 7  # 1 + 1 + 5 diagnostics, 0 restart
