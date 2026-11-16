"""Les exemples du nœud SSH (examples/ssh) sont valides, se simulent sans réseau ni secret, et ne contiennent aucun secret."""

import json
import re
from pathlib import Path

import pytest

from tests.ssh_helpers import FakeSshd, make_conn, make_secret, publish
from tests.wf_helpers import drain, launch
from workflows.validation import validate

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "ssh"
FILES = sorted(EXAMPLES.glob("*.json"))


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def example_ws(world, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    ws = world["ws_a"]
    users = {"robot": {"password": "pw-synthetique-1234", "keys": []}}
    with FakeSshd(users=users) as bastion, FakeSshd(users=users) as cible:
        for name, d in (("bastion-exemple", bastion), ("cible-exemple", cible)):
            make_conn(ws, name, d, secret=make_secret(ws, f"pw-{name}", "pw-synthetique-1234"))
        make_secret(ws, "secret-exemple", "valeur-synthetique-5678")
        make_conn(ws, "fluxonata_client_key", bastion, secret=make_secret(ws, "pw-fck", "pw-synthetique-1234"))
        yield ws, bastion


def test_there_are_three_examples():
    assert [f.name for f in FILES] == [
        "exemple-ssh-diagnostic.json",
        "exemple-ssh-session.json",
        "tester-connexion-ssh-simulee.json",
    ]


@pytest.mark.parametrize("name", [f.name for f in FILES])
def test_each_example_validates_against_the_workspace_connections(example_ws, name):
    ws, _ = example_ws
    doc = load(name)
    errors = [i for i in validate(doc["graph"], ws, doc["slug"]) if i["level"] == "error"]
    assert errors == []
    assert doc["graph"]["nodes"][0]["mode"] == "test"  # simulated by default


def test_the_examples_simulate_without_touching_the_network_or_a_secret(example_ws, world, monkeypatch):
    import socket

    from core import netsec
    from vault.models import Secret

    ws, daemon = example_ws

    def boom(*a, **k):
        raise AssertionError("réseau ou secret touché en mode test")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(netsec, "resolve", boom)
    monkeypatch.setattr(Secret, "reveal", boom)
    doc = load("exemple-ssh-diagnostic.json")
    v = publish(
        ws,
        doc["slug"],
        doc["graph"]["nodes"],
        doc["graph"]["edges"],
        inputs=doc["graph"]["inputs"],
        outputs=doc["graph"]["outputs"],
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"], inputs={"commandes": ["uname -a", "uptime"]}))
    assert run.status == "succeeded", (
        run.error,
        [(x.node_id, x.status, x.error) for x in run.steps.all() if x.node_id == "diagnostic"],
    )
    assert run.outputs["statut"] == "succeeded"
    assert [r["stdout"] for r in run.outputs["resultats"]] == [
        "résultat simulé de la première commande\n",
        "résultat simulé de la deuxième commande\n",
    ]
    doc = load("exemple-ssh-session.json")
    v = publish(
        ws,
        doc["slug"],
        doc["graph"]["nodes"],
        doc["graph"]["edges"],
        inputs=doc["graph"]["inputs"],
        outputs=doc["graph"]["outputs"],
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"]))
    assert run.status == "succeeded" and run.outputs == {"statut": "succeeded"}
    assert daemon.connections == []


def test_the_examples_contain_no_secret_and_no_address():
    for f in FILES:
        text = f.read_text(encoding="utf-8")
        assert not re.search(
            r"""BEGIN [A-Z ]*PRIVATE KEY|SHA256:[A-Za-z0-9+/]{20,}|"(password|passphrase|secret_value)"\s*:\s*"[^"]{6,}"|\b\d{1,3}(\.\d{1,3}){3}\b""",
            text,
            re.I,
        ), f.name


def test_the_demo_is_accepted_by_the_mcp_path_and_simulated_end_to_end(example_ws, world, monkeypatch):
    """The demonstration draft goes through the same MCP write rules (test mode, SSH connection of this workspace) and its dry run is simulated."""
    import socket

    from ai import dryrun
    from core import netsec
    from runs.runners import SubprocessRunner
    from tests.conftest import client_for
    from tests.test_mcp_design_write import call
    from vault.models import Secret

    ws, daemon = example_ws
    doc = load("tester-connexion-ssh-simulee.json")
    _, out = call(
        client_for(world["designer"]),
        "create_workflow",
        {"slug": doc["slug"], "name": doc["name"], "folder": doc["folder"], "graph": doc["graph"]},
    )
    assert out["status"] == "brouillon" and [i for i in out["issues"] if i["level"] == "error"] == []
    from workflows.models import Workflow

    wf = Workflow.objects.get(workspace=ws, slug="tester-connexion-ssh-simulee")
    assert (
        wf.name == "Tester une connexion SSH simulée"
        and wf.folder == "Infrastructure/Réseau/Tests"
        and not wf.versions.exists()
    )  # a draft, never published

    def boom(*a, **k):
        raise AssertionError("réseau, DNS ou secret touché")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(netsec, "resolve", boom)
    monkeypatch.setattr(Secret, "reveal", boom)
    result = dryrun.dry_run(doc["graph"], ws, SubprocessRunner(), [{"label": "démo", "inputs": {}}])
    steps = {s["id"]: s for s in result["scenarios"][0]["steps"]}
    assert (
        steps["session"]["state"] == "simulated"
        and steps["resume"]["state"] != "skipped"
        and steps["diagnostic"]["state"] == "skipped"
    )
    assert daemon.connections == []
