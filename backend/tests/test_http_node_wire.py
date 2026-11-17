"""`runs.http_node` côté fil : réponse décodée, corps multipart, nouvelles tentatives, XML/SSE, `fail_if`."""

import json
from types import SimpleNamespace

import pytest

from core import netsec
from runs import files, http_node
from runs.http_node import HttpStepError

SPEC = {"method": "GET", "timeout": 5, "body": None}


def _reply(monkeypatch, status=200, headers=None, raw=b"", exc=None):
    def fake(method, url, **kw):
        if exc:
            raise exc
        return status, headers or {}, raw

    monkeypatch.setattr(netsec, "safe_request", fake)


def _send(monkeypatch, spec=None, secrets=(), **reply):
    _reply(monkeypatch, **reply)
    return http_node._send({**SPEC, **(spec or {})}, "https://h.test/x", {}, list(secrets))


def test_json_reply_is_decoded_and_secrets_masked(monkeypatch):
    raw = json.dumps({"a": "tok-123 ok"}).encode()
    result, error, lower = _send(
        monkeypatch, secrets=["tok-123"], headers={"Content-Type": "application/json", "ETag": "v1"}, raw=raw
    )
    assert error is None and result["ok"] and "tok-123" not in json.dumps(result["body"])
    assert result["headers"]["etag"] == "v1" and lower["content-type"] == "application/json"


def test_bad_json_stays_text_and_status_not_ok(monkeypatch):
    result, _, _ = _send(monkeypatch, status=500, headers={"content-type": "application/json"}, raw=b"{pas json")
    assert result["body"] == "{pas json" and result["ok"] is False


def test_retry_after_and_truncation(monkeypatch):
    result, _, _ = _send(monkeypatch, status=429, headers={"retry-after": "7"}, raw=b"x" * 100, spec={"max_bytes": 10})
    assert result["retry_after"] == 7 and result["truncated"] is True and len(result["body"]) == 10


def test_nul_bytes_are_replaced(monkeypatch):
    result, _, _ = _send(monkeypatch, raw=b"a\x00b")
    assert result["body"] == "a�b"


def test_event_stream_and_xml_replies(monkeypatch):
    sse, _, _ = _send(monkeypatch, headers={"content-type": "text/event-stream"}, raw=b'data: {"x": 1}\n\n')
    assert sse["body"] == {"x": 1}
    sse2, _, _ = _send(monkeypatch, headers={"content-type": "text/event-stream"}, raw=b"data: pas json\n")
    assert sse2["body"] == "data: pas json\n"
    xml, _, _ = _send(monkeypatch, headers={"content-type": "application/xml"}, raw=b"<r><a>1</a><a>2</a></r>")
    assert xml["body"] == {"r": {"a": ["1", "2"]}}
    bad, _, _ = _send(monkeypatch, headers={"content-type": "text/xml"}, raw=b"<r><a></r>")
    assert bad["body"] == "<r><a></r>"


def test_xml_with_entities_is_never_parsed():
    assert http_node.xml_to_data('<!DOCTYPE r [<!ENTITY a "b">]><r>&a;</r>') is None
    assert http_node.sse_last_json("rien") is None


def test_network_errors_become_explicit_errors(monkeypatch):
    _, error, _ = _send(monkeypatch, exc=netsec.UnsafeURL("privée"))
    assert error["code"] == "HTTP_BLOCKED" and "privée" in error["message"]
    _, error, _ = _send(monkeypatch, exc=TimeoutError())
    assert error["code"] == "HTTP_ERROR" and "TimeoutError" in error["message"]


def test_response_saved_as_file_is_kept_raw_and_bounded(monkeypatch):
    spec = {"save_file": {"name": "r"}}
    result, error, _ = _send(monkeypatch, spec=spec, headers={"content-type": "application/pdf"}, raw=b"%PDF")
    assert error is None and result["_raw"] == b"%PDF" and result["body"] is None
    monkeypatch.setattr(http_node, "MAX_FILE", 3)
    _, error, _ = _send(monkeypatch, spec=spec, raw=b"%PDF")
    assert error["code"] == "FILE_TOO_BIG"


def test_signer_signs_the_request(monkeypatch):
    seen = {}
    _reply(monkeypatch, raw=b"ok")
    http_node._send(
        {**SPEC, "body": "abc"}, "https://h.test/x", {"A": "1"}, [], lambda m, u, h, b: seen.update(b=b) or {"S": "x"}
    )
    assert seen["b"] == b"abc"


