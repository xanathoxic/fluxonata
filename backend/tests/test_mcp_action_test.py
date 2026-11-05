"""ADR 0025 (amendement) — l'assistant TESTE le code d'une action qu'il propose, dans le bac à sable, sans que l'action devienne exécutable ailleurs."""

import json
from types import SimpleNamespace

import pytest

from ai import jobs, service
from ai.models import AiJob, AiPolicy
from audit.models import AuditEvent
from catalog.models import Action
from runs import proposed, services
from runs.models import Execution, WorkflowRun
from runs.runners import SubprocessRunner
from tenancy.models import Environment
from tests.conftest import client_for

URL = "/api/v1/workspaces/team-a/mcp/design/"

ACTION = {
    "slug": "compter-voyelles",
    "name": "Compter les voyelles",
    "description": "Compte le nombre de voyelles d'un texte fourni en entrée.",
    "version": {
        "source": "def run(inputs):\n    return {'n': sum(c in 'aeiouy' for c in inputs['texte'].lower())}",
        "input_schema": {
            "type": "object",
            "required": ["texte"],
            "properties": {"texte": {"type": "string", "title": "Texte", "description": "Le texte à analyser."}},
        },
        "output_schema": {
            "type": "object",
            "required": ["n"],
            "properties": {"n": {"type": "integer", "title": "Nombre", "description": "Nombre de voyelles trouvées."}},
        },
        "secrets_required": [{"slot": "api", "env": "API_TOKEN"}],
        "network_policy": "test-service",
    },
}


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


def worker_once() -> AiJob:
    job = jobs.claim_next("w")
    assert job is not None
    jobs.process(job)
    return AiJob.objects.get(pk=job.pk)


class Spy(SubprocessRunner):
    """Un runner qui enregistre ce qu'on lui donne : la version (réseau, limites) et l'environnement (secrets)."""

    seen: list = []

    def run(self, version, inputs, timeout, cancel=None, env=None):
        Spy.seen.append(
            {"network": version.network_policy, "limits": dict(version.limits), "env": env, "timeout": timeout}
        )
        return super().run(version, inputs, timeout, cancel, env)


@pytest.fixture
def assistant(world, monkeypatch, settings):
    ws, user = world["ws_a"], world["designer"]
    AiPolicy.objects.update_or_create(workspace=ws, defaults={"mode": "local_strict", "dry_run": True})
    settings.ORCH_MCP_ACTION_TEST_UNISOLATED = True  # les tests n'ont pas de conteneur : on autorise le runner local, comme le fait `conftest` pour l'essai à blanc
    Spy.seen = []
    monkeypatch.setattr(service, "RUNNER_FACTORY", Spy)
    user._token = SimpleNamespace(scopes=["view", "edit"], workspace_id=ws.id, workspace=ws, environments=[])
    c = client_for(user)
    call(c, "propose_action", ACTION)
    yield c
    del user._token


def test_the_assistant_tests_its_proposed_code_and_reads_real_results(assistant):
    out = structured(
        call(
            assistant,
            "start_action_test",
            {
                "slug": "compter-voyelles",
                "cases": [
                    {"label": "bonjour", "inputs": {"texte": "Bonjour"}, "expected": {"n": 3}},
                    {"label": "vide", "inputs": {"texte": ""}, "expected": {"n": 0}},
                ],
            },
        )
    )
    assert (
        out["status"] == "queued" and AiJob.objects.get(pk=out["job_id"]).result is None
    )  # rien n'a tourné dans le process web
    worker_once()
    res = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]
    assert [c["ok"] for c in res["cases"]] == [True, True] and json.loads(res["cases"][0]["output"]) == {"n": 3}
    assert "reste « proposée »" in res["legend"]


def test_bugs_contract_violations_and_wrong_expectations_are_reported(assistant, world):
    from catalog.models import ActionVersion

    action = Action.objects.get(slug="compter-voyelles")
    v = action.latest_version()
    ActionVersion.objects.create(
        action=action,
        version="1.1.0",
        source="def run(inputs):\n    if inputs['texte'] == 'boom':\n        raise ValueError('cas non géré')\n    return {'n': 'beaucoup'}",
        input_schema=v.input_schema,
        output_schema=v.output_schema,
    )
    out = structured(
        call(
            assistant,
            "start_action_test",
            {
                "slug": "compter-voyelles",
                "cases": [
                    {"label": "plante", "inputs": {"texte": "boom"}},
                    {"label": "mauvais type", "inputs": {"texte": "abc"}},
                    {"label": "attendu faux", "inputs": {"texte": "abc"}, "expected": {"n": 1}},
                ],
            },
        )
    )
    worker_once()
    cases = structured(call(assistant, "get_dry_run", {"job_id": out["job_id"]}))["result"]["cases"]
    assert not any(c["ok"] for c in cases)
    assert "cas non géré" in json.dumps(cases[0]["problems"], ensure_ascii=False)
    assert cases[1]["problems"][0]["code"] == "OUTPUT_INVALID"
    assert any(p["code"] == "EXPECTED" for p in cases[2]["problems"])


