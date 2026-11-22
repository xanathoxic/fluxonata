"""Administration du SSO : droits, validation, secret jamais renvoyé, règles, simulation, suppression sans perte d'accès."""

import pytest

from audit.models import AuditEvent
from tenancy.models import IdentityProvider, Membership, Role
from tests.conftest import client_for, make_user

BASE = "/api/v1/platform/sso"
OIDC = {
    "slug": "corp",
    "name": "Annuaire",
    "kind": "oidc",
    "secret": "tres-secret-123",
    "config": {"issuer": "https://idp.example.org/realms/x", "client_id": "fluxonata"},
}
KEY_WORDS = (
    "PRIVATE" + " KEY"
)  # écrit en deux morceaux : le test refuse une clé privée collée parmi les certificats, sans en contenir une
CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
SAML = {
    "slug": "adfs",
    "name": "AD FS",
    "kind": "saml",
    "config": {
        "idp_entity_id": "https://adfs.example.org/adfs/services/trust",
        "idp_sso_url": "https://adfs.example.org/adfs/ls/",
        "idp_certs": [CERT],
    },
}


@pytest.fixture
def admin(world):
    u = make_user("root")
    u.is_superuser = True
    u.save()
    return client_for(u)


def test_only_platform_administrators_may_use_it(world, admin):
    assert admin.get(f"{BASE}/providers/").status_code == 200
    for who in (client_for(world["designer"]),):
        assert who.get(f"{BASE}/providers/").status_code == 403
        assert who.post(f"{BASE}/providers/", OIDC, format="json").status_code == 403
        assert who.put(f"{BASE}/providers/corp/rules/", {"rules": []}, format="json").status_code == 403
    from rest_framework.test import APIClient

    assert APIClient().get(f"{BASE}/providers/").status_code in (401, 403)


def test_create_an_oidc_provider_and_never_return_its_secret(admin):
    r = admin.post(f"{BASE}/providers/", OIDC, format="json")
    assert r.status_code == 201, r.data
    body = r.json()
    assert body["has_secret"] is True and "tres-secret-123" not in r.content.decode()
    assert body["config"]["claims"]["groups"] == "groups" and body["config"]["pkce"] is True
    assert body["endpoints"]["callback"].endswith("/api/v1/auth/sso/corp/callback/")
    assert "tres-secret-123" not in admin.get(f"{BASE}/providers/").content.decode()
    row = IdentityProvider.objects.get(slug="corp")
    assert "tres-secret-123" not in row.secret_token and row.secret_token  # chiffré
    from tenancy import sso_config

    assert sso_config.get_secret(row) == "tres-secret-123"
    assert AuditEvent.objects.filter(action="platform.sso_create").exists()
    assert "tres-secret-123" not in str(list(AuditEvent.objects.values_list("data", flat=True)))


def test_update_secret_clear_it_and_enable(admin):
    admin.post(f"{BASE}/providers/", OIDC, format="json")
    r = admin.patch(f"{BASE}/providers/corp/", {"enabled": True, "name": "Autre nom"}, format="json").json()
    assert (
        r["enabled"] is True and r["name"] == "Autre nom" and r["has_secret"] is True
    )  # le secret reste quand on ne le touche pas
    assert admin.patch(f"{BASE}/providers/corp/", {"clear_secret": True}, format="json").json()["has_secret"] is False
    assert admin.patch(f"{BASE}/providers/corp/", {"secret": "nouveau"}, format="json").json()["has_secret"] is True


@pytest.mark.parametrize(
    "mutate,field",
    [
        (lambda d: d.update(slug="X"), "slug"),
        (lambda d: d.update(slug="config"), "slug"),
        (lambda d: d.update(name=""), "name"),
        (lambda d: d.update(kind="ldap"), "kind"),
        (lambda d: d["config"].update(issuer="ftp://idp"), "issuer"),
        (lambda d: d["config"].update(client_id=""), "client_id"),
        (lambda d: d["config"].update(scopes="profile email"), "scopes"),
        (lambda d: d["config"].update(token_auth="digest"), "token_auth"),
        (lambda d: d["config"].update(allowed_domains=["pas un domaine"]), "allowed_domains"),
        (lambda d: d["config"].update(claims={"groups": "bad name!"}), "claims.groups"),
    ],
)
def test_oidc_provider_validation(admin, mutate, field):
    d = {**OIDC, "config": dict(OIDC["config"])}
    mutate(d)
    r = admin.post(f"{BASE}/providers/", d, format="json")
    assert r.status_code == 400
    assert f'"{field}"' in r.content.decode()  # le message est rattaché au bon champ
    assert not IdentityProvider.objects.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(idp_entity_id=""),
        lambda c: c.update(idp_sso_url="javascript:alert(1)"),
        lambda c: c.update(idp_certs=[]),
        lambda c: c.update(idp_certs=["pas un certificat"]),
        lambda c: c.update(
            idp_certs=[f"-----BEGIN {KEY_WORDS}-----\nabc\n-----END {KEY_WORDS}-----\n-----BEGIN CERTIFICATE-----"]
        ),
        lambda c: c.update(name_id_format="n'importe quoi"),
        lambda c: c.update(clock_skew=9999),
    ],
)
def test_saml_provider_validation(admin, mutate):
    d = {**SAML, "config": dict(SAML["config"])}
    mutate(d["config"])
    assert admin.post(f"{BASE}/providers/", d, format="json").status_code == 400
    assert admin.post(f"{BASE}/providers/", SAML, format="json").status_code == 201


