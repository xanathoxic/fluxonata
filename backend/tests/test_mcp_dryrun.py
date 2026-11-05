"""ADR 0025 — l'assistant ESSAIE ce qu'il a écrit (essai à blanc) au lieu de deviner : exécuté par le worker, jamais par le process web."""

import json
from types import SimpleNamespace

import pytest

from ai import jobs
from ai.models import AiJob, AiPolicy
from audit.models import AuditEvent
from runs.models import Execution, WorkflowRun
from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.test_mcp_design_stress import big_graph
from tests.wf_helpers import STR, make_action, schema
from workflows.models import Workflow

URL = "/api/v1/workspaces/team-a/mcp/design/"


def call(c, tool, args=None, ok=True):
    res = c.post(
        URL,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}},
        format="json",
    ).json()["result"]
    if ok:
        assert not res["isError"], f"{tool}: {res['content'][0]['text'][:300]}"
    return res


def structured(res):
    return res["structuredContent"]


def run_worker_once() -> AiJob:
    """Ce que fait le worker (rôle ai) : prendre le travail en file et l'exécuter."""
    job = jobs.claim_next("test-worker")
    assert job is not None, "aucun essai en file"
    jobs.process(job)
    return AiJob.objects.get(pk=job.pk)


def script(nid, code, bindings=None, outputs=()):
    return {
        "id": nid,
        "type": "script",
        "name": nid,
        "language": "python",
        "script": code,
        "bindings": bindings or {},
        "outputs": [{"name": n, "type": t} for n, t in outputs],
    }


GRAPH = {
    "inputs": schema(["pays", "montant"], pays=STR, montant={"type": "number"}),
    "nodes": [
        script(
            "taux",
            "taux = {'fr': 0.2, 'de': 0.19}.get(pays, 0.0)\n",
            {"pays": "{{ inputs.pays }}"},
            [("taux", "number")],
        ),
        script(
            "ttc",
            "total = round(montant * (1 + taux), 2)\n",
            {"montant": "{{ inputs.montant }}", "taux": "{{ steps.taux.output.taux }}"},
            [("total", "number")],
        ),
    ],
    "edges": [{"from": "taux", "to": "ttc"}],
    "outputs": {"total": "{{ steps.ttc.output.total }}"},
}


@pytest.fixture
def assistant(world):
    ws, user = world["ws_a"], world["designer"]
    AiPolicy.objects.update_or_create(workspace=ws, defaults={"mode": "local_strict", "dry_run": True})
    user._token = SimpleNamespace(scopes=["view", "edit"], workspace_id=ws.id, workspace=ws, environments=[])
    Workflow.objects.create(workspace=ws, slug="tva", name="TVA", draft_graph=GRAPH)
    yield client_for(user)
    del user._token


def test_starting_only_queues_a_job_the_web_process_executes_nothing(assistant, world):
    out = structured(
        call(
            assistant,
            "start_dry_run",
            {"slug": "tva", "samples": [{"label": "France", "inputs": {"pays": "fr", "montant": 100}}]},
        )
    )
    assert out["status"] == "queued" and out["job_id"]
    job = AiJob.objects.get(pk=out["job_id"])
    assert job.kind == "dryrun" and job.status == "queued" and job.result is None  # rien n'a tourné dans le process web
    assert (WorkflowRun.objects.count(), Execution.objects.count()) == (0, 0)
    pending = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))
    assert pending["status"] == "queued" and "rappelez" in pending["note"].lower()


def test_the_worker_runs_the_real_scripts_and_the_assistant_reads_actual_values(assistant, world):
    out = structured(
        call(
            assistant,
            "start_dry_run",
            {
                "slug": "tva",
                "samples": [
                    {
                        "label": "France",
                        "inputs": {"pays": "fr", "montant": 100},
                        "expected": [{"step": "ttc", "field": "total", "value": 120.0}],
                    },
                    {"label": "Allemagne", "inputs": {"pays": "de", "montant": 100}},
                ],
            },
        )
    )
    done = run_worker_once()
    assert done.status == "succeeded" and "graph" not in done.payload  # le graphe n'est pas conservé
    res = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]
    assert res["ran"] is True and res["problems"] == [] and res["coverage"]["executed"] == res["coverage"]["total"] == 2
    france = next(s for s in res["scenarios"] if s["label"] == "France")
    values = {st["id"]: st["output"] for st in france["steps"]}
    assert (
        float(values["taux"]["taux"]) == 0.2 and float(values["ttc"]["total"]) == 120.0
    )  # les VRAIES valeurs calculées, pas une supposition
    assert all(st["state"] == "ok" and "bac à sable" in st["detail"] for st in france["steps"])


