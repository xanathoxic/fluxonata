import shutil
import socket
import subprocess
import time

import pytest

from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from vault.models import Connection, Secret

paramiko = pytest.importorskip("paramiko")
PASSWORD = "sftp-pw-12345"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def sftp_server():
    if shutil.which("docker") is None:
        pytest.skip("docker indisponible")
    port = _free_port()
    cid = subprocess.run(
        ["docker", "run", "-d", "--rm", "-p", f"127.0.0.1:{port}:22", "atmoz/sftp:alpine", f"robot:{PASSWORD}:::depot"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not cid:
        pytest.skip("image atmoz/sftp indisponible")
    try:
        deadline = time.time() + 90
        while True:
            try:
                t = paramiko.Transport(socket.create_connection(("127.0.0.1", port), 3))
                t.start_client(timeout=5)
                t.auth_password("robot", PASSWORD)
                client = paramiko.SFTPClient.from_transport(t)
                break
            except Exception:  # noqa: BLE001  (still starting)
                if time.time() > deadline:
                    pytest.skip("le serveur SFTP ne démarre pas")
                time.sleep(1)
        with client.open("/depot/bonjour.txt", "wb") as fh:
            fh.write(b"salut")
        fingerprint = "SHA256:" + __import__("base64").b64encode(
            __import__("hashlib").sha256(t.get_remote_server_key().asbytes()).digest()
        ).decode().rstrip("=")
        t.close()
        yield port, fingerprint
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


@pytest.fixture
def ws(world, sftp_server, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    port, _ = sftp_server
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="sftppw")
    s.set_value(PASSWORD)
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="depot",
        label="Dépôt",
        kind="sftp",
        secret=s,
        config={"host": "127.0.0.1", "port": str(port), "username": "robot"},
    )
    return ws


def _run(ws, world, slug, **fields):
    user = world["designer"]
    v = publish(ws, slug, [node("f", "sftp", connection="depot", mode="reel", **fields)], [], user=user)
    run = drain(launch(ws, v, user), max_rounds=1500)
    return run, run.steps.get(node_id="f")


def test_list_read_write_delete_round_trip_and_fingerprint(ws, world, sftp_server):
    _, fingerprint = sftp_server
    run, sr = _run(ws, world, "a", operation="list", path="/depot")
    assert (
        run.status == "succeeded"
        and [e["name"] for e in sr.output["entries"]] == ["bonjour.txt"]
        and sr.output["host_fingerprint"] == fingerprint
    )
    run, sr = _run(ws, world, "b", operation="read", path="/depot/bonjour.txt")
    assert sr.output["content"] == "salut"
    run, sr = _run(
        ws, world, "c", operation="write", path="/depot/nouveau.txt", content="Bonjour {{ run.environment }}"
    )
    assert sr.output["size"] == len("Bonjour test")
    run, sr = _run(ws, world, "d", operation="read", path="/depot/nouveau.txt")
    assert sr.output["content"] == "Bonjour test"
    run, sr = _run(ws, world, "e", operation="delete", path="/depot/nouveau.txt")
    assert run.status == "succeeded"
    run, _ = _run(ws, world, "f", operation="read", path="/depot/nouveau.txt")
    assert run.error["code"] == "SFTP_NOT_FOUND"


def test_host_key_pinning_auth_blocked_hosts_and_test_mode(ws, world, sftp_server, settings):
    conn = Connection.objects.get(name="depot")
    Connection.objects.filter(pk=conn.pk).update(config={**conn.config, "host_key_sha256": "SHA256:fausse"})
    run, _ = _run(ws, world, "g", operation="list", path="/depot")
    assert run.error["code"] == "SFTP_HOSTKEY"
    Connection.objects.filter(pk=conn.pk).update(config={**conn.config, "host_key_sha256": sftp_server[1]})
    run, sr = _run(ws, world, "h", operation="list", path="/depot")
    assert run.status == "succeeded"
    s = Secret.objects.get(name="sftppw")
    s.set_value("mauvais")
    s.save()
    run, _ = _run(ws, world, "i", operation="list", path="/depot")
    assert run.error["code"] == "SFTP_AUTH" and "mauvais" not in str(run.error)
    settings.ORCH_EGRESS_ALLOWLIST = ""
    run, _ = _run(ws, world, "j", operation="list", path="/depot")
    assert run.error["code"] == "SFTP_BLOCKED"
    v = publish(
        ws,
        "k",
        [node("f", "sftp", connection="depot", mode="test", operation="delete", path="/depot/bonjour.txt")],
        [],
        user=world["designer"],
    )
    out = drain(launch(ws, v, world["designer"])).steps.get(node_id="f").output
    assert out["simulated"] is True


def test_validator(ws, world):
    from workflows.validation import validate_graph

    g = {
        "inputs": {"type": "object"},
        "nodes": [node("f", "sftp", connection="depot", operation="write", path="/x")],
        "edges": [],
    }
    assert "bad_sftp" in {i["code"] for i in validate_graph(g, ws, "t")}  # nothing to write
    g["nodes"][0].update(operation="list", connection="absente")
    assert "unknown_connection" in {i["code"] for i in validate_graph(g, ws, "t")}


def test_sftp_folder_polling_trigger_starts_one_run_per_new_file(ws, world, sftp_server):
    from runs.models import WorkflowRun
    from tests.conftest import client_for
    from triggers import poller
    from triggers.models import PollTrigger

    publish(
        ws,
        "fichier",
        [node("e", action="echo", inputs={"value": "{{ inputs.item.name }}"})],
        [],
        inputs={"type": "object", "required": ["item"], "properties": {"item": {"type": "object"}}},
        user=world["designer"],
    )
    r = client_for(world["designer"]).post(
        "/api/v1/workspaces/team-a/poll-triggers/",
        {
            "name": "Dépôt",
            "workflow": "fichier",
            "environment": "test",
            "source": "sftp",
            "interval_seconds": 60,
            "id_path": "id",
            "request": {"connection": "depot", "path": "/depot", "name_contains": ".txt"},
        },
        format="json",
    )
    assert r.status_code == 201, r.data
    t = PollTrigger.objects.get(pk=r.data["id"])
    poller.poll_once(PollTrigger.objects.get(pk=t.pk))
    t = PollTrigger.objects.get(pk=t.pk)
    assert t.last_ok and "mémorisé" in t.last_note and WorkflowRun.objects.filter(workspace=ws).count() == 0
    _run(ws, world, "w", operation="write", path="/depot/arrive.txt", content="x")
    _run(ws, world, "w2", operation="write", path="/depot/ignore.dat", content="x")
    poller.poll_once(PollTrigger.objects.get(pk=t.pk))
    runs = list(WorkflowRun.objects.filter(workspace=ws, workflow_version__workflow__slug="fichier"))
    assert [r.inputs["item"]["name"] for r in runs] == ["arrive.txt"]
    _run(ws, world, "d", operation="delete", path="/depot/arrive.txt")
    _run(ws, world, "d2", operation="delete", path="/depot/ignore.dat")


def test_binary_files_are_read_into_the_run_and_written_back_intact(ws, world, sftp_server):
    import hashlib

    from runs.models import RunFile
    from tests.wf_helpers import edge

    port, _ = sftp_server
    blob = bytes(range(256)) * 64  # 16 KB, not UTF-8
    t = paramiko.Transport(socket.create_connection(("127.0.0.1", port), 5))
    t.start_client(timeout=5)
    t.auth_password("robot", PASSWORD)
    client = paramiko.SFTPClient.from_transport(t)
    with client.open("/depot/image.bin", "wb") as fh:
        fh.write(blob)
    user = world["designer"]
    v = publish(
        ws,
        "bin",
        [
            node("r", "sftp", connection="depot", mode="reel", operation="read", path="/depot/image.bin", binary=True),
            node(
                "w",
                "sftp",
                connection="depot",
                mode="reel",
                operation="write",
                path="/depot/copie.bin",
                file="{{ steps.r.output.file }}",
            ),
        ],
        [edge("r", "w")],
        user=user,
    )
    run = drain(launch(ws, v, user), max_rounds=1500)
    try:
        assert run.status == "succeeded", run.error
        ref = run.steps.get(node_id="r").output["file"]
        assert (
            ref["name"] == "image.bin"
            and ref["size"] == len(blob)
            and ref["sha256"] == hashlib.sha256(blob).hexdigest()
        )
        assert bytes(RunFile.objects.get(pk=ref["file_id"]).data) == blob and run.steps.get(node_id="w").output[
            "size"
        ] == len(blob)
        with client.open("/depot/copie.bin", "rb") as fh:
            assert fh.read() == blob
    finally:
        client.close()
        t.close()
