"""`scripts/prod-init.sh` et le jeton de l'agent : fichier distinct, droits 600, transition explicite pour les installations existantes.

Chaque essai se déroule dans un arbre temporaire (copie du script et du modèle) : le vrai `infra/.env.prod` n'est ni lu ni modifié.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LEGACY = "orr_hérité-synthétique-" + "h" * 24


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "infra").mkdir()
    for name in ("prod-init.sh", "_common.sh"):
        shutil.copy(REPO / "scripts" / name, tmp_path / "scripts" / name)
    for name in (".env.prod.example", ".env.agent.example"):
        shutil.copy(REPO / "infra" / name, tmp_path / "infra" / name)
    return tmp_path


def init(tree, *args):
    return subprocess.run(
        [str(tree / "scripts" / "prod-init.sh"), *args],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tree), "ORCH_WORK_HOST_DIR": str(tree / "work")},
    )


def agent_file(tree):
    return (tree / "infra" / ".env.agent").read_text()


def legacy_install(tree):
    (tree / "infra" / ".env.prod").write_text(f"ORCH_SECRET_KEY=cle\nORCH_EXEC_AGENT_TOKEN={LEGACY}\nORCH_DB_NAME=x\n")


def test_a_fresh_install_puts_the_token_in_its_own_file_and_not_in_env_prod(tree):
    r = init(tree)
    assert r.returncode == 0, r.stderr
    prod, agent = (tree / "infra" / ".env.prod").read_text(), agent_file(tree)
    token = agent.strip().split("=", 1)[1]
    assert token.startswith("orr_") and len(token) > 40 and "ORCH_EXEC_AGENT_TOKEN" not in prod and token not in prod
    assert stat.S_IMODE((tree / "infra" / ".env.agent").stat().st_mode) == 0o600
    assert token not in r.stdout + r.stderr  # jamais affiché


def test_replaying_on_an_existing_install_changes_nothing(tree):
    init(tree)
    before = (agent_file(tree), (tree / "infra" / ".env.prod").read_text())
    r = init(tree)
    assert r.returncode == 0 and (agent_file(tree), (tree / "infra" / ".env.prod").read_text()) == before


def test_a_legacy_token_is_reported_not_moved_without_the_explicit_option(tree):
    legacy_install(tree)
    before = (tree / "infra" / ".env.prod").read_text()
    r = init(tree)
    assert r.returncode == 0 and "--migrate-agent-token" in r.stderr
    assert (tree / "infra" / ".env.prod").read_text() == before and not (tree / "infra" / ".env.agent").exists()
    assert LEGACY not in r.stdout + r.stderr


def test_migration_moves_the_same_value_and_removes_it_from_env_prod(tree):
    legacy_install(tree)
    r = init(tree, "--migrate-agent-token")
    assert r.returncode == 0, r.stderr
    prod = (tree / "infra" / ".env.prod").read_text()
    assert (
        agent_file(tree) == f"ORCH_RUNNER_TOKEN={LEGACY}\n"
        and "ORCH_EXEC_AGENT_TOKEN" not in prod
        and LEGACY not in prod
    )
    assert "ORCH_SECRET_KEY=cle" in prod and "ORCH_DB_NAME=x" in prod  # le reste est intact
    assert stat.S_IMODE((tree / "infra" / ".env.agent").stat().st_mode) == 0o600
    assert LEGACY not in r.stdout + r.stderr


@pytest.mark.parametrize("prepare", ["fresh", "already"])
def test_migration_refuses_when_there_is_nothing_to_migrate_or_the_target_exists(tree, prepare):
    if prepare == "fresh":
        init(tree)  # pas de jeton hérité
    else:
        legacy_install(tree)
        (tree / "infra" / ".env.agent").write_text("ORCH_RUNNER_TOKEN=orr_deja\n")
    r = init(tree, "--migrate-agent-token")
    assert r.returncode == 2


def test_rotation_replaces_the_token_and_drops_a_legacy_entry(tree):
    legacy_install(tree)
    init(tree, "--migrate-agent-token")
    old = agent_file(tree)
    r = init(tree, "--rotate-agent-token")
    assert r.returncode == 0 and agent_file(tree) != old and agent_file(tree).startswith("ORCH_RUNNER_TOKEN=orr_")
    assert agent_file(tree).strip().split("=", 1)[1] not in r.stdout + r.stderr


def test_an_unknown_option_is_refused(tree):
    assert init(tree, "--n-importe-quoi").returncode == 2
