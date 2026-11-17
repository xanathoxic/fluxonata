"""Durcissement de l'agent (`runner/agent.py`) et du bac à sable partagé (`runs/runners.py`) : images épinglées, restes d'un arrêt brutal, sonde de santé."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from runs import runners
from runs.runners import (
    DockerRunner,
    build_docker_command,
    remove_stale_sandboxes,
    remove_stale_workdirs,
    unpinned_images,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
import agent as agent_mod  # noqa: E402

PINNED = "@sha256:" + "a" * 64


# --- images épinglées par digest ------------------------------------------------------------------------------------------------------------
def test_unpinned_images_lists_only_the_runtimes_served_and_only_tags(settings):
    settings.ORCH_PYTHON_IMAGE = "python:3.13-slim"
    settings.ORCH_NODE_IMAGE = "node" + PINNED
    settings.ORCH_POWERSHELL_IMAGE = "mcr.microsoft.com/powershell:lts"
    assert unpinned_images(["python", "node"]) == ["python:3.13-slim"]  # powershell n'est pas servi : non examiné
    assert unpinned_images(["node"]) == []
    assert unpinned_images(["python", "shell", "unknown"]) == [
        "python:3.13-slim"
    ]  # `shell` utilise l'image Python ; l'inconnu est ignoré


def test_unpinned_images_accepts_a_registry_with_a_port(settings):
    settings.ORCH_PYTHON_IMAGE = "registre.interne:5000/python" + PINNED
    assert unpinned_images(["python"]) == []


# --- étiquette des conteneurs d'action ------------------------------------------------------------------------------------------------------
def _cmd(settings, label):
    settings.ORCH_SANDBOX_LABEL = label
    return build_docker_command("n", "img", "none", 64, Path("/w"), None, runners.RUNTIMES["python"])


def test_action_containers_carry_the_agent_label_only_when_one_is_set(settings):
    assert "--label" not in _cmd(settings, "")
    cmd = _cmd(settings, "abc-123")
    assert cmd[cmd.index("--label") + 1] == "fluxonata.sandbox=abc-123"


def test_the_action_command_stays_unprivileged_whatever_the_label(settings):
    cmd = _cmd(settings, "x")
    for must in ("--read-only", "--cap-drop", "--security-opt", "--pids-limit", "--memory"):
        assert must in cmd
    assert cmd[cmd.index("--user") + 1] == "65534:65534" and "--privileged" not in cmd and "--network" in cmd
    assert not [a for a in cmd if a in ("--cap-add", "--device", "--pid", "--userns", "--ipc=host", "--network=host")]


# --- restes d'un arrêt brutal ---------------------------------------------------------------------------------------------------------------
def test_stale_containers_of_this_agent_are_removed_and_nothing_without_a_label(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="c1\nc2\n" if cmd[1] == "ps" else "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert remove_stale_sandboxes("") == 0 and calls == []  # sans étiquette : on ne touche à aucun conteneur
    assert remove_stale_sandboxes("rid") == 2
    assert calls[0][-2:] == ["--filter", "label=fluxonata.sandbox=rid"] and calls[1] == [
        "docker",
        "rm",
        "-f",
        "c1",
        "c2",
    ]


def test_a_failing_docker_listing_removes_nothing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="c1\n"))
    assert remove_stale_sandboxes("rid") == 0


def test_stale_work_directories_are_removed_but_recent_ones_and_other_files_are_not(tmp_path, settings):
    settings.ORCH_WORK_DIR = str(tmp_path)
    old = time.time() - 7200
    for name in ("orch-run-old", "orch-env-old", "orch-run-new", "autre-dossier"):
        d = tmp_path / name
        d.mkdir()
        (d / "env").write_text("SECRET=valeur-sentinelle-9999\n")
        if name.endswith("old") or name == "autre-dossier":
            os.utime(d, (old, old))
    (tmp_path / "orch-run-fichier").write_text("x")
    assert remove_stale_workdirs() == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["autre-dossier", "orch-run-fichier", "orch-run-new"]
    assert remove_stale_workdirs(older_than=0) == 1  # le plus récent, sur demande explicite


def test_stale_cleanup_never_follows_a_symlink(tmp_path, settings):
    outside = tmp_path / "dehors"
    outside.mkdir()
    (outside / "garder").write_text("x")
    work = tmp_path / "w"
    work.mkdir()
    (work / "orch-run-lien").symlink_to(outside)
    os.utime(outside, (0, 0))
    settings.ORCH_WORK_DIR = str(work)
    assert remove_stale_workdirs(older_than=0) == 0 and (outside / "garder").exists()


def test_the_secret_env_file_directory_is_removed_after_every_outcome(tmp_path, settings, monkeypatch):
    """Succès, échec du conteneur et exception : le répertoire `orch-env-*` (valeurs de secrets en clair) ne survit jamais à l'exécution."""
    settings.ORCH_WORK_DIR = str(tmp_path)
    version = SimpleNamespace(
        runtime="python", source="print(1)", entrypoint="run", limits={"memory_mb": 64}, network_policy="none"
    )

    class Boom:
        def __init__(self, *a, **kw):
            raise OSError("docker introuvable")

    monkeypatch.setattr(runners.subprocess, "Popen", Boom)
    with pytest.raises(OSError):
        DockerRunner().run(version, {}, 5, env={"TOKEN_API": "valeur-sentinelle-9999"})
    assert list(tmp_path.iterdir()) == []


