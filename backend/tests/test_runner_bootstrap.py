"""AR3.2 — enregistrement d'un runner à jeton fourni par l'environnement (agent d'exécution sans base ni clé, ADR 0023)."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from audit.models import AuditEvent
from runs.models import Runner
from runs.runner_admin import ALL_RUNTIMES
from runs.runner_auth import hash_token

TOKEN = "orr_" + "x" * 40


def run(capsys, **env):
    for k, v in env.items():
        pass
    call_command("runner_bootstrap")
    return capsys.readouterr().out


@pytest.fixture
def tok(monkeypatch):
    monkeypatch.setenv("ORCH_EXEC_AGENT_TOKEN", TOKEN)


def test_creates_a_shared_runner_eligible_for_every_runtime_and_stores_only_the_hash(db, tok, capsys):
    call_command("runner_bootstrap")
    r = Runner.objects.get(name="exec-agent")
    # défaut trouvé sur la vraie pile : sans langages autorisés, le runner n'était éligible à AUCUNE tâche (les actions restaient « en file »)
    assert r.scope == "shared" and r.runtimes == list(ALL_RUNTIMES) and r.runtimes
    assert r.token_hash == hash_token(TOKEN) and TOKEN not in r.token_hash and r.token_prefix == TOKEN[:12]
    assert TOKEN not in capsys.readouterr().out  # jamais affiché
    assert AuditEvent.objects.filter(action="runner.bootstrap").count() == 1


def test_is_idempotent(db, tok, capsys):
    call_command("runner_bootstrap")
    capsys.readouterr()
    call_command("runner_bootstrap")
    assert (
        "rien à faire" in capsys.readouterr().out
        and Runner.objects.count() == 1
        and AuditEvent.objects.filter(action="runner.bootstrap").count() == 1
    )


def test_a_new_token_in_the_environment_replaces_the_old_one_and_keeps_the_settings(db, tok, monkeypatch, capsys):
    call_command("runner_bootstrap")
    Runner.objects.update(runtimes=["python"], paused=True)
    new = "orr_" + "y" * 40
    monkeypatch.setenv("ORCH_EXEC_AGENT_TOKEN", new)
    call_command("runner_bootstrap")
    r = Runner.objects.get()
    assert r.token_hash == hash_token(new) and "jeton remplacé" in capsys.readouterr().out
    assert r.runtimes == ["python"] and r.paused is True  # un réglage fait à la main n'est pas écrasé


@pytest.mark.parametrize("value", ["", "pas-un-jeton", "orr_court"])
def test_refuses_a_missing_or_malformed_token(db, monkeypatch, value):
    monkeypatch.setenv("ORCH_EXEC_AGENT_TOKEN", value)
    with pytest.raises(CommandError, match="absente ou invalide"):
        call_command("runner_bootstrap")
    assert not Runner.objects.exists()


def test_refuses_a_token_that_belongs_to_another_runner(db, tok):
    Runner.objects.create(name="autre", scope="shared", token_hash=hash_token(TOKEN), token_prefix="orr_xxxxxxxx")
    with pytest.raises(CommandError, match="déjà à un autre runner"):
        call_command("runner_bootstrap")


def test_the_agent_stack_gives_the_agent_neither_a_database_nor_the_key():
    import shutil
    import subprocess
    from pathlib import Path

    import yaml

    if not shutil.which("docker"):
        pytest.skip("docker indisponible")
    infra = Path(__file__).resolve().parents[2] / "infra"
    env = {
        "PATH": __import__("os").environ["PATH"],
        "ORCH_DB_PASSWORD": "x",
        "ORCH_WORK_DIR": "/srv/w",
        "ORCH_EXEC_AGENT_TOKEN": TOKEN,
    }
    cmd = [
        "docker",
        "compose",
        "-f",
        str(infra / "docker-compose.prod.yml"),
        "-f",
        str(infra / "docker-compose.roles.yml"),
        "-f",
        str(infra / "docker-compose.agent.yml"),
        "--env-file",
        str(infra / ".env.prod.example"),
        "--profile",
        "agent",
        "config",
        "--format",
        "json",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    if out.returncode:
        pytest.skip(out.stderr[:120])
    services = yaml.safe_load(out.stdout)["services"]
    agent = services["exec-agent"]
    assert "worker-exec" not in services and "worker-ai" not in services  # ni worker qui cumule le socket ET la clé
    assert not [k for k in agent["environment"] if k.startswith("ORCH_DB_") or k == "ORCH_SECRET_KEY"]
    assert not [v for v in agent["volumes"] if "/keys" in v["target"] or "artifacts" in v["target"]]
    assert sorted(agent["networks"]) == ["edge"] and agent["environment"]["ORCH_URL"] == "http://runner-gw:8000"
    holders = [n for n, s in services.items() if any("docker.sock" in v["target"] for v in s.get("volumes", []))]
    keyholders = [n for n, s in services.items() if any(v["target"] == "/keys" for v in s.get("volumes", []))]
    assert holders == ["exec-agent"] and not set(holders) & set(
        keyholders
    )  # I1 : personne ne cumule socket Docker et clé du coffre
