import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tenancy.models import OidcIdentity

User = get_user_model()
ISSUER = "https://idp.example.org/realms/orch"
BASE = "/api/v1/scim/v2"


@pytest.fixture
def scim(settings, db):
    settings.ORCH_SCIM_TOKEN = "scim-token-0123456789"
    settings.ORCH_OIDC_ISSUER = ISSUER
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Bearer scim-token-0123456789")
    return c


def post(c, path, body, method="post"):
    return getattr(c, method)(f"{BASE}{path}", json.dumps(body), content_type="application/scim+json")


def test_disabled_without_a_token_and_refused_with_a_wrong_one(settings, db):
    settings.ORCH_SCIM_TOKEN = ""
    assert APIClient().get(f"{BASE}/Users").status_code == 404
    settings.ORCH_SCIM_TOKEN = "scim-token-0123456789"
    assert APIClient().get(f"{BASE}/Users").status_code in (401, 403)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Bearer faux")
    assert c.get(f"{BASE}/Users").status_code in (401, 403)


def test_provision_then_deprovision_a_user_linked_by_external_id(scim):
    r = post(
        scim,
        "/Users",
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "marie.curie@exemple.org",
            "externalId": "kc-uuid-42",
            "emails": [{"value": "marie@exemple.org"}],
            "active": True,
        },
    )
    assert r.status_code == 201 and r["Content-Type"].startswith("application/scim+json")
    uid = r.json()["id"]
    user = User.objects.get(pk=uid)
    assert user.username == "sso.marie.curie" and user.email == "marie@exemple.org" and not user.has_usable_password()
    assert (
        OidcIdentity.objects.get(issuer=ISSUER, subject="kc-uuid-42").user_id == user.pk
    )  # what an SSO login will find
    assert post(scim, "/Users", {"userName": "autre", "externalId": "kc-uuid-42"}).status_code == 409
    assert post(scim, "/Users", {"userName": "sans-ext"}).status_code == 400
    found = scim.get(f"{BASE}/Users", {"filter": 'userName eq "sso.marie.curie"'}).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["externalId"] == "kc-uuid-42"
    assert scim.get(f"{BASE}/Users", {"filter": 'emails co "x"'}).status_code == 400
    off = post(
        scim,
        f"/Users/{uid}",
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "Replace", "path": "active", "value": False}],
        },
        "patch",
    )
    assert off.status_code == 200 and off.json()["active"] is False and not User.objects.get(pk=uid).is_active
    on = post(scim, f"/Users/{uid}", {"Operations": [{"op": "replace", "value": {"active": True}}]}, "patch")
    assert on.json()["active"] is True
    assert scim.delete(f"{BASE}/Users/{uid}").status_code == 204
    assert User.objects.filter(pk=uid, is_active=False).exists()  # deactivated, never erased


def test_a_deactivated_account_cannot_log_in_by_sso_and_unknown_ids_are_404(scim):
    from tenancy import oidc

    post(scim, "/Users", {"userName": "bob", "externalId": "sub-1"})
    uid = OidcIdentity.objects.get(subject="sub-1").user_id
    scim.delete(f"{BASE}/Users/{uid}")
    with pytest.raises(oidc.OidcError, match="désactivé"):
        oidc._sync_user({"sub": "sub-1", "preferred_username": "bob"})
    assert scim.get(f"{BASE}/Users/999999").status_code == 404
    assert scim.get(f"{BASE}/Users/abc").status_code == 404
    assert scim.get(f"{BASE}/ServiceProviderConfig").json()["patch"]["supported"] is True
