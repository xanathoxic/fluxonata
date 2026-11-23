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


@pytest.fixture
def redirecting(settings):
    """Un serveur d'identité qui redirige sa racine (302) vers une page à jeton, et répond 200 sur le chemin final."""

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/realms/demo"):
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(302)
            self.send_header(
                "Location", "https://user:pw@sso.example:8443/admin/master/console/?token=SECRETVALUE#frag"
            )
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def test_a_redirect_is_explained_with_its_destination_and_never_leaks_the_query(world, redirecting):
    c = client_for(world["designer"])
    conn = mk_conn(c, kind="http_bearer", config={"base_url": redirecting})
    r = c.post(f"{BASE}/connections/{conn['id']}/test/").json()
    assert r["ok"] is False and "HTTP 302" in r["message"]
    assert "https://sso.example:8443/admin/master/console/" in r["message"] and "chemin de test" in r["message"]
    assert "SECRETVALUE" not in str(r) and "user:pw" not in str(r) and "frag" not in r["message"]


def test_the_redirect_message_is_cured_by_a_test_path_that_answers_directly(world, redirecting):
    c = client_for(world["designer"])
    conn = mk_conn(c, kind="http_bearer", config={"base_url": redirecting, "test_path": "/realms/demo"})
    assert c.post(f"{BASE}/connections/{conn['id']}/test/").json()["ok"] is True


@pytest.mark.parametrize("status", [301, 303, 307, 308])
def test_every_redirect_status_gets_the_explanation(status):
    from vault.views import _verdict

    ok, message = _verdict(status, {}, {"Location": "/ailleurs"})
    assert ok is False and f"HTTP {status}" in message and "/ailleurs" in message


def test_a_redirect_without_location_still_explains():
    from vault.views import _verdict

    ok, message = _verdict(302, {}, {})
    assert ok is False and "redirige (HTTP 302)" in message and "vers" not in message


@pytest.fixture
def idp_like(settings):
    """Un serveur d'identité : /token délivre un jeton ; /admin/realms répond 200 avec ce jeton ; toute autre adresse (dont /admin) redirige vers sa console."""
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def _go(self):
            seen.append(self.path)
            if self.path == "/token":
                body = b'{"access_token": "at-opaque-xyz", "expires_in": 300, "token_type": "Bearer"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/admin/realms" and self.headers.get("Authorization") == "Bearer at-opaque-xyz":
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(302)
            self.send_header("Location", "/admin/master/console/")
            self.end_headers()

        do_GET = do_POST = _go

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


@pytest.mark.parametrize("kind", ["oauth2_client", "oauth2_code"])
def test_oauth2_connections_keep_their_test_path(world, kind):
    c = client_for(world["designer"])
    config = {
        "base_url": "https://8.8.8.8/admin",
        "token_url": "https://8.8.8.8/token",
        "client_id": "cid",
        "test_path": "/realms",
    }
    if kind == "oauth2_code":
        config["authorize_url"] = "https://8.8.8.8/authorize"
    body = {"name": f"c-{kind}", "label": "C", "kind": kind, "config": config, "secret_value": "abcdefgh"}
    created = c.post(f"{BASE}/connections/", body, format="json")
    assert (
        created.status_code == 201 and created.json()["config"]["test_path"] == "/realms"
    )  # plus jamais écarté sans erreur
    bad = c.post(
        f"{BASE}/connections/", {**body, "name": "mauvais", "config": {**config, "test_path": "realms"}}, format="json"
    )
    assert bad.status_code == 400 and "test_path" in str(bad.json())


def test_an_oauth2_connection_is_tested_on_its_test_path_with_the_token(world, idp_like):
    base, seen = idp_like
    c = client_for(world["designer"])
    cfg = {"base_url": f"{base}/admin", "token_url": f"{base}/token", "client_id": "cid"}
    blind = mk_conn(c, name="sans-chemin", kind="oauth2_client", config=cfg)
    r = c.post(f"{BASE}/connections/{blind['id']}/test/").json()
    assert r["ok"] is False and "redirige" in r["message"] and "/admin/master/console/" in r["message"]
    sure = mk_conn(c, name="avec-chemin", kind="oauth2_client", config={**cfg, "test_path": "/realms"})
    r = c.post(f"{BASE}/connections/{sure['id']}/test/").json()
    assert r["ok"] is True and "/admin/realms" in seen and "at-opaque-xyz" not in str(r)