# --- démarrage de l'agent (preflight) -------------------------------------------------------------------------------------------------------
class NoClient:
    def call(self, *a, **k):
        raise AssertionError("pas d'appel réseau attendu")


def _agent(docker=True, runtimes=("python",)):
    a = agent_mod.Agent(NoClient(), object(), docker=docker)
    a.config = {"id": "rid-1", "runtimes": list(runtimes)}
    return a


def test_the_agent_refuses_loose_images_when_digests_are_required(monkeypatch, settings, caplog):
    monkeypatch.setenv("ORCH_REQUIRE_IMAGE_DIGEST", "1")
    monkeypatch.setenv("ORCH_SANDBOX_LABEL", "")  # restaure l'environnement : l'agent le pose
    settings.ORCH_PYTHON_IMAGE = "python:3.13-slim"
    with caplog.at_level(logging.ERROR):
        assert _agent().preflight() == 4
    assert "python:3.13-slim" in caplog.text and "sha256" in caplog.text


def test_the_agent_starts_with_pinned_images_and_labels_its_containers(monkeypatch, settings, tmp_path):
    monkeypatch.setenv("ORCH_REQUIRE_IMAGE_DIGEST", "1")
    monkeypatch.setenv("ORCH_SANDBOX_LABEL", "")
    settings.ORCH_PYTHON_IMAGE = "python" + PINNED
    settings.ORCH_WORK_DIR = str(tmp_path)
    monkeypatch.setattr(runners.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=""))
    assert _agent().preflight() is None and os.environ["ORCH_SANDBOX_LABEL"] == "rid-1"


def test_without_the_requirement_loose_images_are_accepted_and_a_subprocess_agent_checks_nothing(
    monkeypatch, settings, tmp_path
):
    monkeypatch.delenv("ORCH_REQUIRE_IMAGE_DIGEST", raising=False)
    monkeypatch.setenv("ORCH_SANDBOX_LABEL", "")
    settings.ORCH_WORK_DIR = str(tmp_path)
    monkeypatch.setattr(runners.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=""))
    assert _agent().preflight() is None
    monkeypatch.setenv("ORCH_REQUIRE_IMAGE_DIGEST", "1")
    assert _agent(docker=False).preflight() is None  # exécuteur sans conteneur : aucune image


def test_a_failing_cleanup_is_reported_not_fatal(monkeypatch, settings, caplog, tmp_path):
    settings.ORCH_WORK_DIR = str(tmp_path)
    monkeypatch.setenv("ORCH_SANDBOX_LABEL", "")
    monkeypatch.delenv("ORCH_REQUIRE_IMAGE_DIGEST", raising=False)

    def broken(*a, **k):
        raise subprocess.TimeoutExpired("docker", 30)

    monkeypatch.setattr(runners.subprocess, "run", broken)
    with caplog.at_level(logging.WARNING):
        assert _agent().preflight() is None
    assert "nettoyage" in caplog.text


# --- sonde de santé -------------------------------------------------------------------------------------------------------------------------
class Answer:
    def __init__(self, status=200, exc=None):
        self.status, self.exc = status, exc

    def call(self, method, path, body=None, timeout=30.0):
        if self.exc:
            raise self.exc
        assert (method, path) == ("GET", "/runner/me/")
        return self.status, {}


def test_healthcheck_is_ok_only_when_the_platform_accepts_the_token(capsys):
    assert agent_mod.healthcheck(Answer(200), docker=False) == 0 and capsys.readouterr().out.strip() == "OK"
    assert agent_mod.healthcheck(Answer(500), docker=False) == 1
    assert agent_mod.healthcheck(Answer(exc=agent_mod.NetworkError("x")), docker=False) == 1
    assert agent_mod.healthcheck(Answer(exc=agent_mod.Revoked()), docker=False) == 1
    assert "refuse le jeton" in capsys.readouterr().out


def test_healthcheck_needs_the_docker_daemon_for_the_docker_executor(monkeypatch, capsys):
    def run(code):
        monkeypatch.setattr(
            agent_mod.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=b"", stderr=b"")
        )
        return agent_mod.healthcheck(Answer(200), docker=True)

    assert run(0) == 0 and run(1) == 1 and "Docker" in capsys.readouterr().out


def test_healthcheck_never_prints_the_token(capsys):
    client = agent_mod.Client("http://127.0.0.1:9", "orr_valeur-sentinelle-9999")
    assert agent_mod.healthcheck(client, docker=False) == 1  # port fermé
    assert "valeur-sentinelle-9999" not in capsys.readouterr().out


def test_the_shared_daemon_warning_is_printed(monkeypatch, caplog):
    monkeypatch.setenv("ORCH_AGENT_ISOLATION", "shared-docker-daemon")
    monkeypatch.setattr(agent_mod, "healthcheck", lambda *a: 0)
    with caplog.at_level(logging.WARNING):
        assert agent_mod.main(["--url", "http://127.0.0.1:9", "--token", "orr_x", "--healthcheck"]) == 0
    assert "partage le démon Docker" in caplog.text
