import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pin-images.sh"


def test_write_records_digests_replaces_old_pins_and_skips_missing_images(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text(
        '#!/usr/bin/env bash\ncase "$*" in\n  *python:3.13-slim*) echo "python@sha256:aaa";;\n  *node:22-slim*) echo "node@sha256:bbb";;\n  *) exit 1;;\nesac\n'
    )
    fake.chmod(0o755)
    env_file = tmp_path / "prod.env"
    env_file.write_text("AUTRE=1\nORCH_PYTHON_IMAGE=python@sha256:vieux\n")
    r = subprocess.run(
        ["bash", str(SCRIPT), "--write", str(env_file)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "DOCKER_BIN": str(fake)},
    )
    assert r.returncode == 0, r.stderr
    lines = env_file.read_text().splitlines()
    assert (
        "AUTRE=1" in lines
        and "ORCH_PYTHON_IMAGE=python@sha256:aaa" in lines
        and "ORCH_NODE_IMAGE=node@sha256:bbb" in lines
    )
    assert not any("vieux" in line for line in lines) and not any(
        line.startswith("ORCH_POWERSHELL_IMAGE") for line in lines
    )
    assert "(non téléchargée)" in r.stdout and oct(env_file.stat().st_mode & 0o777) == "0o600"
