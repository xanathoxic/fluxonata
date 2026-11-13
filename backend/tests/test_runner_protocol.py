"""Garde-fous qui ne dépendent plus d'assertions : version du protocole d'un résultat d'action, pagination sans page lue."""

import json

import pytest

from runs import http_node
from runs.runners import _parse


def envelope(**kw):
    return json.dumps({"protocol": 1, "ok": True, "result": {"x": 1}, **kw}).encode()


def test_result_with_protocol_1_is_accepted():
    out = _parse(envelope(), b"", 0)
    assert out.ok and out.result == {"x": 1}


@pytest.mark.parametrize(
    "raw",
    [json.dumps({"protocol": 2, "ok": True, "result": {}}).encode(), json.dumps({"ok": True}).encode(), b"pas du json"],
)
def test_other_protocol_versions_and_garbage_are_a_protocol_error(raw):
    out = _parse(raw, b"", 0)
    assert not out.ok and out.error["code"] == "PROTOCOL_ERROR"


def test_pagination_reads_pages_until_an_empty_one(monkeypatch):
    pages = iter([{"ok": True, "body": {"rows": [1, 2]}}, {"ok": True, "body": {"rows": []}}])
    monkeypatch.setattr(http_node, "_send", lambda *a, **k: (next(pages), None, {}))
    spec = {"pagination": {"type": "page", "items_path": "rows", "max_pages": 5}}
    result, error = http_node._paginate(spec, "https://example.invalid/x", {}, [])
    assert error is None and result["body"] == [1, 2] and result["pages"] == 2


def test_pagination_without_any_page_is_an_explicit_error_not_an_assertion(monkeypatch):
    monkeypatch.setattr(http_node, "_send", lambda *a, **k: pytest.fail("aucune page ne doit être demandée"))
    spec = {"pagination": {"type": "page", "max_pages": 0}}
    result, error = http_node._paginate(spec, "https://example.invalid/x", {}, [])
    assert result is None and error["code"] == "PAGINATION_EMPTY"
