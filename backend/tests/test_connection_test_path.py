"""« Tester » une connexion : chemin de test facultatif (la racine d'une API répond souvent 404, ex. GitLab /api/v4/)."""

import http.server
import threading

import pytest

from tests.conftest import client_for
from tests.test_connections import BASE, SECRET, mk_conn
from vault.views import probe_url


@pytest.fixture
def gitlab_like(settings):
    """Un serveur qui répond 404 à la racine de l'API et 200 sur /user avec le bon jeton, comme GitLab."""
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            ok = self.path == "/api/v4/user" and self.headers.get("Authorization") == f"Bearer {SECRET}"
            self.send_response(200 if ok else 404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/v4/", seen
    srv.shutdown()


def test_without_test_path_a_404_on_the_root_explains_what_to_do(world, gitlab_like):
    url, seen = gitlab_like
    c = client_for(world["designer"])
    conn = mk_conn(c, kind="http_bearer", config={"base_url": url})
    r = c.post(f"{BASE}/connections/{conn['id']}/test/").json()
    assert r["ok"] is False and "chemin de test" in r["message"] and seen == ["/api/v4/"]


def test_with_a_test_path_the_connection_is_checked_on_that_path(world, gitlab_like):
    url, seen = gitlab_like
    c = client_for(world["designer"])
    conn = mk_conn(c, kind="http_bearer", config={"base_url": url, "test_path": "/user"})
    r = c.post(f"{BASE}/connections/{conn['id']}/test/").json()
    assert r["ok"] is True and seen == ["/api/v4/user"] and SECRET not in str(r)


def test_a_wrong_test_path_still_fails_without_the_hint(world, gitlab_like):
    url, _ = gitlab_like
    c = client_for(world["designer"])
    conn = mk_conn(c, kind="http_bearer", config={"base_url": url, "test_path": "/inconnu"})
    r = c.post(f"{BASE}/connections/{conn['id']}/test/").json()
    assert r["ok"] is False and "HTTP 404" in r["message"] and "chemin de test" not in r["message"]


@pytest.mark.parametrize(
    "bad", ["user", "http://autre.example/x", "//autre.example/x", "/a/../b", "/a b", "/" + "a" * 200]
)
def test_a_test_path_must_be_a_simple_relative_path(world, bad):
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/connections/",
        {
            "name": "x",
            "label": "x",
            "kind": "http_bearer",
            "secret_value": SECRET,
            "config": {"base_url": "https://8.8.8.8/", "test_path": bad},
        },
        format="json",
    )
    assert r.status_code == 400 and "test_path" in str(r.json())


def test_the_test_address_joins_the_base_and_the_path_with_one_slash():
    assert probe_url({"base_url": "https://g.example/api/v4/", "test_path": "/user"}) == "https://g.example/api/v4/user"
    assert probe_url({"base_url": "https://g.example/api/v4", "test_path": "/user"}) == "https://g.example/api/v4/user"
    assert probe_url({"base_url": "https://g.example/api/v4/"}) == "https://g.example/api/v4/"


def test_the_gitlab_catalog_entry_presets_the_test_path():
    from catalog.integrations.catalog import INTEGRATIONS

    gl = next(i for i in INTEGRATIONS if i["slug"] == "gitlab")
    assert gl["connection"]["config"]["test_path"] == "/user"
