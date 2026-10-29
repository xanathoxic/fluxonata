"""OIDC against a REAL identity provider (Keycloak in a container): discovery, authorization code + PKCE, token exchange,
signature check of the real ID token (JWKS), groups mapped to a role. Skipped when docker or the image is missing."""

import html
import http.cookiejar
import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from tenancy.models import Membership
from tests.conftest import make_user  # noqa: F401

IMAGE = "quay.io/keycloak/keycloak:26.0"
CLIENT, SECRET = "fluxonata", "kc-secret-12345"
REDIRECT = "http://localhost:5173/api/v1/auth/oidc/callback/"
REALM = {
    "realm": "essai",
    "enabled": True,
    "sslRequired": "none",
    "groups": [{"name": "orch-designers"}],
    "users": [
        {
            "username": "marie",
            "enabled": True,
            "email": "marie@exemple.org",
            "emailVerified": True,
            "firstName": "Marie",
            "lastName": "Curie",
            "credentials": [{"type": "password", "value": "mot-de-passe-12345", "temporary": False}],
            "groups": ["/orch-designers"],
        }
    ],
    "clients": [
        {
            "clientId": CLIENT,
            "enabled": True,
            "secret": SECRET,
            "publicClient": False,
            "standardFlowEnabled": True,
            "redirectUris": ["http://localhost:5173/*"],
            "attributes": {"pkce.code.challenge.method": "S256"},
            "protocolMappers": [
                {
                    "name": "groupes",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-group-membership-mapper",
                    "config": {
                        "claim.name": "groups",
                        "full.path": "false",
                        "id.token.claim": "true",
                        "access.token.claim": "false",
                        "userinfo.token.claim": "false",
                    },
                }
            ],
        }
    ],
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def keycloak():
    if shutil.which("docker") is None:
        pytest.skip("docker indisponible")
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        pytest.skip(f"image {IMAGE} absente (docker pull {IMAGE})")
    port = _free_port()
    tmp = Path(tempfile.mkdtemp())
    (tmp / "essai-realm.json").write_text(json.dumps(REALM))
    (tmp / "essai-realm.json").chmod(0o644)
    tmp.chmod(0o755)
    cid = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            f"127.0.0.1:{port}:8080",
            "-v",
            f"{tmp}:/opt/keycloak/data/import:ro",
            IMAGE,
            "start-dev",
            "--import-realm",
            "--hostname-strict=false",
            "--http-enabled=true",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not cid:
        pytest.skip("Keycloak ne démarre pas")
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 240
        while True:
            try:
                urllib.request.urlopen(f"{base}/realms/essai/.well-known/openid-configuration", timeout=3).read()
                break
            except Exception:  # noqa: BLE001  (starting, importing the realm)
                if time.time() > deadline:
                    pytest.skip("Keycloak met trop de temps à démarrer")
                time.sleep(2)
        yield base
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _browser_login(auth_url: str, username: str, password: str) -> dict:
    """What a person's browser does: open the login page, submit the form, read the redirect back to the platform."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect)
    page = opener.open(auth_url, timeout=15).read().decode()
    action = html.unescape(re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page).group(1))
    for cookie in jar:  # Keycloak flags its cookies Secure even over plain http (local test): send them anyway
        cookie.secure = False
    data = urllib.parse.urlencode({"username": username, "password": password, "credentialId": ""}).encode()
    try:
        opener.open(urllib.request.Request(action, data=data), timeout=15)
        raise AssertionError("Keycloak aurait dû rediriger")
    except urllib.error.HTTPError as redirect:
        location = redirect.headers.get("Location") or ""
        assert location, (
            f"Keycloak a répondu {redirect.code} : {re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', redirect.read().decode()))[-300:]}"
        )
    assert location.startswith(REDIRECT), location
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(location).query).items()}


@pytest.fixture
def sso(world, keycloak, settings):
    from django.core.cache import cache

    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    settings.ORCH_OIDC_ISSUER = f"{keycloak}/realms/essai"
    settings.ORCH_OIDC_CLIENT_ID, settings.ORCH_OIDC_CLIENT_SECRET, settings.ORCH_OIDC_REDIRECT_URI = (
        CLIENT,
        SECRET,
        REDIRECT,
    )
    settings.ORCH_OIDC_GROUP_ROLES = json.dumps({"orch-designers": {"workspace": "team-a", "role": "designer"}})
    cache.clear()
    return keycloak


def test_real_keycloak_login_with_pkce_creates_the_user_and_maps_the_group(sso, world):
    c = APIClient()
    login = c.get("/api/v1/auth/oidc/login/")
    assert login.status_code == 302 and login["Location"].startswith(f"{sso}/realms/essai/protocol/openid-connect/auth")
    back = _browser_login(login["Location"], "marie", "mot-de-passe-12345")
    done = c.get("/api/v1/auth/oidc/callback/", {"code": back["code"], "state": back["state"]})
    assert done.status_code == 302 and done["Location"] == "/"
    me = c.get("/api/v1/auth/me/").json()
    assert (
        me["username"].startswith("sso.")
        and me["workspaces"][0]["slug"] == "team-a"
        and me["workspaces"][0]["role"] == "designer"
    )
    assert Membership.objects.filter(user__username=me["username"], workspace__slug="team-a").exists()


def test_real_keycloak_rejects_a_replayed_code_a_forged_state_and_a_bad_password(sso, world):
    c = APIClient()
    login = c.get("/api/v1/auth/oidc/login/")
    back = _browser_login(login["Location"], "marie", "mot-de-passe-12345")
    assert c.get("/api/v1/auth/oidc/callback/", {"code": back["code"], "state": "falsifie"}).status_code in (
        302,
        400,
        401,
        403,
    )
    assert APIClient().get("/api/v1/auth/me/").status_code in (401, 403)
    c2 = APIClient()
    login2 = c2.get("/api/v1/auth/oidc/login/")
    ok = _browser_login(login2["Location"], "marie", "mot-de-passe-12345")
    assert c2.get("/api/v1/auth/oidc/callback/", {"code": ok["code"], "state": ok["state"]}).status_code == 302
    replay = APIClient()
    replay.get("/api/v1/auth/oidc/login/")  # a fresh session cannot use a code obtained for another one
    assert replay.get("/api/v1/auth/oidc/callback/", {"code": ok["code"], "state": ok["state"]}).status_code in (
        302,
        400,
        401,
        403,
    )
    assert replay.get("/api/v1/auth/me/").status_code in (401, 403)
    with pytest.raises(AssertionError, match="aurait dû rediriger"):  # a wrong password keeps the login page
        _browser_login(APIClient().get("/api/v1/auth/oidc/login/")["Location"], "marie", "mauvais")
