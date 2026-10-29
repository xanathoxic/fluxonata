import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, make_action, node, publish
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    publish(world["ws_a"], "alpha", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    Workflow.objects.filter(slug="alpha").update(folder="Equipe/Bravo", description="Premier")
    return world["ws_a"]


def test_export_all_then_import_with_dry_run_overwrite_and_refusals(ws, world):
    c = client_for(world["designer"])
    doc = c.get(f"{BASE}/workflows/export-all/").data
    assert (
        doc["format"] == "fluxonata-workflows/1"
        and doc["workflows"][0]["slug"] == "alpha"
        and doc["workflows"][0]["published_version"] == "1.0.0"
    )
    wf = doc["workflows"][0]
    other = {**wf, "slug": "beta", "name": "Beta"}
    bad = {"slug": "Pas Valide", "graph": {}}
    broken = {"slug": "casse", "graph": {"nodes": [{"id": "a", "type": "inconnu", "name": "x"}], "edges": []}}
    body = {"format": "fluxonata-workflows/1", "workflows": [wf, other, bad, broken]}
    dry = c.post(f"{BASE}/workflows/import-all/", {**body, "dry_run": True}, format="json").data["results"]
    assert [r["status"] for r in dry] == [
        "skipped",
        "would_create",
        "refused",
        "refused",
    ] and not Workflow.objects.filter(slug="beta").exists()
    done = c.post(f"{BASE}/workflows/import-all/", body, format="json").data["results"]
    assert [r["status"] for r in done[:2]] == ["skipped", "created"] and Workflow.objects.get(
        slug="beta"
    ).latest_version() is None
    before = Workflow.objects.get(slug="alpha").draft_revision
    changed = {**wf, "description": "Modifié", "folder": "Autre"}
    res = c.post(
        f"{BASE}/workflows/import-all/",
        {"format": "fluxonata-workflows/1", "workflows": [changed], "overwrite": True},
        format="json",
    ).data["results"]
    a = Workflow.objects.get(slug="alpha")
    assert (
        res[0]["status"] == "updated"
        and a.description == "Modifié"
        and a.folder == "Autre"
        and a.draft_revision == before + 1
    )
    assert client_for(world["viewer"]).post(f"{BASE}/workflows/import-all/", body, format="json").status_code == 403
    assert c.post(f"{BASE}/workflows/import-all/", {"format": "x"}, format="json").status_code == 400


def test_the_cli_script_documents_itself_and_refuses_without_a_token(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflows_sync.py"
    helped = subprocess.run([sys.executable, "-I", str(script), "--help"], capture_output=True, text=True)
    assert helped.returncode == 0 and "Workflows as code" in helped.stdout.replace(
        "« Workflows as code »", "Workflows as code"
    )
    no_token = subprocess.run(
        [sys.executable, "-I", str(script), "export", "--url", "http://x", "--workspace", "w", "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        env={"PATH": ""},
    )
    assert no_token.returncode != 0 and "FLUXONATA_TOKEN" in no_token.stderr
