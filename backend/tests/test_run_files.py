import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from runs import files
from runs.models import RunFile
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, edge, launch, make_action, node, publish
from vault.models import Connection, Secret

BLOB = bytes(range(256)) * 4  # not valid UTF-8: only a real binary path keeps it intact
SEEN: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        SEEN.append(
            {
                "path": self.path,
                "ctype": self.headers.get("Content-Type"),
                "body": self.rfile.read(length) if length else b"",
            }
        )
        if self.path == "/blob":
            data, ctype = BLOB, "image/png"
        elif self.path == "/huge":
            data, ctype = b"x" * (files.MAX_FILE + 10), "application/octet-stream"
        else:
            data, ctype = b'{"ok": true}', "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _reply

    def log_message(self, *a):
        pass


@pytest.fixture
def ws(world, settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    SEEN.clear()
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="tok")
    s.set_value("sekret-token-123")
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="svc",
        label="Service",
        kind="http_bearer",
        config={"base_url": f"http://127.0.0.1:{srv.server_port}"},
        secret=s,
    )
    yield ws
    srv.shutdown()


def _chain(ws, world, down, up, slug="f"):
    v = publish(
        ws,
        slug,
        [
            node("dl", "http", name="Télécharger", connection="svc", mode="reel", method="GET", **down),
            node("up", "http", name="Envoyer", connection="svc", mode="reel", method="POST", **up),
        ],
        [edge("dl", "up")],
        user=world["designer"],
    )
    return drain(launch(ws, v, world["designer"]))


def test_binary_answer_is_kept_as_a_file_not_in_the_output_and_downloads_intact(ws, world):
    run = _chain(
        ws,
        world,
        {"path": "/blob", "response_file": {"name": "logo.png"}},
        {"path": "/sink", "body_format": "file", "file": "{{ steps.dl.output.file }}"},
    )
    assert run.status == "succeeded", run.error
    out = run.steps.get(node_id="dl").output
    ref = out["file"]
    assert (
        out["body"] is None
        and ref["name"] == "logo.png"
        and ref["size"] == len(BLOB)
        and ref["content_type"] == "image/png"
    )
    assert "_raw" not in out and str(BLOB[:20]) not in str(out)
    sent = SEEN[-1]  # the second step sent the very same bytes with the file's type
    assert sent["path"] == "/sink" and sent["body"] == BLOB and sent["ctype"] == "image/png"

    c = client_for(world["viewer"])
    base = f"/api/v1/workspaces/{ws.slug}"
    listing = c.get(f"{base}/workflow-runs/{run.pk}/files/").json()
    assert [f["name"] for f in listing] == ["logo.png"] and "data" not in listing[0]
    r = c.get(f"{base}/run-files/{ref['file_id']}/download/")
    assert r.status_code == 200 and b"".join(r.streaming_content if r.streaming else [r.content]) == BLOB
    assert r["Content-Type"] == "application/octet-stream" and r["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in r["Content-Disposition"]


def test_a_file_can_be_a_part_of_a_multipart_upload(ws, world):
    run = _chain(
        ws,
        world,
        {"path": "/blob", "response_file": True},
        {
            "path": "/up",
            "body_format": "multipart",
            "body": {"titre": "Logo"},
            "files": [{"field": "fichier", "filename": "l.png", "file": "{{ steps.dl.output.file }}"}],
        },
    )
    assert run.status == "succeeded", run.error
    sent = SEEN[-1]
    assert sent["ctype"].startswith("multipart/form-data; boundary=fluxonata-")
    assert b'name="titre"\r\n\r\nLogo' in sent["body"] and b'filename="l.png"' in sent["body"]
    assert b"Content-Type: image/png\r\n\r\n" + BLOB + b"\r\n" in sent["body"]


def test_limits_and_foreign_references_are_refused(ws, world):
    run = _chain(ws, world, {"path": "/huge", "response_file": True}, {"path": "/sink"}, slug="big")
    assert run.status == "failed" and run.error["code"] == "FILE_TOO_BIG" and not RunFile.objects.exists()

    first = _chain(ws, world, {"path": "/blob", "response_file": True}, {"path": "/sink"}, slug="ok")
    fid = first.steps.get(node_id="dl").output["file"]["file_id"]
    # another run (not of the same family) cannot use the file, whether it guesses the id or not
    other = _chain(
        ws,
        world,
        {"path": "/blob"},
        {"path": "/sink", "body_format": "file", "file": fid},
        slug="steal",
    )
    assert other.status == "failed" and other.error["code"] == "FILE_MISSING"
    for bad in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        r = _chain(
            ws, world, {"path": "/blob"}, {"path": "/sink", "body_format": "file", "file": bad}, slug="b" + bad[:3]
        )
        assert r.status == "failed" and r.error["code"] == "FILE_MISSING"


def test_download_is_limited_to_the_workspace_and_the_quota_per_run(ws, world):
    run = _chain(ws, world, {"path": "/blob", "response_file": True}, {"path": "/sink"}, slug="dl")
    fid = run.steps.get(node_id="dl").output["file"]["file_id"]
    base = f"/api/v1/workspaces/{ws.slug}/run-files/{fid}/download/"
    assert client_for(world["outsider"]).get(base).status_code in (403, 404)
    assert client_for(world["designer"]).get(base).status_code == 200
    # per-run quota
    run_obj = RunFile.objects.get(pk=fid).run
    for i in range(files.MAX_RUN // files.MAX_FILE - 1):
        files.save(run_obj, "x", f"f{i}", "", b"y" * files.MAX_FILE)
    with pytest.raises(files.FileError) as exc:
        files.save(run_obj, "x", "one-more", "", b"z" * files.MAX_FILE)
    assert exc.value.code == "FILE_QUOTA"
    assert files.safe_name("../../etc/passwd") == "passwd" and files.safe_name("") == "fichier"


def test_files_leave_with_their_run_and_test_mode_sends_nothing(ws, world):
    v = publish(
        ws,
        "t",
        [node("dl", "http", name="Télécharger", connection="svc", method="GET", path="/blob", response_file=True)],
        [],
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"]))
    out = run.steps.get(node_id="dl").output
    assert out["simulated"] is True and out["file"]["file_id"] is None and SEEN == [] and not RunFile.objects.exists()
    real = _chain(ws, world, {"path": "/blob", "response_file": True}, {"path": "/sink"}, slug="r")
    assert RunFile.objects.filter(run=real).count() == 1
    real.delete()
    assert RunFile.objects.count() == 0
