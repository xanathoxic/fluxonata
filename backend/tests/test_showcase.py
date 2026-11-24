import importlib.util
import json
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from ai.models import AiCall
from ai.providers import FakeProvider
from ai.runner import AiRunner
from formbuilder.models import Form
from runs import engine
from runs.models import Approval, WorkflowRun
from tests.conftest import client_for
from tests.wf_helpers import drain, launch
from triggers import lookups
from vault.models import Connection
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
    assert Workflow.objects.filter(workspace=ws).count() == 6
    assert Connection.objects.filter(workspace=ws, name="demo-api").count() == 1  # pas de doublon au 2e passage
    form = Form.objects.get(
        workspace=ws, slug="acces-groupe"
    )  # le formulaire d'entrée est un formulaire de la BIBLIOTHÈQUE
    assert form.versions.count() == 1  # publié une fois, pas à chaque passage
    wf = Workflow.objects.get(workspace=ws, slug="ajouter-membre-groupe")
    assert wf.draft_graph["input_form"] == {"ref": {"form": "acces-groupe", "version": None}}
    pinned = wf.latest_version().graph["input_form"]
    assert pinned["ref"] == {"form": "acces-groupe", "version": "1.0.0"} and pinned["sections"]  # figé, avec sa copie
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


@pytest.fixture
def demo_api(settings):
    """Le vrai scripts/demo-api.py, sur un port libre (adresse locale autorisée comme le ferait l'opérateur)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "demo-api.py"
    spec = importlib.util.spec_from_file_location("demo_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    srv = HTTPServer(("127.0.0.1", 0), module.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    from django.core.cache import cache

    cache.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def lookup(client, field, params=None):
    base = "/api/v1/workspaces/vitrine/workflows/ajouter-membre-groupe/form-lookup"
    r = client.post(f"{base}/{field}/", {"params": params or {}}, format="json")
    assert r.status_code == 202, r.content
    assert lookups.process_one("w1") is True
    return client.get(f"{base}/{field}/{r.data['id']}/").data


def test_the_access_form_example_reads_groups_then_members_and_the_workflow_runs(db, demo_api):
    call_command("seed_showcase", "--fake-ai", "--demo-api-url", demo_api)
    ws = Workflow.objects.get(slug="ajouter-membre-groupe").workspace
    conn = Connection.objects.get(workspace=ws, name="demo-api")
    assert (
        conn.config == {"base_url": demo_api} and conn.secret is not None
    )  # le jeton est au coffre, pas dans la configuration
    alice = User.objects.get(username="alice")
    c = client_for(alice)
    groups = lookup(c, "groupe")
    # clés d'un objet → options ; « archives » (vide) écarté par le filtre ; trié par libellé ; libellé calculé avec l'effectif
    assert [o["label"] for o in groups["options"]] == [
        "group:developpement (2)",
        "group:exploitation (3)",
        "group:support (1)",
    ]
    members = lookup(c, "membre", {"groupe": "group:exploitation"})
    assert [o["value"] for o in members["options"]] == ["alice@exemple.fr", "bob@exemple.fr", "claire@exemple.fr"]
    run = drain(
        launch(
            ws,
            Workflow.objects.get(slug="ajouter-membre-groupe").latest_version(),
            alice,
            {"groupe": "group:support", "membre": "farid@exemple.fr"},
        )
    )
    assert run.status == "succeeded", run.error
    assert (
        run.outputs["resume"]
        == "Demande : ajouter farid@exemple.fr au groupe group:support (simulation : rien n'est modifié)"
    )


def test_the_report_example_turns_one_model_into_word_pdf_web_zip_and_a_mail(vitrine):
    import io
    import zipfile

    from runs.models import RunFile
    from workflows.models import DocumentImage

    ws, _ = vitrine
    call_command("seed_showcase", "--fake-ai")  # deux fois : l'image et la connexion ne sont pas dupliquées
    assert DocumentImage.objects.filter(workspace=ws).count() == 1
    assert Connection.objects.filter(workspace=ws, name="courrier-demo", kind="smtp").count() == 1
    version = Workflow.objects.get(workspace=ws, slug="rapport-mensuel").latest_version()
    assert Workflow.objects.get(workspace=ws, slug="rapport-mensuel").in_catalog
    run = drain(
        launch(ws, version, User.objects.get(username="alice"), {"mois": "mars 2027", "destinataire": "a@exemple.org"}),
        max_rounds=200,
    )
    assert run.status == "succeeded", [(s.node_id, s.status, s.error) for s in run.steps.all()]
    files = {f.name: bytes(f.data) for f in RunFile.objects.filter(run=run)}
    assert sorted(files) == ["rapport-complet.zip", "rapport.docx", "rapport.html", "rapport.pdf"]
    page = files["rapport.html"].decode()
    assert "mars 2027" in page and "7 incidents" in page and "data:image/png;base64," in page and "<table>" in page
    assert "word/media/image1.png" in zipfile.ZipFile(io.BytesIO(files["rapport.docx"])).namelist()
    assert files["rapport.pdf"].startswith(b"%PDF-1.4")
    assert sorted(zipfile.ZipFile(io.BytesIO(files["rapport-complet.zip"])).namelist()) == [
        "rapport.docx",
        "rapport.html",
        "rapport.pdf",
    ]
    mail = run.steps.get(node_id="courriel").output
    assert mail["simulated"] is True and mail["sent"] is False  # mode test : rien n'est envoyé
