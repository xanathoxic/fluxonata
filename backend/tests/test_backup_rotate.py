import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup-rotate.sh"


def run(*args, env=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", **(env or {})},
    )


def test_prune_only_keeps_the_most_recent_backups_and_nothing_else(tmp_path):
    names = ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z", "20260104T000000Z"]
    for n in names:
        (tmp_path / n).mkdir()
    (tmp_path / "autre-dossier").mkdir()  # not a backup: never touched
    r = run("--prune-only", "--keep", "2", "--dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["20260103T000000Z", "20260104T000000Z", "autre-dossier"]
    assert "Supprimé" in r.stdout and "limite 2" in r.stdout


def test_bad_options_are_refused(tmp_path):
    assert run("--keep", "0", "--dir", str(tmp_path), "--prune-only").returncode == 2
    assert run("--keep", "x", "--dir", str(tmp_path), "--prune-only").returncode == 2
    assert run("--nope").returncode == 2
