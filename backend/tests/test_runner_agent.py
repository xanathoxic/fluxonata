"""The real agent (runner/agent.py) against a real HTTP server, through the real protocol. The sandbox is the
unisolated subprocess executor: Docker itself is exercised by the sandbox tests and by the end-to-end demo."""

import sys
import threading
import time
from pathlib import Path

import pytest

from catalog.models import Action, ActionVersion
from runs import services
from runs.models import Attempt, Execution, Runner
from runs.runner_auth import new_token
from runs.runners import SubprocessRunner
from tenancy.models import Environment
from tests.conftest import make_user, make_workspace
from vault.models import Connection, Secret

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
import agent as agent_mod  # noqa: E402

pytestmark = pytest.mark.django_db(transaction=True)

EMPTY = {"type": "object", "properties": {}}


def action(ws, slug, source, out=None, secrets=None):
    a = Action.objects.create(workspace=ws, slug=slug, name=slug)
    return ActionVersion.objects.create(
        action=a,
        version="1.0.0",
        source=source,
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        output_schema=out or {"type": "object", "properties": {"n": {"type": "integer"}}},
        limits={"timeout_seconds": 30, "memory_mb": 64},
        secrets_required=secrets or [],
    )


def queued(ws, user, version, inputs=None, connections=None):
    env = Environment.objects.get(workspace=ws, kind="test")
    return services.enqueue(
        workspace=ws,
        environment=env,
        action_version=version,
        user=user,
        inputs=inputs or {"n": 20},
        connections=connections or {},
    )


@pytest.fixture
def platform(live_server, settings):
    settings.ORCH_LEASE_SECONDS = 3  # heartbeat every 2 s: cancellation is noticed quickly
    ws = make_workspace("agent-ws")
    user = make_user("dana", ws)
    raw, digest, prefix = new_token()
    runner = Runner.objects.create(
        name="agent", scope="shared", runtimes=["python"], token_hash=digest, token_prefix=prefix, max_concurrent=2
    )
    return type("P", (), {"ws": ws, "user": user, "runner": runner, "url": live_server.url, "token": raw})


def run_agent(p, **kw):
    agent = agent_mod.Agent(
        agent_mod.Client(p.url, p.token),
        SubprocessRunner(),
        concurrency=kw.pop("concurrency", 1),
        once=kw.pop("once", True),
        idle_exit=kw.pop("idle_exit", 20),
    )
    out = []
    t = threading.Thread(target=lambda: out.append(agent.run()), daemon=True)
    t.start()
    return agent, t, out


def test_agent_runs_a_job_end_to_end(platform):
    v = action(platform.ws, "double", "def run(i):\n    print('calcul en cours')\n    return {'n': i['n'] * 2}\n")
    ex = queued(platform.ws, platform.user, v)
    agent, t, out = run_agent(platform)
    t.join(60)
    assert out == [0] and agent.done == 1
    ex.refresh_from_db()
    assert ex.status == "succeeded" and ex.result == {"n": 40} and ex.runner_id == platform.runner.pk
    att = Attempt.objects.get(execution=ex)
    assert att.worker_id == "runner:agent" and "calcul en cours" in att.logs and att.exit_code == 0
    platform.runner.refresh_from_db()
    assert platform.runner.agent_version == agent_mod.VERSION and "/" in platform.runner.platform


def test_agent_reports_failures_and_contract_violations(platform):
    boom = action(platform.ws, "boom", "def run(i):\n    raise RuntimeError('panne volontaire')\n")
    wrong = action(platform.ws, "wrong", "def run(i):\n    return {'n': 'pas un entier'}\n")
    a, b = queued(platform.ws, platform.user, boom), queued(platform.ws, platform.user, wrong)
    agent, t, out = run_agent(platform, once=False, idle_exit=3)
    t.join(60)
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.status == "failed" and "panne volontaire" in (a.error["message"] + Attempt.objects.get(execution=a).logs)
    assert b.status == "failed" and b.error["code"] == "OUTPUT_INVALID"  # validated by the platform
    assert agent.done == 2


def test_secrets_are_delivered_to_the_action_and_masked_by_the_platform(platform):
    v = action(
        platform.ws,
        "needs-secret",
        "import os\ndef run(i):\n    print('token', os.environ['API_TOKEN'])\n    return {'n': len(os.environ['API_TOKEN']), 'echo': os.environ['API_TOKEN']}\n",
        out={"type": "object", "properties": {"n": {"type": "integer"}, "echo": {"type": "string"}}},
        secrets=[{"slot": "api", "env": "API_TOKEN"}],
    )
    s = Secret(workspace=platform.ws, name="api-secret")
    s.set_value("sk-live-very-secret-123456")
    s.save()
    Connection.objects.create(workspace=platform.ws, name="api", label="API", kind="generic", secret=s)
    ex = queued(platform.ws, platform.user, v, connections={"api": "api"})
    _, t, _ = run_agent(platform)
    t.join(60)
    ex.refresh_from_db()
    assert (
        ex.status == "succeeded" and ex.result["n"] == len("sk-live-very-secret-123456") and ex.result["echo"] == "***"
    )
    assert "sk-live" not in Attempt.objects.get(execution=ex).logs


def test_cancellation_reaches_the_running_action(platform):
    v = action(platform.ws, "slow", "import time\ndef run(i):\n    time.sleep(120)\n    return {'n': 1}\n")
    ex = queued(platform.ws, platform.user, v)
    _, t, _ = run_agent(platform)
    deadline = time.time() + 20
    while time.time() < deadline and Execution.objects.get(pk=ex.pk).status != "running":
        time.sleep(0.2)
    assert Execution.objects.get(pk=ex.pk).status == "running"
    started = time.time()
    services.request_cancel(Execution.objects.get(pk=ex.pk))
    t.join(30)
    ex.refresh_from_db()
    assert (
        ex.status == "cancelled" and time.time() - started < 20
    )  # noticed at the next heartbeat, not after the 120 s sleep


def test_revoked_token_and_unreachable_platform_have_distinct_exit_codes(platform):
    agent, t, out = run_agent(platform, once=False)
    time.sleep(1.0)
    Runner.objects.filter(pk=platform.runner.pk).delete()  # revoked while waiting
    t.join(45)
    assert out == [2] and agent.revoked
    assert agent_mod.main(["--url", platform.url, "--token", "orr_" + "x" * 30, "--executor", "subprocess"]) == 2
    assert (
        agent_mod.main(["--url", "http://127.0.0.1:9", "--token", "orr_" + "x" * 30, "--executor", "subprocess"]) == 3
    )
    with pytest.raises(SystemExit):
        agent_mod.main(["--url", platform.url, "--token", "pas-un-jeton", "--executor", "subprocess"])


def test_the_agent_never_logs_the_token(platform, caplog):
    caplog.set_level("DEBUG")
    v = action(platform.ws, "noop", "def run(i):\n    return {'n': 1}\n")
    queued(platform.ws, platform.user, v)
    _, t, _ = run_agent(platform)
    t.join(60)
    assert platform.token not in caplog.text and "orr_" not in caplog.text
