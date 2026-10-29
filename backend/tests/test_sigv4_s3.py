import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from runs import http_node
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from vault import sigv4
from vault.models import Connection, Secret

SEEN: list[dict] = []
XML = b"<?xml version='1.0'?><ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'><KeyCount>2</KeyCount><Contents><Key>a.txt</Key></Contents><Contents><Key>b.txt</Key></Contents></ListBucketResult>"


class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        SEEN.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.rfile.read(length).decode() if length else "",
            }
        )
        body = XML if self.command == "GET" and "list-type" in self.path else b"contenu"
        self.send_response(200)
        self.send_header("Content-Type", "application/xml" if body is XML else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_PUT = do_DELETE = _reply

    def log_message(self, *a):
        pass


def test_sigv4_matches_the_published_get_vanilla_test_vector():
    headers = sigv4.sign(
        "GET",
        "https://example.amazonaws.com/",
        {},
        None,
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        service="service",
        now=datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC),
    )
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )
    assert headers["X-Amz-Date"] == "20150830T123600Z"


def test_xml_is_turned_into_data_and_dangerous_documents_are_refused():
    data = http_node.xml_to_data(XML.decode())
    assert data["ListBucketResult"]["KeyCount"] == "2" and [c["Key"] for c in data["ListBucketResult"]["Contents"]] == [
        "a.txt",
        "b.txt",
    ]
    assert http_node.xml_to_data('<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>') is None
    assert http_node.xml_to_data("pas du xml") is None


@pytest.fixture
def ws(world, settings):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    SEEN.clear()
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="aws")
    s.set_value("SECRETKEYSECRETKEY")
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="s3",
        label="S3",
        kind="aws_sigv4",
        secret=s,
        config={
            "base_url": f"http://127.0.0.1:{srv.server_port}",
            "access_key_id": "AKIATEST",
            "region": "eu-west-3",
            "service": "s3",
        },
    )
    yield ws
    srv.shutdown()


def _step(ws, world, op, params, slug):
    user = world["designer"]
    v = publish(
        ws,
        slug,
        [node("h", "http", connection="s3", mode="reel", integration="s3", operation=op, params=params)],
        [],
        user=user,
    )
    run = drain(launch(ws, v, user))
    return run, run.steps.get(node_id="h")


def test_s3_operations_are_signed_and_keys_keep_their_slashes(ws, world):
    run, sr = _step(ws, world, "list_objects", {"bucket": "mon-bucket", "prefix": "rapports/"}, "l")
    sent = SEEN[-1]
    assert run.status == "succeeded" and sent["path"].startswith("/mon-bucket?")
    auth = sent["headers"]["Authorization"]
    assert (
        auth.startswith("AWS4-HMAC-SHA256 Credential=AKIATEST/")
        and "/eu-west-3/s3/aws4_request" in auth
        and "x-amz-content-sha256" in auth
    )
    assert "SECRETKEYSECRETKEY" not in str(sent) and sr.output["body"]["ListBucketResult"]["KeyCount"] == "2"
    _step(ws, world, "put_object", {"bucket": "b", "key": "rapports/2026/oct.csv", "content": "a;b"}, "p")
    put = SEEN[-1]
    assert put["method"] == "PUT" and put["path"] == "/b/rapports/2026/oct.csv" and put["body"] == "a;b"
    assert put["headers"]["X-Amz-Content-Sha256"] == __import__("hashlib").sha256(b"a;b").hexdigest()
    run, sr = _step(ws, world, "get_object", {"bucket": "b", "key": "x y/é.txt"}, "g")
    assert SEEN[-1]["path"] == "/b/x%20y/%C3%A9.txt" and sr.output["body"] == "contenu"