def test_multipart_text_files_and_refusals():
    boundary, body = http_node.multipart(
        {"a": "1", "b": {"x": 1}}, [{"field": "f", "filename": "n.txt", "content": "hi"}]
    )
    assert f"--{boundary}--" in body and 'name="b"' in body and '{"x": 1}' in body and 'filename="n.txt"' in body
    for fields, fl in (
        ({"a": "1"}, "x"),
        ("x", []),
        ({}, [{"filename": 'a"b'}]),
        ({"a\n": "1"}, []),
        ({}, [{"content_type": "a\nb"}]),
    ):
        with pytest.raises(HttpStepError):
            http_node.multipart(fields, fl)


def test_multipart_bytes_reads_files_through_the_loader():
    f = SimpleNamespace(data=b"\x00\x01", content_type="application/x-bin", name="b.bin")
    boundary, body = http_node.multipart_bytes(
        {"a": "1"}, [{"file_id": "id1", "field": "up"}, {"content": "txt"}], lambda fid: f
    )
    assert b"\x00\x01" in body and b'filename="b.bin"' in body and b"application/x-bin" in body
    assert b'filename="fichier.txt"' in body and body.endswith(f"--{boundary}--\r\n".encode())
    with pytest.raises(HttpStepError):
        http_node.multipart_bytes("x", [], None)
    with pytest.raises(HttpStepError):
        http_node.multipart_bytes({}, [{"content": "a", "content_type": "a\nb"}], None)


def test_check_parts_shapes():
    with pytest.raises(HttpStepError):
        http_node._check_parts("x")
    assert http_node._check_parts([{"content": 5, "field": "f"}]) == [{"field": "f", "content": "5"}]
    with pytest.raises(HttpStepError):
        http_node._check_parts([{"file": {"sans": "identifiant"}}])


def test_save_file_name_is_cleaned():
    assert http_node._save_file({}) is None
    assert http_node._save_file({"response_file": {"name": "../x y.pdf"}})["name"] == files.safe_name(
        "../x y.pdf", "reponse"
    )
    assert http_node._save_file({"response_file": True})["name"] == "reponse"


