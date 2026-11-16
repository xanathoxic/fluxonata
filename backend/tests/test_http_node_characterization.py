"""Comportement de `runs.http_node.prepare` et `_paginate` (tests de caractérisation, écrits avant le découpage)."""

from types import SimpleNamespace

import pytest

from runs import http_node
from runs.http_node import HttpStepError

BASE = "https://api.example.test/v1"


@pytest.fixture
def conn(monkeypatch):
    c = SimpleNamespace(name="svc", config={"base_url": BASE + "/"})
    monkeypatch.setattr(http_node, "find_connection", lambda *a: c)
    return c


def _prep(node, conn_=None):
    return http_node.prepare({"connection": "svc", **node}, lambda v, ctx: v, {}, 1, None)


def _code(node):
    with pytest.raises(HttpStepError) as exc:
        _prep(node)
    return exc.value.code


def test_prepare_nominal_json(conn):
    spec = _prep({"method": "post", "path": "/items", "query": {"a": 1}, "body": {"x": 1}, "headers": {"X-Id": "7"}})
    assert spec["url"] == BASE + "/items?a=1"
    assert spec["method"] == "POST" and spec["mode"] == "test" and spec["timeout"] == 15
    assert spec["body"] == '{"x": 1}'
    assert spec["headers"] == {"X-Id": "7", "Content-Type": "application/json"}
    assert spec["fail_on_status"] is True and spec["pagination"] is None and spec["parts"] is None


def test_prepare_missing_connection(monkeypatch):
    monkeypatch.setattr(http_node, "find_connection", lambda *a: None)
    assert _code_no_conn() == "CONNECTION_MISSING"


def _code_no_conn():
    with pytest.raises(HttpStepError) as exc:
        http_node.prepare({"connection": "svc"}, lambda v, c: v, {}, 1, None)
    return exc.value.code


def test_prepare_connection_without_base_url(monkeypatch):
    monkeypatch.setattr(http_node, "find_connection", lambda *a: SimpleNamespace(name="s", config={}))
    assert _code_no_conn() == "CONNECTION_MISSING"


@pytest.mark.parametrize(
    "node",
    [
        {"method": "TRACE"},
        {"path": "items"},
        {"path": "/a b"},
        {"path": "/a\\b"},
        {"path": "/x", "query": [1]},
        {"path": "/x", "headers": ["a"]},
        {"path": "/x", "headers": {f"h{i}": "v" for i in range(21)}},
        {"path": "/x", "headers": {"Host": "evil"}},
        {"path": "/x", "headers": {"bad name": "v"}},
        {"path": "/x", "headers": {"X-A": "a\nb"}},
        {"path": "/x", "body_format": "file"},
        {"path": "/x", "body": "z" * (http_node.MAX_BODY + 1)},
        {"path": "/x", "response_file": {"name": "a"}, "pagination": {"type": "page"}},
        {"path": "/x", "pagination": {"type": "nope"}},
        {"path": "/x", "pagination": {"type": "cursor"}},
    ],
)
def test_prepare_rejects_bad_requests(conn, node):
    assert _code(node) == "HTTP_CONFIG"


def test_prepare_double_slash_path_stays_on_the_connection_host(conn):
    assert _prep({"path": "//evil.test/x"})["url"] == BASE + "//evil.test/x"


def test_prepare_root_path_keeps_connection_address(conn):
    assert _prep({"path": "/"})["url"] == BASE
    conn.config = {"base_url": "https://h.test"}
    assert _prep({"path": "/", "query": {"q": "1"}})["url"] == "https://h.test/?q=1"


def test_prepare_query_appended_to_existing_query(conn):
    assert _prep({"path": "/x?z=1", "query": {"a": 2}})["url"] == BASE + "/x?z=1&a=2"


