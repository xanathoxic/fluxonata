"""Étapes IA sans worker « exec » (topologie par agents) : qui peut les prendre, et surtout qui ne le peut pas."""

import pytest

from ai.providers import FakeProvider
from ai.runner import AiRunner
from runs import engine, services
from runs import worker as w
from runs.models import Execution
from tests.test_worker_roles import Stop
from tests.wf_helpers import launch, make_action, node, publish, schema


@pytest.fixture
def queued_both(ai_ws, world):
    """Un workflow dont deux étapes sont en file : une action Python (conteneur) et une étape IA."""
    ws, _ = ai_ws
    make_action(ws, "echo")
    v = publish(
        ws,
        "both",
        [
            node("a", action="echo", inputs={"value": "x"}),
            node(
                "i", "ai", provider="fake", prompt="Qualifie", output_schema=schema(["label"], label={"type": "string"})
            ),
        ],
        [],
        user=world["designer"],
    )
    run = launch(ws, v, world["designer"])
    engine.advance(run.pk)
    runtimes = set(Execution.objects.filter(status="queued").values_list("action_version__runtime", flat=True))
    assert "ai" in runtimes and len(runtimes) == 2
    return run


def test_a_restricted_claim_only_takes_ai_steps_and_never_a_container_action(queued_both):
    first = services.claim_next("aux", runtimes=("ai",))
    assert first is not None and first[0].action_version.runtime == "ai"
    assert services.claim_next("aux", runtimes=("ai",)) is None  # il reste l'action Python : refusée, pas prise
    assert Execution.objects.filter(status="queued", action_version__runtime="python").count() == 1


def test_an_unrestricted_claim_is_unchanged(queued_both):
    assert services.claim_next("exec") is not None and services.claim_next("exec") is not None
    assert not Execution.objects.filter(status="queued").exists()


def test_an_ai_step_runs_to_the_end_with_no_exec_worker(ai_ws, world, monkeypatch):
    ws, _ = ai_ws
    v = publish(
        ws,
        "ai-only",
        [
            node(
                "i", "ai", provider="fake", prompt="Qualifie", output_schema=schema(["label"], label={"type": "string"})
            )
        ],
        [],
        outputs={"l": "{{ steps.i.output.label }}"},
        user=world["designer"],
    )
    monkeypatch.setattr(
        AiRunner,
        "__init__",
        lambda self, execution, provider=None: (
            setattr(self, "execution", execution)
            or setattr(self, "provider", FakeProvider(script='{"label": "positif"}'))
        ),
    )
    run = launch(ws, v, world["designer"])
    for _ in range(10):
        engine.advance(run.pk)
        w.process_one("aux", runtimes=w.AI_STEP_RUNTIMES)
    run.refresh_from_db()
    assert run.status == "succeeded" and run.outputs == {"l": "positif"}


@pytest.fixture
def loop(monkeypatch, settings, db):
    """Une itération de `run_forever` : on note les appels à `process_one` (arguments compris), rien n'est exécuté."""
    settings.ORCH_ROLES = ""
    calls = []

    class FakeThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(w.threading, "Thread", FakeThread)
    monkeypatch.setattr(w, "close_old_connections", lambda: None)
    monkeypatch.setattr(w, "process_one", lambda worker_id, **kw: calls.append(kw) or False)
    monkeypatch.setattr(w.services, "reconcile_expired", lambda: None)
    monkeypatch.setattr(w, "_pause", lambda s: (_ for _ in ()).throw(Stop()))
    w.STOP.clear()
    return calls


def one_iteration(roles):
    with pytest.raises(Stop):
        w.run_forever(roles=roles)


def test_aux_takes_ai_steps_only_when_enabled(loop, settings):
    settings.ORCH_AUX_RUNS_AI_STEPS = True
    one_iteration("aux")
    assert loop == [{"runtimes": ("ai",)}]


def test_aux_takes_nothing_by_default(loop, settings):
    settings.ORCH_AUX_RUNS_AI_STEPS = False
    one_iteration("aux")
    assert loop == []


def test_the_exec_role_keeps_its_unrestricted_claim_even_when_enabled(loop, settings):
    settings.ORCH_AUX_RUNS_AI_STEPS = True
    one_iteration("exec,aux")
    assert loop == [{}]  # aucun filtre de langage : comportement historique


def test_the_ai_only_role_never_claims_executions(loop, settings):
    settings.ORCH_AUX_RUNS_AI_STEPS = True
    one_iteration("ai")
    assert loop == []
