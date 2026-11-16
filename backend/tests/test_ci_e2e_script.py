"""Préparation CI/E2E (`scripts/ci-e2e.sh`) : refus hors base jetable, refus si une pile occupe les ports, ordre base → migrations → services → tests.

Les refus sont essayés sur une copie des scripts sans `.env` (rien ne lit la base ni la pile de développement) ; l'ordre est vérifié sur le texte du
script et du workflow, le parcours réel (base vide, migrations, E2E) l'étant par une exécution sur base vide jetable (voir l'en-tête du script).
"""

import os
import shutil
import socket
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "scripts" / "ci-e2e.sh").read_text(encoding="utf-8")
WORKFLOW = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def run_isolated(tmp_path, **env):
    (tmp_path / "scripts").mkdir()
    for name in ("_common.sh", "ci-e2e.sh"):
        shutil.copy(REPO / "scripts" / name, tmp_path / "scripts" / name)
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    return subprocess.run(
        ["bash", str(tmp_path / "scripts" / "ci-e2e.sh")],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_refuses_without_the_disposable_database_declaration(tmp_path):
    out = run_isolated(tmp_path)
    assert out.returncode == 2 and "CI_DISPOSABLE_DB=1" in out.stderr
    assert not (tmp_path / "artifacts").exists()  # rien n'a démarré : ni migration ni service


def test_refuses_any_other_value_of_the_declaration(tmp_path):
    out = run_isolated(tmp_path, CI_DISPOSABLE_DB="true")
    assert out.returncode == 2 and not (tmp_path / "artifacts").exists()


def test_refuses_when_a_stack_already_listens_on_the_api_port(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        out = run_isolated(tmp_path, CI_DISPOSABLE_DB="1", ORCH_API_PORT=str(port), ORCH_WEB_PORT="1")
    assert out.returncode == 2 and f"port {port}" in out.stderr
    assert not (tmp_path / "artifacts").exists()


def test_order_is_database_then_migrations_then_services_then_browser_tests():
    marks = ["ensure_connection", "manage.py migrate --noinput", "start api", "health/ready", '\nscripts/e2e.sh "$@"']
    positions = [SCRIPT.index(m) for m in marks]
    assert positions == sorted(positions)


def test_the_workflow_runs_the_prepared_e2e_after_the_other_steps_with_the_declaration():
    assert (
        WORKFLOW.index("--wait db testsvc")
        < WORKFLOW.index("scripts/ci.sh --skip-e2e")
        < WORKFLOW.index("scripts/ci-e2e.sh")
    )
    assert 'CI_DISPOSABLE_DB: "1"' in WORKFLOW


def test_the_development_ci_script_still_never_migrates():
    # `ci.sh` reste un outil de développement : aucune migration appliquée sans déclaration de base jetable
    ci = (REPO / "scripts" / "ci.sh").read_text(encoding="utf-8")
    assert "manage.py migrate" not in ci


def test_the_workflow_installs_chromium_with_its_system_libraries_before_the_e2e():
    assert WORKFLOW.index("playwright install --with-deps chromium") < WORKFLOW.index("scripts/ci-e2e.sh")