def test_the_code_runs_isolated_without_network_secrets_or_connections(assistant):
    """Le code est celui d'une proposition NON relue : réseau coupé quoi qu'ait demandé la version, aucune variable d'environnement, limites plafonnées."""
    structured(
        call(
            assistant,
            "start_action_test",
            {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {"texte": "a"}}]},
        )
    )
    worker_once()
    [seen] = Spy.seen
    assert (
        Action.objects.get(slug="compter-voyelles").latest_version().network_policy == "test-service"
    )  # ce que la version demande
    assert seen["network"] == "none" and seen["env"] is None  # ce que le bac à sable accorde
    assert seen["limits"]["timeout_seconds"] <= 20 and seen["limits"]["memory_mb"] <= 256


def test_generous_limits_asked_by_the_proposal_are_capped(assistant):
    from catalog.models import ActionVersion

    action = Action.objects.get(slug="compter-voyelles")
    v = action.latest_version()
    ActionVersion.objects.create(
        action=action,
        version="1.1.0",
        source=v.source,
        input_schema=v.input_schema,
        output_schema=v.output_schema,
        limits={"timeout_seconds": 3600, "memory_mb": 2048},
    )
    structured(
        call(
            assistant,
            "start_action_test",
            {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {"texte": "a"}}]},
        )
    )
    worker_once()
    [seen] = Spy.seen
    assert (
        seen["limits"] == {"timeout_seconds": 20, "memory_mb": 256} and seen["timeout"] == 20
    )  # 3600 s et 2 Go demandés, 20 s et 256 Mo accordés


def test_the_test_does_not_make_the_proposal_usable_anywhere_else(assistant, world):
    structured(
        call(
            assistant,
            "start_action_test",
            {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {"texte": "a"}}]},
        )
    )
    worker_once()
    action = Action.objects.get(slug="compter-voyelles")
    assert action.status == "proposed"  # toujours à relire et à activer par une personne
    assert (Execution.objects.count(), WorkflowRun.objects.count()) == (0, 0)  # aucune exécution plate-forme
    with pytest.raises(proposed.ProposedResource):  # le barrage d'exécution reste fermé
        services.enqueue(
            workspace=world["ws_a"],
            environment=Environment.objects.get(workspace=world["ws_a"], kind="test"),
            action_version=action.latest_version(),
            user=world["designer"],
            inputs={"texte": "a"},
        )


def test_an_unisolated_runner_is_refused_unless_explicitly_allowed(assistant, settings):
    settings.ORCH_MCP_ACTION_TEST_UNISOLATED = False
    res = call(
        assistant,
        "start_action_test",
        {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {"texte": "a"}}]},
        ok=False,
    )
    assert res["isError"] and "isolée" in res["content"][0]["text"]
    assert not AiJob.objects.filter(kind="actiontest").exists()


def test_inputs_are_validated_against_the_contract_before_anything_runs(assistant):
    res = call(
        assistant, "start_action_test", {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {}}]}, ok=False
    )
    assert res["isError"] and "cases.0" in res["content"][0]["text"] and "texte" in res["content"][0]["text"]
    assert (
        call(assistant, "start_action_test", {"slug": "absente", "cases": [{"label": "x", "inputs": {}}]}, ok=False)[
            "content"
        ][0]["text"]
        == "Introuvable."
    )
    assert not AiJob.objects.filter(kind="actiontest").exists()


def test_rights_policy_quota_ownership_and_audit(assistant, world, settings):
    ws = world["ws_a"]
    assert (
        "modifier"
        in call(
            client_for(world["viewer"]),
            "start_action_test",
            {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {"texte": "a"}}]},
            ok=False,
        )["content"][0]["text"]
    )
    AiPolicy.objects.filter(workspace=ws).update(dry_run=False)
    assert (
        "désactivés"
        in call(
            assistant,
            "start_action_test",
            {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {"texte": "a"}}]},
            ok=False,
        )["content"][0]["text"]
    )
    AiPolicy.objects.filter(workspace=ws).update(dry_run=True)
    first = structured(
        call(
            assistant,
            "start_action_test",
            {"slug": "compter-voyelles", "cases": [{"label": "x", "inputs": {"texte": "a"}}]},
        )
    )
    call(assistant, "start_dry_run", {"slug": "inconnu"}, ok=False)  # un autre appel ne casse rien
    call(
        assistant,
        "start_action_test",
        {"slug": "compter-voyelles", "cases": [{"label": "y", "inputs": {"texte": "b"}}]},
    )
    assert (
        "déjà en cours"
        in call(
            assistant,
            "start_action_test",
            {"slug": "compter-voyelles", "cases": [{"label": "z", "inputs": {"texte": "c"}}]},
            ok=False,
        )["content"][0]["text"]
    )
    ev = AuditEvent.objects.filter(action="mcp.design.write", data__tool="start_action_test")
    assert ev.count() == 2 and all(e.data["external_assistant"] and e.data["slug"] == "compter-voyelles" for e in ev)
    other = SimpleNamespace(scopes=["view", "edit"], workspace_id=ws.id, workspace=ws, environments=[])
    from tenancy.models import Role
    from tests.conftest import make_user

    stranger = make_user("autre", ws, Role.DESIGNER)
    stranger._token = other
    assert (
        call(client_for(stranger), "get_dry_run", {"job_id": first["job_id"]}, ok=False)["content"][0]["text"]
        == "Introuvable."
    )
