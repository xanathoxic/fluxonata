"""Connexion OpenID Connect par un fournisseur réglé dans l'interface (base de données), avec un fournisseur simulé en mémoire."""

import json
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from audit.models import AuditEvent
from core import netsec
from tenancy import sso_config
from tenancy.models import GroupRule, IdentityProvider, Membership, Role
from tests.test_oidc import CLIENT, ISSUER, FakeIdP


class PostIdP(FakeIdP):
    """Comme le fournisseur de base, mais le client s'authentifie dans le corps (client_secret_post)."""

    def __call__(self, method, url, headers=None, body=None, **kw):
        if url.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            assert (
                "Authorization" not in (headers or {})
                and form["client_secret"] == "s3cr3t"
                and form["client_id"] == CLIENT
            )
            ctx = self.codes.get(form.get("code"))
            return 200, {}, json.dumps({"id_token": self._token_for(ctx)}).encode()
        return super().__call__(method, url, headers=headers, body=body, **kw)


@pytest.fixture
def provider(world, monkeypatch):
    cache.clear()
    p = IdentityProvider(
        slug="corp",
        name="Annuaire",
        kind="oidc",
        enabled=True,
        config=sso_config.clean("oidc", {"issuer": ISSUER, "client_id": CLIENT, "claims": {"groups": "roles"}})[0],
    )
    sso_config.set_secret(p, "s3cr3t")
    p.save()
    GroupRule.objects.create(
        provider=p, group="orch-designers", workspace=world["ws_a"], role=Role.DESIGNER, grant="workspace"
    )
    return p


@pytest.fixture
def idp(monkeypatch, provider):
    f = FakeIdP()
    f.claims = {
        "sub": "user-42",
        "preferred_username": "marie",
        "email": "marie@example.org",
        "roles": ["orch-designers"],
    }
    monkeypatch.setattr(netsec, "safe_request", f)
    return f


def start(client, idp, slug="corp", **over):
    r = client.get(f"/api/v1/auth/sso/{slug}/login/")
    assert r.status_code == 302, r.content
    q = {k: v[0] for k, v in parse_qs(urlparse(r["Location"]).query).items()}
    idp.codes["good-code"] = {"nonce": q["nonce"], "challenge": q.get("code_challenge", ""), "over": over}
    return q


def back(client, q, slug="corp", code="good-code", state=None):
    return client.get(f"/api/v1/auth/sso/{slug}/callback/", {"code": code, "state": state or q["state"]})


def test_login_redirects_with_pkce_and_the_callback_of_this_provider(idp):
    q = start(APIClient(), idp)
    assert q["client_id"] == CLIENT and q["code_challenge_method"] == "S256" and q["scope"] == "openid profile email"
    assert q["redirect_uri"].endswith("/api/v1/auth/sso/corp/callback/")


def test_full_login_with_custom_claim_names_applies_the_rules(idp, world):
    c = APIClient()
    r = back(c, start(c, idp))
    assert r.status_code == 302 and r["Location"] == "/"
    me = c.get("/api/v1/auth/me/").json()
    assert me["username"] == "sso.marie" and [(w["slug"], w["role"]) for w in me["workspaces"]] == [
        ("team-a", "designer")
    ]
    assert Membership.objects.get(user__username="sso.marie").source == "sso:corp"
    assert AuditEvent.objects.filter(action="auth.login", data__provider="corp").exists()


def test_roles_follow_the_groups_on_the_next_login(idp, world):
    back(APIClient(), start(APIClient(), idp))
    idp.claims["roles"] = []
    c = APIClient()
    back(c, start(c, idp))
    assert not Membership.objects.filter(user__username="sso.marie").exists()


def test_a_provider_without_pkce_sends_no_challenge_and_post_authentication_works(monkeypatch, provider):
    provider.config = {**provider.config, "pkce": False, "token_auth": "post"}
    provider.save()
    f = PostIdP()
    f.claims = {"sub": "user-9", "preferred_username": "paul", "roles": []}
    monkeypatch.setattr(netsec, "safe_request", f)
    c = APIClient()
    q = start(c, f)
    assert "code_challenge" not in q
    f.codes["good-code"]["challenge"] = ""
    r = back(c, q)
    assert r.status_code == 302 and r["Location"] == "/"


def error_of(response) -> str:
    assert response.status_code == 302 and response["Location"].startswith("/?sso_error=")
    return unquote(response["Location"].split("sso_error=", 1)[1]).replace("+", " ")


def test_errors_go_back_to_the_login_page_with_a_message(idp):
    c = APIClient()
    q = start(c, idp)
    assert "state" in error_of(back(c, q, state="forged")).lower()
    c = APIClient()
    q = start(c, idp, iss="https://evil.example")
    assert "invalide" in error_of(back(c, q))
    assert "expirée" in error_of(back(APIClient(), q))  # sans session de départ
    assert AuditEvent.objects.filter(action="auth.sso_failed", data__provider="corp").count() >= 3


def test_a_forged_signature_is_refused(idp):
    from joserfc import jwt
    from joserfc.jwk import RSAKey

    rogue = RSAKey.generate_key(
        2048, parameters={"kid": "k1", "use": "sig"}
    )  # même identifiant de clé, autre clé que celle publiée

    def forged(nonce, **over):
        return jwt.encode(
            {"alg": "RS256", "kid": "k1"},
            {"iss": ISSUER, "aud": CLIENT, "exp": 9999999999, "nonce": nonce, **idp.claims},
            rogue,
        )

    idp.id_token = forged
    c = APIClient()
    assert "invalide" in error_of(back(c, start(c, idp)))
    assert not Membership.objects.filter(user__username="sso.marie").exists()


def test_unmapped_deny_and_disabled_provider(idp, provider, world):
    provider.unmapped = IdentityProvider.Unmapped.DENY
    provider.save()
    idp.claims["roles"] = ["nobody"]
    c = APIClient()
    assert "aucun groupe autorisé" in error_of(back(c, start(c, idp)))
    provider.enabled = False
    provider.save()
    assert APIClient().get("/api/v1/auth/sso/corp/login/").status_code == 404
    assert APIClient().get("/api/v1/auth/sso/corp/callback/").status_code == 404


def test_a_saml_provider_has_no_oidc_callback_and_the_wrong_slug_is_unknown(provider):
    saml = IdentityProvider.objects.create(slug="adfs", name="AD FS", kind="saml", enabled=True, config={})
    assert APIClient().get(f"/api/v1/auth/sso/{saml.slug}/callback/").status_code == 404
    assert APIClient().get("/api/v1/auth/sso/nope/login/").status_code == 404


def test_legacy_environment_provider_is_listed_next_to_the_others(provider, settings):
    settings.ORCH_OIDC_ISSUER, settings.ORCH_OIDC_CLIENT_ID = "https://legacy.example", "legacy"
    slugs = [r["slug"] for r in APIClient().get("/api/v1/auth/sso/providers/").json()["results"]]
    assert slugs == ["corp", "environment"]