def test_a_script_bug_is_reported_to_the_assistant_with_the_line(assistant, world):
    wf = Workflow.objects.get(slug="tva")
    bug = script(
        "ttc",
        "total = round(montant * (1 + taux) / (taux - 0.2), 2)\n",
        GRAPH["nodes"][1]["bindings"],
        [("total", "number")],
    )
    wf.draft_graph = {**GRAPH, "nodes": [GRAPH["nodes"][0], bug]}
    wf.save()
    out = structured(
        call(
            assistant,
            "start_dry_run",
            {"slug": "tva", "samples": [{"label": "France", "inputs": {"pays": "fr", "montant": 100}}]},
        )
    )
    run_worker_once()
    res = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]
    assert res["problems"], "la division par zéro doit être signalée"
    text = json.dumps(res, ensure_ascii=False).lower()
    assert "division" in text and "ttc" in text and "ligne" in text  # quelle étape, quelle erreur, quelle ligne


def test_a_wrong_expectation_is_reported(assistant, world):
    out = structured(
        call(
            assistant,
            "start_dry_run",
            {
                "slug": "tva",
                "samples": [
                    {
                        "label": "attendu faux",
                        "inputs": {"pays": "de", "montant": 100},
                        "expected": [{"step": "ttc", "field": "total", "value": 120.0}],
                    }
                ],
            },
        )
    )
    run_worker_once()
    res = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]
    assert res["problems"] and "119" in json.dumps(
        res["problems"], ensure_ascii=False
    )  # attendu 120 mais l'essai a obtenu 119 (TVA allemande)


def test_samples_are_validated_with_precise_errors(assistant, world):
    bad = call(
        assistant, "start_dry_run", {"slug": "tva", "samples": [{"label": "x", "inputs": {"pays": 3}}]}, ok=False
    )
    assert (
        bad["isError"] and "samples.0" in bad["content"][0]["text"] and "schéma d'entrées" in bad["content"][0]["text"]
    )
    bad = call(
        assistant,
        "start_dry_run",
        {
            "slug": "tva",
            "samples": [
                {
                    "label": "x",
                    "inputs": {"pays": "fr", "montant": 1},
                    "expected": [{"step": "inconnue", "field": "a", "value": 1}],
                }
            ],
        },
        ok=False,
    )
    assert bad["isError"] and "expected" in bad["content"][0]["text"]
    bad = call(
        assistant,
        "start_dry_run",
        {
            "slug": "tva",
            "samples": [{"label": "x", "inputs": {"pays": "fr", "montant": 1}, "mocks": {"nulle part": {"a": 1}}}],
        },
        ok=False,
    )
    assert bad["isError"] and "mocks" in bad["content"][0]["text"]
    assert AiJob.objects.filter(kind="dryrun").count() == 0


def test_without_samples_a_typical_and_a_limit_case_are_generated(assistant, world):
    out = structured(call(assistant, "start_dry_run", {"slug": "tva"}))
    assert "automatiques" in str(out["samples"])
    run_worker_once()
    res = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]
    assert {s["label"] for s in res["scenarios"]} >= {"typique"}


def test_a_large_workflow_is_tried_to_the_end_not_cut_at_80_steps(world, assistant):
    make_action(world["ws_a"], "echo")
    Workflow.objects.create(workspace=world["ws_a"], slug="gros", name="Gros", draft_graph=big_graph(21))
    out = structured(
        call(assistant, "start_dry_run", {"slug": "gros", "samples": [{"label": "pair", "inputs": {"n": 4, "t": "x"}}]})
    )
    run_worker_once()
    res = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]
    assert (
        res["scenarios"][0]["outcome"]["status"] == "succeeded"
    )  # « stopped » (plafond de 80 étapes) avant ce changement
    assert len(json.dumps(res)) < 60_000  # compact : les étapes non empruntées sont comptées, pas listées


