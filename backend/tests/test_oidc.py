import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from rest_framework.test import APIClient

from audit.models import AuditEvent
from core import netsec
from tenancy.models import Membership, OidcIdentity, Role
from tests.conftest import make_user

ISSUER = "https://idp.example.org/realms/orch"
CLIENT = "fluxonata"


class FakeIdP:
    """In-process OIDC provider: discovery, JWKS and token endpoint (via a patched netsec.safe_request)."""

    def __init__(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})
        self.claims = {
            "sub": "user-42",
            "preferred_username": "marie",
            "email": "marie@example.org",
            "groups": ["orch-designers"],
        }
        self.codes = {}
        self.calls = []

    def _token_for(self, ctx):
        over = dict(ctx.get("over", {}))
        nonce = over.pop("nonce", ctx["nonce"])
        return self.id_token(nonce, **over)

    def id_token(self, nonce, **over):
        claims = {
            "iss": ISSUER,
            "aud": CLIENT,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": nonce,
            **self.claims,
            **over,
        }
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, self.key)

    def __call__(self, method, url, headers=None, body=None, **kw):
        self.calls.append((method, url))
        if url.endswith("/.well-known/openid-configuration"):
            doc = {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/auth",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            }
            return 200, {}, json.dumps(doc).encode()
        if url.endswith("/jwks"):
            return 200, {}, json.dumps(KeySet([self.key]).as_dict(private=False)).encode()
        if url.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            ctx = self.codes.get(form.get("code"))
            if ctx is None:
                return 400, {}, b"{}"
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            )
            if expected != ctx["challenge"]:  # PKCE is enforced by the IdP
                return 400, {}, b"{}"
            assert headers["Authorization"].startswith("Basic ")
            return (
                200,
                {},
                json.dumps({"id_token": self._token_for(ctx), "access_token": "x"}).encode(),
            )
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def idp(monkeypatch, settings, world):
    f = FakeIdP()
    monkeypatch.setattr(netsec, "safe_request", f)
    settings.ORCH_OIDC_ISSUER, settings.ORCH_OIDC_CLIENT_ID, settings.ORCH_OIDC_CLIENT_SECRET = (
        ISSUER,
        CLIENT,
        "s3cr3t-client",
    )
    settings.ORCH_OIDC_GROUP_ROLES = json.dumps(
        {
            "orch-designers": {"workspace": "team-a", "role": "designer"},
            "orch-viewers": {"workspace": "team-a", "role": "viewer"},
        }
    )
    from django.core.cache import cache

    cache.clear()
    return f


def start(client, idp, **over):
    r = client.get("/api/v1/auth/oidc/login/")
    assert r.status_code == 302
    q = {k: v[0] for k, v in parse_qs(urlparse(r["Location"]).query).items()}
    assert q["code_challenge_method"] == "S256" and q["client_id"] == CLIENT and q["response_type"] == "code"
    idp.codes["good-code"] = {"nonce": q["nonce"], "challenge": q["code_challenge"], "over": over}
    return q


def callback(client, q, code="good-code", state=None):
    return client.get("/api/v1/auth/oidc/callback/", {"code": code, "state": state or q["state"]})


def test_disabled_by_default(world, settings):
    settings.ORCH_OIDC_ISSUER = ""
    c = APIClient()
    assert c.get("/api/v1/auth/oidc/config/").json() == {"enabled": False, "login_url": None}
    assert c.get("/api/v1/auth/oidc/login/").status_code == 404


def test_full_login_creates_user_and_maps_group_to_role(idp, world):
    c = APIClient()
    assert c.get("/api/v1/auth/oidc/config/").json()["enabled"] is True
    q = start(c, idp)
    r = callback(c, q)
    assert r.status_code == 302 and r["Location"] == "/"
    me = c.get("/api/v1/auth/me/").json()
    assert (
        me["username"] == "sso.marie"
        and me["workspaces"][0]["slug"] == "team-a"
        and me["workspaces"][0]["role"] == "designer"
    )
    user = get_user_model().objects.get(username="sso.marie")
    assert not user.has_usable_password() and OidcIdentity.objects.get().subject == "user-42"
    assert (
        AuditEvent.objects.filter(action="auth.login").exists()
        and AuditEvent.objects.filter(action="auth.sso_grant").exists()
    )


