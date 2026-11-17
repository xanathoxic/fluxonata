"""Le même script Python produit le même résultat sur le worker intégré et sur un agent distant (même plate-forme, même protocole, exécuteur sans isolation).

Ce que ces essais établissent : la CHAÎNE (contrat de sortie, masquage, limites, codes d'erreur) est identique des deux côtés. Ils n'établissent pas l'identité
des images ni du démon Docker : l'exécuteur est le même objet des deux côtés, les différences d'environnement (images, démon Docker distinct) n'ont pas été éprouvées.
"""

import sys
import threading
from pathlib import Path

import pytest

from catalog.models import Action, ActionVersion
from runs import services
from runs.models import Attempt, Execution, Runner
from runs.runner_auth import new_token
from runs.runners import SubprocessRunner
from runs.worker import process_one
from tenancy.models import Environment
from tests.agent_corpus import CASES, SECRET
from tests.conftest import make_user, make_workspace
from vault.models import Connection, Secret

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
import agent as agent_mod  # noqa: E402

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def platform(live_server, settings):
    settings.ORCH_LEASE_SECONDS = 6
    ws = make_workspace("parity")
    user = make_user("dana", ws)
    raw, digest, prefix = new_token()
    runner = Runner.objects.create(
        name="agent", scope="shared", runtimes=["python"], token_hash=digest, token_prefix=prefix, max_concurrent=1
    )
    s = Secret(workspace=ws, name="api-secret")
    s.set_value(SECRET)
    s.save()
    Connection.objects.create(workspace=ws, name="api", label="API", kind="generic", secret=s)
    return type("P", (), {"ws": ws, "user": user, "runner": runner, "url": live_server.url, "token": raw})


def version(ws, name, source, timeout, secret):
    a = Action.objects.create(workspace=ws, slug=name, name=name)
    return ActionVersion.objects.create(
        action=a,
        version="1.0.0",
        source=source,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        limits={"timeout_seconds": timeout, "memory_mb": 128},
        secrets_required=[{"slot": "api", "env": "API_TOKEN"}] if secret else [],
    )


def enqueue(p, v, inputs, secret):
    return services.enqueue(
        workspace=p.ws,
        environment=Environment.objects.get(workspace=p.ws, kind="test"),
        action_version=v,
        user=p.user,
        inputs=inputs,
        connections={"api": "api"} if secret else {},
    )


def on_worker(p, v, inputs, secret):
    ex = enqueue(p, v, inputs, secret)
    assert process_one("worker-intégré", SubprocessRunner())
    return Execution.objects.get(pk=ex.pk)


def on_agent(p, v, inputs, secret):
    ex = enqueue(p, v, inputs, secret)
    agent = agent_mod.Agent(
        agent_mod.Client(p.url, p.token), SubprocessRunner(), concurrency=1, once=True, idle_exit=30
    )
    out = []
    t = threading.Thread(target=lambda: out.append(agent.run()), daemon=True)
    t.start()
    t.join(90)
    assert out == [0], "l'agent ne s'est pas arrêté normalement"
    return Execution.objects.get(pk=ex.pk)


def observed(ex):
    logs = Attempt.objects.get(execution=ex).logs
    return {
        "status": ex.status,
        "result": ex.result,
        "error_code": (ex.error or {}).get("code"),
        "logs_len_capped": len(logs.encode()) <= 64 * 1024 + 8,
        "secret_in_logs": SECRET in logs,
        "secret_in_result": SECRET in str(ex.result),
        "logs_mark": [
            m for m in ("vers-stdout", "vers-stderr", "panne-volontaire", "No module named", "FIN", "***") if m in logs
        ],
    }


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_same_script_gives_the_same_outcome_on_the_builtin_worker_and_on_a_remote_agent(platform, name):
    source, inputs, timeout, secret = CASES[name]
    worker = observed(on_worker(platform, version(platform.ws, f"w-{name}", source, timeout, secret), inputs, secret))
    remote = observed(on_agent(platform, version(platform.ws, f"a-{name}", source, timeout, secret), inputs, secret))
    assert worker == remote
    assert (
        not worker["secret_in_logs"] and not worker["secret_in_result"]
    )  # la valeur n'apparaît nulle part, des deux côtés


EXPECTED = {
    "stdlib": ("succeeded", None),
    "inputs-dict-list-null": ("succeeded", None),
    "structured-output": ("succeeded", None),
    "stdout-stderr": ("succeeded", None),
    "exception": ("failed", "ACTION_ERROR"),
    "timeout": ("failed", "TIMEOUT"),
    "missing-dependency": ("failed", None),
    "result-too-large": ("failed", "RESULT_TOO_LARGE"),
    "logs-too-large": ("succeeded", None),
    "secret": ("succeeded", None),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_corpus_outcomes_are_the_documented_ones(platform, name):
    source, inputs, timeout, secret = CASES[name]
    seen = observed(on_agent(platform, version(platform.ws, f"d-{name}", source, timeout, secret), inputs, secret))
    status, code = EXPECTED[name]
    assert seen["status"] == status and (code is None or seen["error_code"] == code), seen
    if name == "missing-dependency":
        assert (
            "No module named" in " ".join(seen["logs_mark"]) or seen["error_code"] is not None
        )  # message explicite de Python
        assert seen["logs_mark"].count("No module named") == 1
    if name == "secret":
        assert seen["result"]["echo"] == "***" and seen["result"]["n"] == len(SECRET)
    if name == "logs-too-large":
        assert seen["logs_len_capped"] and "FIN" in seen["logs_mark"]  # la fin des journaux est conservée
    if name == "stdout-stderr":
        assert seen["logs_mark"] == [
            "vers-stdout",
            "vers-stderr",
        ]  # `print` et stderr vont tous deux au journal ; le résultat est l'enveloppe
