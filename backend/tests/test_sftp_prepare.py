"""Étape SFTP : validation à la préparation, sortie simulée, refus avant toute connexion (aucun serveur)."""

from types import SimpleNamespace

import pytest

from runs import sftp_node
from runs.sftp_node import SftpStepError


def _res(v, ctx):
    return v


@pytest.fixture(autouse=True)
def _connection(monkeypatch):
    monkeypatch.setattr(sftp_node, "find_connection", lambda *a: SimpleNamespace(name="srv"))


def _prep(**kw):
    return sftp_node.prepare({"connection": "srv", **kw}, _res, {}, 1, None)


def test_paths_are_normalised_under_the_root_and_bounded():
    assert sftp_node.clean_path("a/../b/./c") == "/b/c"
    assert sftp_node.clean_path("/x//y/") == "/x/y"
    for bad in ("", "  ", "a\x00b", "x" * 501, None):
        with pytest.raises(SftpStepError) as exc:
            sftp_node.clean_path(bad)
        assert exc.value.code == "SFTP_CONFIG"


def test_prepare_nominal_and_defaults():
    spec = _prep(path="/in/a.txt", operation="read", binary=True, mode="reel", timeout_seconds=30)
    assert spec["binary"] is True and spec["mode"] == "reel" and spec["timeout"] == 30 and spec["path"] == "/in/a.txt"
    spec = _prep(path="/", timeout_seconds=999)
    assert spec["operation"] == "list" and spec["mode"] == "test" and spec["timeout"] == 20 and spec["binary"] is False


@pytest.mark.parametrize(
    "node",
    [
        {"operation": "chmod"},
        {"operation": "write", "path": "/a", "file": {"nope": 1}},
        {"operation": "write", "path": "/a", "content": "x" * (sftp_node.MAX_BYTES + 1)},
        {"operation": "list", "path": ""},
    ],
)
def test_prepare_rejects(node):
    with pytest.raises(SftpStepError) as exc:
        _prep(**node)
    assert exc.value.code == "SFTP_CONFIG"


def test_write_content_is_turned_into_text():
    assert _prep(operation="write", path="/a", content=42)["content"] == "42"


def test_prepare_without_connection(monkeypatch):
    monkeypatch.setattr(sftp_node, "find_connection", lambda *a: None)
    with pytest.raises(SftpStepError) as exc:
        _prep(path="/a")
    assert exc.value.code == "CONNECTION_MISSING"


def test_simulated_outputs_follow_the_operation():
    base = {"path": "/dir/f.bin", "simulated": True}
    assert sftp_node.simulated_output({"operation": "list", "path": "/dir"})["entries"] == []
    assert sftp_node.simulated_output({"operation": "read", "path": "/f"})["content"] == ""
    assert sftp_node.simulated_output({"operation": "write", "path": "/f"})["content"] is None
    binary = sftp_node.simulated_output({"operation": "read", "path": base["path"], "binary": True})
    assert binary["content"] is None and binary["file"]["name"] == "f.bin" and binary["file"]["file_id"] is None


def test_files_are_only_usable_inside_a_run():
    ws = SimpleNamespace(pk=1)
    for extra in ({"file_id": "f1"}, {"binary": True}):
        result, err = sftp_node.execute_spec({"connection": "srv", **extra}, ws, None, None)
        assert result is None and err["code"] == "FILE_MISSING"


def test_a_connection_without_secret_is_refused(monkeypatch):
    monkeypatch.setattr(sftp_node, "find_connection", lambda *a: SimpleNamespace(name="srv", secret=None))
    result, err = sftp_node.execute_spec({"connection": "srv"}, SimpleNamespace(pk=1), None, None)
    assert result is None and err["code"] == "CONNECTION_MISSING"