def test_role_is_resynchronised_and_revoked_on_next_login(idp, world):
    c = APIClient()
    callback(c, start(c, idp))
    idp.claims["groups"] = ["orch-viewers"]
    c2 = APIClient()
    callback(c2, start(c2, idp))
    assert Membership.objects.get(user__username="sso.marie", workspace=world["ws_a"]).role == Role.VIEWER
    idp.claims["groups"] = []
    c3 = APIClient()
    callback(c3, start(c3, idp))
    assert not Membership.objects.filter(
        user__username="sso.marie"
    ).exists()  # IdP is authoritative for mapped workspaces


def test_same_subject_keeps_same_account_but_email_never_links_local_accounts(idp, world):
    local = make_user("marie.local", None)
    local.email = "marie@example.org"
    local.save()
    c = APIClient()
    callback(c, start(c, idp))
    sso = get_user_model().objects.get(username="sso.marie")
    assert sso.pk != local.pk  # same e-mail, different account: no takeover of local accounts
    c2 = APIClient()
    callback(c2, start(c2, idp))
    assert get_user_model().objects.filter(username__startswith="sso.").count() == 1


@pytest.mark.parametrize(
    "over,message",
    [
        ({"iss": "https://evil.example.org"}, "invalide"),
        ({"aud": "someone-else"}, "invalide"),
        ({"exp": 1}, "invalide"),
        ({"nonce": "replayed"}, "nonce"),
    ],
)
def test_bad_id_token_claims_are_rejected(idp, world, over, message):
    c = APIClient()
    q = start(c, idp, **over)
    r = callback(c, q)
    assert r.status_code == 401 and message in r.json()["error"]["message"]
    assert c.get("/api/v1/auth/me/").status_code in (401, 403)


def test_forged_signature_is_rejected(idp, world):
    c = APIClient()
    q = start(c, idp)
    other = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})  # attacker's key, same kid
    idp.key, real = other, idp.key
    forged_ctx = idp.codes["good-code"]
    token = jwt.encode(
        {"alg": "RS256", "kid": "k1"},
        {"iss": ISSUER, "aud": CLIENT, "exp": int(time.time()) + 300, "sub": "x", "nonce": forged_ctx["nonce"]},
        other,
    )
    idp.key = real  # JWKS published by the IdP has the real key
    idp.id_token = lambda nonce, **o: token
    r = callback(c, q)
    assert r.status_code == 401


def test_state_mismatch_expired_and_missing_session_are_rejected(idp, world, monkeypatch):
    c = APIClient()
    q = start(c, idp)
    assert callback(c, q, state="forged").status_code == 401
    c2 = APIClient()
    assert callback(c2, {"state": "x"}).status_code == 401  # no session started
    c3 = APIClient()
    q3 = start(c3, idp)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    assert callback(c3, q3).status_code == 401


def test_pkce_is_enforced_and_idp_error_is_reported(idp, world):
    c = APIClient()
    q = start(c, idp)
    idp.codes["good-code"]["challenge"] = "tampered"
    assert callback(c, q).status_code == 401
    c2 = APIClient()
    q2 = start(c2, idp)
    r = c2.get("/api/v1/auth/oidc/callback/", {"error": "access_denied", "state": q2["state"]})
    assert r.status_code == 401


def test_no_auto_create_refuses_unknown_identity(idp, world, settings):
    settings.ORCH_OIDC_AUTO_CREATE = False
    c = APIClient()
    r = callback(c, start(c, idp))
    assert r.status_code == 401 and "Aucun compte local" in r.json()["error"]["message"]


def test_internal_idp_requires_allowlist(world, settings, monkeypatch):
    settings.ORCH_OIDC_ISSUER, settings.ORCH_OIDC_CLIENT_ID = "http://127.0.0.1:8080/realms/x", CLIENT
    from django.core.cache import cache

    cache.clear()
    r = APIClient().get("/api/v1/auth/oidc/login/")
    assert r.status_code == 502 and "refusée" in r.json()["error"]["message"]  # SSRF guard also applies to the IdP
