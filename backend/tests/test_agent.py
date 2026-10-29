import json

import pytest

from ai.providers import FakeProvider
from ai.runner import AiRunner
from catalog.models import ActionVersion
from runs import engine
from runs.models import Approval
from tenancy.models import Role
from tests.conftest import make_user
from tests.test_ai import ai_ws  # noqa: F401  (fixture: AI policy on, catalogue)
from tests.wf_helpers import drain, launch, make_action, node, publish


def _script(decisions):
    """A model that answers with the next prepared decision; remembers the prompts it saw."""
    seen = []

    def run(system, user):
        seen.append(user)
        return json.dumps(decisions[min(len(seen) - 1, len(decisions) - 1)])

    run.seen = seen
    return run


@pytest.fixture
def setup(ai_ws, world, monkeypatch):  # noqa: F811
    ws, _ = ai_ws
    make_action(ws, "echo")
    holder = {}
    monkeypatch.setattr(
        AiRunner,
        "__init__",
        lambda self, execution, provider=None: (
            setattr(self, "execution", execution) or setattr(self, "provider", FakeProvider(script=holder["script"]))
        ),
    )
    return ws, holder


def _agent(ws, world, **kw):
    node_kw = {"goal": "Répète « salut » puis conclus.", "tools": ["echo"], "provider": "fake", **kw}
    v = publish(
        ws,
        "agent",
        [node("ag", "agent", **node_kw)],
        [],
        outputs={"reponse": "{{ steps.ag.output.answer }}"},
        user=world["designer"],
    )
    return v


def test_real_agent_calls_the_tool_then_answers_and_treats_results_as_data(setup, world):
    ws, holder = setup
    holder["script"] = _script(
        [
            {"action": "tool", "tool": "echo", "inputs": {"value": "salut"}, "reason": "demandé"},
            {"action": "final", "answer": "J'ai répété salut."},
        ]
    )
    run = drain(launch(ws, _agent(ws, world, mode="reel", approval="never"), world["designer"]), max_rounds=200)
    out = run.steps.get(node_id="ag").output
    assert (
        run.status == "succeeded"
        and out["answer"] == "J'ai répété salut."
        and out["iterations"] == 1
        and out["stopped"] == "done"
    )
    assert (
        out["steps"][0]["tool"] == "echo"
        and '"value": "salut"' in out["steps"][0]["observation"]
        and out["simulated"] is False
    )
    second_prompt = holder["script"].seen[1]
    assert "<historique>" in second_prompt and "salut" in second_prompt and "DONNÉES" in second_prompt
    assert run.outputs == {"reponse": "J'ai répété salut."}


def test_test_mode_simulates_tools_and_never_executes_them(setup, world):
    from runs.models import Execution

    ws, holder = setup
    holder["script"] = _script(
        [{"action": "tool", "tool": "echo", "inputs": {"value": "x"}}, {"action": "final", "answer": "fini"}]
    )
    run = drain(launch(ws, _agent(ws, world), world["designer"]), max_rounds=200)
    out = run.steps.get(node_id="ag").output
    assert out["simulated"] is True and "simulated" in out["steps"][0]["observation"]
    assert not Execution.objects.filter(step_run__run=run, action_version__action__slug="echo").exists()


def test_bad_tool_calls_are_fed_back_and_the_turn_limit_stops_the_agent(setup, world):
    ws, holder = setup
    holder["script"] = _script(
        [
            {"action": "tool", "tool": "inconnu", "inputs": {}},
            {"action": "tool", "tool": "echo", "inputs": {"pas_le_bon": 1}},
        ]
    )
    run = drain(
        launch(ws, _agent(ws, world, mode="reel", max_steps=2, approval="never"), world["designer"]), max_rounds=300
    )
    out = run.steps.get(node_id="ag").output
    assert (
        run.status == "succeeded"
        and out["stopped"] == "max_steps"
        and out["iterations"] == 2
        and not any(s["ok"] for s in out["steps"])
    )
    assert (
        "Outil inconnu" in holder["script"].seen[1] and len(holder["script"].seen) == 2
    )  # fed back once, then the limit stops it


def test_effectful_tools_wait_for_a_human_and_a_refusal_is_observed(setup, world):
    ws, holder = setup
    ActionVersion.objects.filter(action__workspace=ws, action__slug="echo").update(side_effects="external")
    holder["script"] = _script(
        [{"action": "tool", "tool": "echo", "inputs": {"value": "risqué"}}, {"action": "final", "answer": "abandon"}]
    )
    run = launch(ws, _agent(ws, world, mode="reel", approval="effectful"), world["designer"])
    run = drain(run, max_rounds=60, until=lambda r: Approval.objects.filter(step_run__run=r, status="pending").exists())
    ap = Approval.objects.get(step_run__run=run)
    assert "echo" in ap.title.lower() and "risqué" in ap.instructions and run.status == "running"
    boss = make_user("approbatrice", ws, Role.APPROVER)
    from django.db import transaction

    with transaction.atomic():
        engine.decide_approval(ap.pk, boss, False, "non")
    run = drain(run, max_rounds=200)
    out = run.steps.get(node_id="ag").output
    assert (
        run.status == "succeeded"
        and out["steps"][0]["ok"] is False
        and "refusé" in out["steps"][0]["observation"]
        and out["answer"] == "abandon"
    )
    assert not Approval.objects.filter(step_run__run=run).exists()  # the answered request is cleared for the next one


def test_validator_checks_the_agent_step(setup, world):
    from workflows.validation import validate_graph

    ws, _ = setup

    def codes(**kw):
        g = {"inputs": {"type": "object"}, "nodes": [node("ag", "agent", **kw)], "edges": []}
        return {i["code"] for i in validate_graph(g, ws, "t")}

    assert "bad_agent" in codes(tools=["echo"])  # no goal
    assert "bad_agent" in codes(goal="x", tools=[])
    assert "unknown_action" in codes(goal="x", tools=["absente"])
    assert "bad_agent" in codes(goal="x", tools=["echo"], max_steps=50)
    ok = codes(goal="x", tools=["echo"], mode="reel", approval="never")
    assert "agent_no_approval" in ok and "bad_agent" not in ok
    assert "agent_test_mode" in codes(goal="x", tools=["echo"])