def test_mocks_impose_the_outputs_of_simulated_steps(world, assistant):
    make_action(world["ws_a"], "add")
    g = {
        "inputs": schema(),
        "nodes": [
            {
                "id": "calc",
                "type": "action",
                "name": "Somme",
                "action": "add",
                "version": "1.0.0",
                "inputs": {"a": 1, "b": 2},
            },
            script("lire", "double = s * 2\n", {"s": "{{ steps.calc.output.sum }}"}, [("double", "integer")]),
        ],
        "edges": [{"from": "calc", "to": "lire"}],
        "outputs": {},
    }
    Workflow.objects.create(workspace=world["ws_a"], slug="mock", name="Mock", draft_graph=g)
    out = structured(
        call(
            assistant,
            "start_dry_run",
            {"slug": "mock", "samples": [{"label": "somme 21", "inputs": {}, "mocks": {"calc": {"sum": 21}}}]},
        )
    )
    run_worker_once()
    res = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]
    lire = next(s for s in res["scenarios"][0]["steps"] if s["id"] == "lire")
    assert (
        int(lire["output"]["double"]) == 42
        and next(s for s in res["scenarios"][0]["steps"] if s["id"] == "calc")["state"] == "simulated"
    )


def test_guards_rights_policy_quota_ownership(assistant, world):
    ws = world["ws_a"]
    # lecteur : ne lance rien
    assert (
        "modifier"
        in call(client_for(world["viewer"]), "start_dry_run", {"slug": "tva"}, ok=False)["content"][0]["text"]
    )
    # essai désactivé par la politique de l'espace
    AiPolicy.objects.filter(workspace=ws).update(dry_run=False)
    assert "désactivé" in call(assistant, "start_dry_run", {"slug": "tva"}, ok=False)["content"][0]["text"]
    AiPolicy.objects.filter(workspace=ws).update(dry_run=True)
    # rien à exécuter : pas de job
    Workflow.objects.create(
        workspace=ws,
        slug="vide",
        name="Vide",
        draft_graph={"inputs": schema(), "nodes": [{"id": "j", "type": "join", "name": "j"}], "edges": []},
    )
    assert structured(call(assistant, "start_dry_run", {"slug": "vide"}))["applicable"] is False
    # au plus 2 essais en cours par personne
    first = structured(call(assistant, "start_dry_run", {"slug": "tva"}))
    call(assistant, "start_dry_run", {"slug": "tva"})
    assert "déjà en cours" in call(assistant, "start_dry_run", {"slug": "tva"}, ok=False)["content"][0]["text"]
    # l'essai d'une autre personne n'existe pas
    other = make_user("autre", ws, Role.DESIGNER)
    other._token = SimpleNamespace(scopes=["view", "edit"], workspace_id=ws.id, workspace=ws, environments=[])
    assert (
        call(client_for(other), "get_dry_run", {"job_id": first["job_id"]}, ok=False)["content"][0]["text"]
        == "Introuvable."
    )
    assert (
        AuditEvent.objects.filter(
            action="mcp.design.write", data__tool="start_dry_run", data__external_assistant=True
        ).count()
        == 2
    )


def test_start_counts_in_the_hourly_budget_and_get_does_not(assistant, world, settings):
    settings.ORCH_MCP_DESIGN_WRITES_PER_HOUR = 2
    first = structured(call(assistant, "start_dry_run", {"slug": "tva"}))
    call(assistant, "start_dry_run", {"slug": "tva"})
    for _ in range(5):
        call(assistant, "get_dry_run", {"job_id": first["job_id"]})  # lire n'est pas écrire
    assert "Limite" in call(assistant, "start_dry_run", {"slug": "tva"}, ok=False)["content"][0]["text"]