def test_prepare_form_body_and_plain_body(conn):
    form = _prep({"path": "/x", "body": {"a": 1}, "body_format": "form"})
    assert form["body"] == "a=1" and form["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    plain = _prep({"path": "/x", "body": "texte", "headers": {"Content-Type": "text/plain"}})
    assert plain["body"] == "texte" and plain["headers"]["Content-Type"] == "text/plain"


def test_prepare_header_value_truncated(conn):
    assert len(_prep({"path": "/x", "headers": {"X-A": "v" * 2000}})["headers"]["X-A"]) == 1000


def test_prepare_multipart_without_files_is_built_inline(conn):
    spec = _prep({"path": "/x", "body": {"a": "1"}, "body_format": "multipart"})
    assert spec["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert spec["parts"] is None and "a" in spec["body"]


def test_prepare_multipart_with_files_is_deferred_to_the_worker(conn):
    spec = _prep({"path": "/x", "body": {"a": "1"}, "body_format": "multipart", "files": [{"file": {"file_id": "f"}}]})
    assert spec["body"] is None and spec["parts"]["fields"] == {"a": "1"}


def test_prepare_options(conn):
    spec = _prep({"path": "/x", "mode": "reel", "timeout_seconds": 30, "fail_on_status": False})
    assert spec["mode"] == "reel" and spec["timeout"] == 30 and spec["fail_on_status"] is False
    assert _prep({"path": "/x", "timeout_seconds": 99})["timeout"] == 15


def _serve(monkeypatch, pages):
    """Réponses successives de `_send` ; renvoie la liste des adresses demandées."""
    queue, asked = list(pages), []

    def send(spec, target, *a, **k):
        asked.append(target)
        return queue.pop(0)

    monkeypatch.setattr(http_node, "_send", send)
    return asked


def _ok(body, link=None):
    return ({"ok": True, "body": body, "status": 200}, None, {"link": link} if link else {})


def test_paginate_page_numbers_and_size(monkeypatch):
    asked = _serve(monkeypatch, [_ok([1, 2]), _ok([3]), _ok([])])
    pg = {"type": "page", "page_start": 3, "page_size": 2, "page_param": "p", "size_param": "n"}
    result, error = http_node._paginate({"pagination": pg}, "https://h.test/x", {}, [])
    assert error is None and result["body"] == [1, 2, 3] and result["pages"] == 3
    assert asked == ["https://h.test/x?p=3&n=2", "https://h.test/x?p=4&n=2", "https://h.test/x?p=5&n=2"]


def test_paginate_offset_stops_on_short_page(monkeypatch):
    asked = _serve(monkeypatch, [_ok([1, 2]), _ok([3])])
    pg = {"type": "offset", "page_size": 2}
    result, _ = http_node._paginate({"pagination": pg}, "https://h.test/x", {}, [])
    assert result["body"] == [1, 2, 3] and result["pages"] == 2
    assert asked == ["https://h.test/x?offset=0&limit=2", "https://h.test/x?offset=2&limit=2"]


def test_paginate_cursor(monkeypatch):
    asked = _serve(monkeypatch, [_ok({"d": [1], "n": "c2"}), _ok({"d": [2], "n": None})])
    pg = {"type": "cursor", "items_path": "d", "next_path": "n"}
    result, _ = http_node._paginate({"pagination": pg}, "https://h.test/x", {}, [])
    assert result["body"] == [1, 2] and asked == ["https://h.test/x", "https://h.test/x?cursor=c2"]


def test_paginate_link_follows_same_host_only(monkeypatch):
    nxt = '<https://h.test/x?page=2>; rel="next"'
    asked = _serve(monkeypatch, [_ok([1], nxt), _ok([2], '<https://evil.test/y>; rel="next"')])
    result, _ = http_node._paginate({"pagination": {"type": "link"}}, "https://h.test/x", {}, [])
    assert result["body"] == [1, 2] and asked == ["https://h.test/x", "https://h.test/x?page=2"]


def test_paginate_truncated_at_max_pages(monkeypatch):
    _serve(monkeypatch, [_ok([1]), _ok([2])])
    result, _ = http_node._paginate({"pagination": {"type": "page", "max_pages": 2}}, "https://h.test/x", {}, [])
    assert result["body"] == [1, 2] and result["truncated"] is True and result["pages"] == 2


def test_paginate_truncated_at_max_items(monkeypatch):
    monkeypatch.setattr(http_node, "MAX_ITEMS", 3)
    _serve(monkeypatch, [_ok([1, 2]), _ok([3, 4])])
    result, _ = http_node._paginate({"pagination": {"type": "page"}}, "https://h.test/x", {}, [])
    assert result["body"] == [1, 2, 3] and result["truncated"] is True


def test_paginate_non_list_items_count_as_empty(monkeypatch):
    _serve(monkeypatch, [_ok({"rows": "x"})])
    result, _ = http_node._paginate({"pagination": {"type": "page", "items_path": "rows"}}, "https://h.test/x", {}, [])
    assert result["body"] == [] and result["pages"] == 1


def test_paginate_failures(monkeypatch):
    err = {"code": "E", "message": "m", "next_step": "n"}
    _serve(monkeypatch, [(None, err, {})])
    assert http_node._paginate({"pagination": {"type": "page"}}, "https://h.test/x", {}, []) == (None, err)
    _serve(monkeypatch, [(None, None, {})])
    _, error = http_node._paginate({"pagination": {"type": "page"}}, "https://h.test/x", {}, [])
    assert error["code"] == "HTTP_ERROR"
    bad = {"ok": False, "status": 500, "body": "x"}
    _serve(monkeypatch, [(bad, None, {})])
    assert http_node._paginate({"pagination": {"type": "page"}}, "https://h.test/x", {}, []) == (bad, None)
