"""SSO configuré dans l'interface (fournisseurs en base) contre un VRAI Keycloak en conteneur : OpenID Connect (client confidentiel, PKCE,
groupes → rôles) et SAML 2.0 (métadonnées importées, assertion signée par Keycloak, groupes → rôles). Ignoré sans docker ni image.

C'est l'essai d'interopérabilité que le fournisseur simulé de `test_sso_saml.py` ne peut pas faire : ici la signature n'est pas produite par la
bibliothèque qui la vérifie."""

import base64
import html
import http.cookiejar
import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from tenancy import sso_config, sso_saml
from tenancy.models import GroupRule, IdentityProvider, Membership, Role
from tests.test_oidc_keycloak import IMAGE, _free_port, _NoRedirect

REALM_NAME = "essai-sso"
OIDC_CLIENT, OIDC_SECRET = "fluxonata-db", "kc-secret-db-12345"
SAML_SLUG, OIDC_SLUG = "kc-saml", "kc-oidc"
SP_ENTITY = f"http://testserver/api/v1/auth/sso/{SAML_SLUG}/metadata/"
PASSWORD = "mot-de-passe-12345"


def _user(name, groups):
    return {
        "username": name,
        "enabled": True,
        "email": f"{name}@exemple.org",
        "emailVerified": True,
        "firstName": name.capitalize(),
        "lastName": "Test",
        "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}],
        "groups": groups,
    }


def _saml_mapper(name, mapper, config):
    return {"name": name, "protocol": "saml", "protocolMapper": mapper, "config": config}


REALM = {
    "realm": REALM_NAME,
    "enabled": True,
    "sslRequired": "none",
    "groups": [{"name": "orch-designers"}, {"name": "orch-viewers"}],
    "users": [_user("marie", ["/orch-designers"]), _user("paul", [])],
    "clients": [
        {
            "clientId": OIDC_CLIENT,
            "enabled": True,
            "secret": OIDC_SECRET,
            "publicClient": False,
            "standardFlowEnabled": True,
            "redirectUris": ["http://testserver/*"],
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
        },
        {
            "clientId": SP_ENTITY,
            "protocol": "saml",
            "enabled": True,
            "redirectUris": ["http://testserver/*"],
            "attributes": {
                "saml.assertion.signature": "true",
                "saml.server.signature": "false",
                "saml.client.signature": "false",
                "saml.authnstatement": "true",
                "saml.force.post.binding": "true",
                "saml.signature.algorithm": "RSA_SHA256",
                "saml_name_id_format": "persistent",
                "saml.signing.certificate": "",
            },
            "protocolMappers": [
                _saml_mapper(
                    "uid",
                    "saml-user-property-mapper",
                    {"user.attribute": "username", "attribute.name": "uid", "attribute.nameformat": "Basic"},
                ),
                _saml_mapper(
                    "mail",
                    "saml-user-property-mapper",
                    {"user.attribute": "email", "attribute.name": "mail", "attribute.nameformat": "Basic"},
                ),
                _saml_mapper(
                    "name",
                    "saml-user-property-mapper",
                    {"user.attribute": "firstName", "attribute.name": "displayName", "attribute.nameformat": "Basic"},
                ),
                _saml_mapper(
                    "groupes",
                    "saml-group-membership-mapper",
                    {
                        "attribute.name": "memberOf",
                        "attribute.nameformat": "Basic",
                        "single": "false",
                        "full.path": "false",
                    },
                ),
            ],
        },
    ],
}


