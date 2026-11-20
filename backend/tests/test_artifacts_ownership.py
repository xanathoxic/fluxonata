"""FileSystemStore : dossiers créés en root rendus au propriétaire de la racine ; configuration nginx (compression, cache, police)."""

import os
from pathlib import Path

import pytest

from runs.artifacts import FileSystemStore

ROOT = Path(__file__).resolve().parents[2]


def test_put_creates_nested_directories_and_roundtrips(tmp_path):
    store = FileSystemStore(tmp_path / "a")
    store.put("7/packages/x/1.zip", b"zip")
    assert store.get("7/packages/x/1.zip") == b"zip"


def test_directories_created_as_root_follow_the_owner_of_the_root(tmp_path, monkeypatch):
    store = FileSystemStore(tmp_path / "a")
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda p, u, g: calls.append((Path(p), u, g)))
    store.put("7/packages/x/1.zip", b"zip")
    owner = store.root.stat()
    assert {c[0] for c in calls} == {store.root / "7", store.root / "7/packages", store.root / "7/packages/x"}
    assert all(c[1:] == (owner.st_uid, owner.st_gid) for c in calls)


def test_nothing_is_chowned_when_not_root(tmp_path, monkeypatch):
    store = FileSystemStore(tmp_path / "a")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(os, "chown", lambda *a: pytest.fail("chown inattendu"))
    store.put("7/x.zip", b"z")


def test_existing_directories_are_not_chowned(tmp_path, monkeypatch):
    store = FileSystemStore(tmp_path / "a")
    store.put("7/x.zip", b"z")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda *a: pytest.fail("chown inattendu"))
    store.put("7/y.zip", b"z")


def test_image_creates_the_artifacts_directory():
    assert "mkdir -p /app/var/artifacts" in (ROOT / "infra" / "Dockerfile.backend").read_text()


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
def test_nginx_compresses_static_text_but_not_the_api(conf):
    text = (ROOT / "infra" / conf).read_text()
    assert "gzip on;" in text and "gzip_vary on;" in text
    proxied = text.split("proxy_pass ")[1:]
    assert proxied and all(
        "gzip off;" in chunk.split("}")[0] for chunk in proxied
    )  # aucune route proxy n'est compressée
    assert "application/json" not in text.split("gzip_types")[1].split(";")[0]


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
def test_nginx_caches_hashed_assets_and_keeps_security_headers(conf):
    text = (ROOT / "infra" / conf).read_text()
    block = text.split("location /assets/ {")[1].split("}")[0]
    assert "immutable" in block and "expires 1y" in block
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy"):
        assert header in block


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
def test_csp_allows_embedded_fonts(conf):
    assert "font-src 'self' data:" in (ROOT / "infra" / conf).read_text()