def test_a_slug_is_unique_and_the_number_of_providers_is_bounded(admin):
    assert admin.post(f"{BASE}/providers/", OIDC, format="json").status_code == 201
    assert admin.post(f"{BASE}/providers/", OIDC, format="json").status_code == 400
    for i in range(19):
        assert admin.post(f"{BASE}/providers/", {**OIDC, "slug": f"p-{i:02d}"}, format="json").status_code == 201
    assert admin.post(f"{BASE}/providers/", {**OIDC, "slug": "one-too-many"}, format="json").status_code == 400


def test_rules_are_replaced_as_a_whole_and_validated(admin, world):
    admin.post(f"{BASE}/providers/", OIDC, format="json")
    good = [
        {"group": "orch-designers", "workspace": "team-a", "role": "designer", "user_group": "Concepteurs"},
        {"group": "orch-*", "match": "glob", "workspace": "team-b", "role": "viewer"},
        {"group": "platform-admins", "grant": "platform_admin"},
    ]
    r = admin.put(f"{BASE}/providers/corp/rules/", {"rules": good}, format="json")
    assert r.status_code == 200 and [x["group"] for x in r.json()["results"]] == [
        "orch-designers",
        "orch-*",
        "platform-admins",
    ]
    assert r.json()["results"][0]["workspace_name"] == "team-a"
    bads = [
        [{"group": "", "workspace": "team-a", "role": "viewer"}],
        [{"group": "g", "workspace": "inconnu", "role": "viewer"}],
        [{"group": "g", "workspace": "team-a", "role": "superman"}],
        [{"group": "g", "workspace": "team-a", "role": "viewer", "match": "regex"}],
        [{"group": "g", "workspace": "team-a", "role": "viewer", "user_group": "x" * 100}],
        "pas une liste",
    ]
    for bad in bads:
        assert admin.put(f"{BASE}/providers/corp/rules/", {"rules": bad}, format="json").status_code == 400
    assert len(admin.get(f"{BASE}/providers/corp/rules/").json()["results"]) == 3  # un refus ne change rien
    assert admin.put(f"{BASE}/providers/corp/rules/", {"rules": []}, format="json").json()["results"] == []
    assert (
        admin.put(
            f"{BASE}/providers/corp/rules/",
            {"rules": [{"group": f"g{i}", "workspace": "team-a", "role": "viewer"} for i in range(201)]},
            format="json",
        ).status_code
        == 400
    )


def test_simulation_uses_the_saved_rules(admin, world):
    admin.post(f"{BASE}/providers/", {**OIDC}, format="json")
    admin.put(
        f"{BASE}/providers/corp/rules/",
        {"rules": [{"group": "d", "workspace": "team-a", "role": "designer"}]},
        format="json",
    )
    out = admin.post(f"{BASE}/providers/corp/simulate/", {"groups": ["d", "autre"]}, format="json").json()
    assert out["workspaces"] == [{"workspace": "team-a", "name": "team-a", "role": "designer", "user_groups": []}]
    assert out["matched"][0]["pattern"] == "d" and out["login_allowed"] is True
    admin.patch(f"{BASE}/providers/corp/", {"unmapped": "deny"}, format="json")
    assert (
        admin.post(f"{BASE}/providers/corp/simulate/", {"groups": ["autre"]}, format="json").json()["login_allowed"]
        is False
    )


def test_deleting_a_provider_keeps_the_access_it_granted_as_manual_rights(admin, world):
    admin.post(f"{BASE}/providers/", OIDC, format="json")
    user = make_user("sso.paul")
    Membership.objects.create(user=user, workspace=world["ws_a"], role=Role.DESIGNER, source="sso:corp")
    assert admin.delete(f"{BASE}/providers/corp/").status_code == 204
    m = Membership.objects.get(user=user, workspace=world["ws_a"])
    assert m.role == Role.DESIGNER and m.source == ""
    assert not IdentityProvider.objects.exists()
    assert admin.get(f"{BASE}/providers/corp/").status_code == 404


def test_public_list_shows_enabled_providers_only_without_any_detail(admin):
    from rest_framework.test import APIClient

    admin.post(f"{BASE}/providers/", OIDC, format="json")
    admin.post(f"{BASE}/providers/", SAML, format="json")
    admin.patch(f"{BASE}/providers/adfs/", {"enabled": True}, format="json")
    rows = APIClient().get("/api/v1/auth/sso/providers/").json()["results"]
    assert rows == [{"slug": "adfs", "name": "AD FS", "kind": "saml", "login_url": "/api/v1/auth/sso/adfs/login/"}]


def test_a_manual_role_change_takes_over_an_sso_right_and_lists_show_the_source(admin, world):
    user = make_user("sso.paul")
    Membership.objects.create(user=user, workspace=world["ws_a"], role=Role.VIEWER, source="sso:corp")
    users = admin.get("/api/v1/platform/users/").json()["results"]
    paul = next(u for u in users if u["username"] == "sso.paul")
    assert paul["memberships"][0]["source"] == "corp" and paul["sso_platform_admin"] is False
    assert admin.post(
        "/api/v1/platform/users/sso.paul/memberships/", {"workspace": "team-a", "role": "designer"}, format="json"
    ).status_code in (200, 201, 204)
    assert Membership.objects.get(user=user, workspace=world["ws_a"]).source == ""