@pytest.mark.parametrize(
    ("spec", "attempt", "error", "result", "expected"),
    [
        ({}, 1, None, {"status": 429}, None),
        ({"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "GET"}, 3, None, {"status": 503}, None),
        ({"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "POST"}, 1, None, {"status": 429}, 2.0),
        ({"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "POST"}, 1, None, {"status": 500}, None),
        ({"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "GET"}, 2, None, {"status": 500}, 4.0),
        ({"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "GET"}, 1, {"code": "HTTP_ERROR"}, None, 2.0),
        ({"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "POST"}, 1, {"code": "HTTP_ERROR"}, None, None),
        (
            {"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "GET"},
            1,
            {"code": "HTTP_BLOCKED"},
            None,
            None,
        ),
        (
            {"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "GET"},
            1,
            None,
            {"status": 429, "retry_after": 30},
            30.0,
        ),
        (
            {"retry": {"max_attempts": 3, "backoff_seconds": 2}, "method": "GET"},
            1,
            None,
            {"status": 429, "retry_after": 999},
            300.0,
        ),
    ],
)
def test_retry_delay(spec, attempt, error, result, expected):
    assert http_node.retry_delay(spec, attempt, error, result) == expected


def test_retry_and_pagination_options_are_bounded():
    assert http_node._retry("x") is None and http_node._retry({"max_attempts": 9}) is None
    assert http_node._retry({"max_attempts": 3, "backoff_seconds": 9999}) == {"max_attempts": 3, "backoff_seconds": 2}
    pg = http_node._pagination(
        {"type": "page", "max_pages": 999, "page_size": 20000, "items_path": " d ", "page_param": 5}
    )
    assert pg == {"type": "page", "max_pages": 10, "items_path": "d"}


def test_simulated_output_with_sample_and_file():
    spec = {"method": "GET", "url": "https://h.test/x", "sample": {"a": 1}}
    out = http_node.simulated_output(spec)
    assert out["simulated"] and out["body"] == {"a": 1} and out["request"]["url"] == "https://h.test/x"
    assert http_node.simulated_output({**spec, "sample": None})["body"] == {}
    assert http_node.simulated_output({**spec, "save_file": {"name": "n"}})["file"]["name"] == "n"


def test_fail_if_turns_a_200_into_an_error():
    ok = {"body": {"ok": True}}
    assert http_node._fail_if({"path": "ok", "is": False}, ok) is None
    err = http_node._fail_if(
        {"path": "ok", "is": False, "message_path": "error"}, {"body": {"ok": False, "error": "nope"}}
    )
    assert err["code"] == "API_ERROR" and "nope" in err["message"]
    present = http_node._fail_if({"path": "error", "present": True, "message_path": "absent"}, {"body": {"error": "x"}})
    assert present["code"] == "API_ERROR" and "refus sans détail" in present["message"]


def test_dig_and_with_query_and_next_link():
    assert http_node._dig({"a": [{"b": 5}]}, "a.0.b") == 5 and http_node._dig({"a": 1}, "a.b") is None
    assert http_node._with_query("https://h.test/x?a=1", {"b": 2}) == "https://h.test/x?a=1&b=2"
    assert http_node._next_link('<https://h/p2>; rel=next, <https://h/p9>; rel="last"') == "https://h/p2"
    assert http_node._next_link('<https://h/p9>; rel="last"') is None


# ---- execute_spec : de la connexion à l'audit


@pytest.fixture
def exec_env(monkeypatch):
    from vault import auth

    audited, sent = [], []
    creds = SimpleNamespace(headers={"Authorization": "Bearer s"}, query={"k": "v"}, secrets=["s"], signer=None)
    monkeypatch.setattr(http_node, "find_connection", lambda *a: SimpleNamespace(name="svc", config={}))
    monkeypatch.setattr(auth, "credentials", lambda conn: creds)
    monkeypatch.setattr(http_node, "record", lambda *a, **k: audited.append(k))

    def send(spec, url, headers, secrets, signer=None):
        sent.append((url, headers, spec))
        return {"status": 200, "ok": True, "headers": {}, "body": {"ok": False}, "simulated": False}, None, {}

    monkeypatch.setattr(http_node, "_send", send)
    return SimpleNamespace(audited=audited, sent=sent, creds=creds, auth=auth, monkeypatch=monkeypatch)


def _spec(**kw):
    return {"connection": "svc", "method": "GET", "url": "https://h.test/x", "headers": {"X": "1"}, "timeout": 5, **kw}


def test_execute_adds_credentials_audits_and_applies_fail_if(exec_env):
    ws = SimpleNamespace(pk=1)
    result, error = http_node.execute_spec(_spec(fail_if={"path": "ok", "is": False}), ws, None, None, step_id="s1")
    url, headers, _ = exec_env.sent[0]
    assert url == "https://h.test/x?k=v" and headers == {"X": "1", "Authorization": "Bearer s"}
    assert error["code"] == "API_ERROR" and result["status"] == 200
    assert exec_env.audited[0]["host"] == "h.test" and exec_env.audited[0]["pages"] == 1


def test_execute_unknown_connection_and_vault_refusal(exec_env):
    exec_env.monkeypatch.setattr(http_node, "find_connection", lambda *a: None)
    assert http_node.execute_spec(_spec(), SimpleNamespace(pk=1), None, None)[1]["code"] == "CONNECTION_MISSING"
    exec_env.monkeypatch.setattr(http_node, "find_connection", lambda *a: SimpleNamespace(name="svc", config={}))

    def refuse(conn):
        raise exec_env.auth.AuthError("VAULT_DOWN", "coffre indisponible")

    exec_env.monkeypatch.setattr(exec_env.auth, "credentials", refuse)
    _, error = http_node.execute_spec(_spec(), SimpleNamespace(pk=1), None, None)
    assert error["code"] == "VAULT_DOWN" and exec_env.sent == []


def test_execute_files_need_a_run(exec_env):
    for extra in ({"body_file": "f1"}, {"parts": {"fields": {}, "files": []}}, {"save_file": {"name": "n"}}):
        result, error = http_node.execute_spec(_spec(**extra), SimpleNamespace(pk=1), None, None)
        assert result is None and error["code"] == "FILE_MISSING"
    assert exec_env.sent == []


def test_execute_with_pagination_reports_pages(exec_env):
    spec = _spec(pagination={"type": "page", "max_pages": 1})
    result, error = http_node.execute_spec(spec, SimpleNamespace(pk=1), None, None)
    assert error is None and result["pages"] == 1 and exec_env.audited[0]["pages"] == 1