@pytest.fixture(scope="module")
def keycloak():
    if shutil.which("docker") is None:
        pytest.skip("docker indisponible")
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        pytest.skip(f"image {IMAGE} absente (docker pull {IMAGE})")
    port = _free_port()
    tmp = Path(tempfile.mkdtemp())
    (tmp / "sso-realm.json").write_text(json.dumps(REALM))
    (tmp / "sso-realm.json").chmod(0o644)
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
                urllib.request.urlopen(f"{base}/realms/{REALM_NAME}/.well-known/openid-configuration", timeout=3).read()
                break
            except Exception:  # noqa: BLE001  (démarrage, import du domaine)
                if time.time() > deadline:
                    pytest.skip("Keycloak met trop de temps à démarrer")
                time.sleep(2)
        yield base
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _open(start_url: str, username: str, password: str):
    """Ce que fait un navigateur : ouvrir l'adresse du fournisseur, remplir le formulaire, lire la réponse ; (redirection, page) selon le cas."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect)
    page = opener.open(start_url, timeout=15).read().decode()
    action = html.unescape(re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page).group(1))
    for cookie in jar:  # Keycloak marque ses cookies « Secure » même en http (essai local)
        cookie.secure = False
    data = urllib.parse.urlencode({"username": username, "password": password, "credentialId": ""}).encode()
    try:
        resp = opener.open(urllib.request.Request(action, data=data), timeout=15)
        return None, resp.read().decode()
    except urllib.error.HTTPError as redirect:
        return redirect.headers.get("Location") or "", redirect.read().decode()


@pytest.fixture
def world_sso(world, keycloak, settings):
    cache.clear()
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    return keycloak


# ---- OpenID Connect ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def oidc(world_sso, world):
    cfg, problems = sso_config.clean("oidc", {"issuer": f"{world_sso}/realms/{REALM_NAME}", "client_id": OIDC_CLIENT})
    assert not problems
    p = IdentityProvider(slug=OIDC_SLUG, name="Keycloak OIDC", kind="oidc", enabled=True, config=cfg)
    sso_config.set_secret(p, OIDC_SECRET)
    p.save()
    GroupRule.objects.create(
        provider=p,
        group="orch-designers",
        workspace=world["ws_a"],
        role=Role.DESIGNER,
        grant="workspace",
        user_group="Concepteurs",
    )
    return p


def test_real_keycloak_oidc_provider_from_the_database(oidc, world):
    c = APIClient()
    login = c.get(f"/api/v1/auth/sso/{OIDC_SLUG}/login/")
    assert login.status_code == 302 and login["Location"].startswith(
        f"{oidc.config['issuer']}/protocol/openid-connect/auth"
    )
    location, _ = _open(login["Location"], "marie", PASSWORD)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(location).query).items()}
    done = c.get(f"/api/v1/auth/sso/{OIDC_SLUG}/callback/", {"code": q["code"], "state": q["state"]})
    assert done.status_code == 302 and done["Location"] == "/", done["Location"]
    me = c.get("/api/v1/auth/me/").json()
    assert [(w["slug"], w["role"]) for w in me["workspaces"]] == [("team-a", "designer")]
    m = Membership.objects.get(user__username=me["username"], workspace=world["ws_a"])
    assert m.source == f"sso:{OIDC_SLUG}"
    assert world["ws_a"].user_groups.get(name="Concepteurs").members.filter(username=me["username"]).exists()


def test_real_keycloak_oidc_test_button_reads_discovery_and_keys(oidc):
    from tenancy import sso_oidc

    out = sso_oidc.test(oidc)
    assert out["issuer"] == oidc.config["issuer"] and out["signing_keys"] >= 1 and out["pkce_supported"] is True


# ---- SAML -------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def saml(world_sso, world):
    raw = urllib.request.urlopen(f"{world_sso}/realms/{REALM_NAME}/protocol/saml/descriptor", timeout=10).read()
    meta = sso_saml.read_idp_metadata(raw)  # les métadonnées RÉELLES de Keycloak
    cfg, problems = sso_config.clean("saml", {**meta, "name_id_format": ""})
    assert not problems, problems
    p = IdentityProvider.objects.create(slug=SAML_SLUG, name="Keycloak SAML", kind="saml", enabled=True, config=cfg)
    GroupRule.objects.create(
        provider=p, group="orch-designers", workspace=world["ws_a"], role=Role.DESIGNER, grant="workspace"
    )
    return p


def _saml_response(page: str) -> tuple[str, str]:
    form = re.search(r"<form[^>]*method=\"post\"[^>]*>(.*?)</form>", page, re.S | re.I)
    assert form, page[:400]
    saml = html.unescape(re.search(r'name="SAMLResponse"\s+value="([^"]+)"', form.group(1)).group(1))
    relay = re.search(r'name="RelayState"\s+value="([^"]*)"', form.group(1))
    return saml, html.unescape(relay.group(1)) if relay else ""


def test_real_keycloak_saml_login_verifies_the_real_signature_and_maps_the_group(saml, world):
    c = APIClient()
    login = c.get(f"/api/v1/auth/sso/{SAML_SLUG}/login/")
    assert login.status_code == 302 and "SAMLRequest=" in login["Location"]
    _, page = _open(login["Location"], "marie", PASSWORD)
    response, relay = _saml_response(page)
    xml = base64.b64decode(response)
    assert b"Signature" in xml  # Keycloak a bien signé l'assertion
    done = c.post(f"/api/v1/auth/sso/{SAML_SLUG}/acs/", {"SAMLResponse": response, "RelayState": relay})
    assert done.status_code == 302 and done["Location"] == "/", done["Location"]
    me = c.get("/api/v1/auth/me/").json()
    assert [(w["slug"], w["role"]) for w in me["workspaces"]] == [("team-a", "designer")]
    assert Membership.objects.get(user__username=me["username"]).source == f"sso:{SAML_SLUG}"
    # la même réponse rejouée dans une autre session est refusée
    other = APIClient()
    other.get(f"/api/v1/auth/sso/{SAML_SLUG}/login/")
    replay = other.post(f"/api/v1/auth/sso/{SAML_SLUG}/acs/", {"SAMLResponse": response, "RelayState": relay})
    assert replay["Location"].startswith("/?sso_error=")


def test_real_keycloak_saml_tampered_and_foreign_certificate_are_refused(saml, world):
    c = APIClient()
    _, page = _open(c.get(f"/api/v1/auth/sso/{SAML_SLUG}/login/")["Location"], "marie", PASSWORD)
    response, relay = _saml_response(page)
    tampered = base64.b64encode(base64.b64decode(response).replace(b"orch-designers", b"orch-viewers")).decode()
    bad = c.post(f"/api/v1/auth/sso/{SAML_SLUG}/acs/", {"SAMLResponse": tampered, "RelayState": relay})
    assert (
        bad["Location"].startswith("/?sso_error=") and not Membership.objects.filter(source=f"sso:{SAML_SLUG}").exists()
    )
    # un autre certificat épinglé : la signature de Keycloak n'est plus reconnue
    from tests.test_sso_saml import ROGUE_CERT

    saml.config = {**saml.config, "idp_certs": [ROGUE_CERT]}
    saml.save()
    c2 = APIClient()
    _, page2 = _open(c2.get(f"/api/v1/auth/sso/{SAML_SLUG}/login/")["Location"], "marie", PASSWORD)
    response2, relay2 = _saml_response(page2)
    refused = c2.post(f"/api/v1/auth/sso/{SAML_SLUG}/acs/", {"SAMLResponse": response2, "RelayState": relay2})
    assert refused["Location"].startswith("/?sso_error=")


def test_real_keycloak_saml_person_without_group_gets_no_access(saml, world):
    c = APIClient()
    _, page = _open(c.get(f"/api/v1/auth/sso/{SAML_SLUG}/login/")["Location"], "paul", PASSWORD)
    response, relay = _saml_response(page)
    assert (
        c.post(f"/api/v1/auth/sso/{SAML_SLUG}/acs/", {"SAMLResponse": response, "RelayState": relay})["Location"] == "/"
    )
    assert c.get("/api/v1/auth/me/").json()["workspaces"] == []
