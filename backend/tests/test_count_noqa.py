"""scripts/count_noqa.py : comptage reproductible des exemptions ruff par composant, sur une révision Git (fichiers suivis)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

M = "no" + "qa"  # les exemples ne doivent pas être comptés comme des exemptions du dépôt
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "count_noqa.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("count_noqa", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def make_repo(tmp_path, files):
    git(tmp_path, "init", "-q")
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.replace("@@U", "# " + M.upper()).replace("@@", "# " + M))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "x")
    return tmp_path


def test_counts_lines_by_component_coded_and_bare(mod, tmp_path, monkeypatch):
    repo = make_repo(
        tmp_path,
        {
            "backend/app/a.py": "import os  @@: F401\nx = 1  @@\ny = 2  @@: E501, S101\n",
            "backend/tests/test_a.py": "import os  @@: F401\n",
            "backend/app/migrations/0001.py": "import os  @@: F401\n",
            "scripts/s.py": "import sys  @@U:F401\n",
        },
    )
    monkeypatch.chdir(repo)
    inc, exc = mod.COMPONENTS["C1 backend applicatif"]
    assert mod.count("HEAD", inc, exc) == {
        "lignes": 3,
        "avec_code": 2,
        "nus": 1,
    }  # une ligne à deux codes compte une fois
    assert mod.count("HEAD", *mod.COMPONENTS["C3 tests du backend"])["lignes"] == 1  # les tests ne comptent pas dans C1
    assert mod.count("HEAD", *mod.COMPONENTS["C2 migrations"])["lignes"] == 1
    assert mod.count("HEAD", *mod.COMPONENTS["C7 scripts et bancs"]) == {
        "lignes": 1,
        "avec_code": 1,
        "nus": 0,
    }  # casse ignorée


def test_no_match_is_zero_not_an_error(mod, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, {"backend/app/a.py": "x = 1\n"})
    monkeypatch.chdir(repo)
    assert mod.count("HEAD", *mod.COMPONENTS["C1 backend applicatif"]) == {"lignes": 0, "avec_code": 0, "nus": 0}


def test_unknown_revision_fails_loudly(mod, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, {"backend/app/a.py": "x = 1\n"})
    monkeypatch.chdir(repo)
    with pytest.raises(RuntimeError):
        mod.count("revision-inconnue", ["backend/*.py"], [])
