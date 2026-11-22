"""Moteur SSO partagé (OpenID Connect et SAML) : groupes du fournisseur → droits, avec les règles de sûreté."""

import pytest
from django.contrib.auth import get_user_model

from audit.models import AuditEvent
from tenancy import sso_engine
from tenancy.models import GroupRule, IdentityProvider, Membership, OidcIdentity, Role, UserGroup, UserProfile
from tests.conftest import make_user

ISSUER = "https://idp.example.org"


@pytest.fixture
def prov(db):
    return IdentityProvider.objects.create(
        slug="corp", name="Corp", kind="oidc", enabled=True, config={"issuer": ISSUER}
    )


def rule(prov, group, ws=None, role="", **kw):
    grant = kw.pop("grant", GroupRule.Grant.WORKSPACE if ws else GroupRule.Grant.PLATFORM_ADMIN)
    return GroupRule.objects.create(provider=prov, group=group, workspace=ws, role=role, grant=grant, **kw)


def ident(prov, groups, sub="u1", **kw):
    return sso_engine.Identity(
        provider=prov,
        issuer=ISSUER,
        subject=sub,
        username=kw.pop("username", "marie"),
        email=kw.pop("email", "marie@example.org"),
        groups=groups,
        **kw,
    )


def test_the_highest_role_wins_when_several_rules_apply(prov, world):
    rule(prov, "viewers", world["ws_a"], Role.VIEWER)
    rule(prov, "designers", world["ws_a"], Role.DESIGNER)
    user = sso_engine.login(ident(prov, ["viewers", "designers"]))
    m = Membership.objects.get(user=user, workspace=world["ws_a"])
    assert m.role == Role.DESIGNER and m.source == "sso:corp"


def test_glob_rules_and_exact_rules_are_case_aware(prov, world):
    rule(prov, "orch-*", world["ws_a"], Role.OPERATOR, match="glob")
    rule(prov, "Admins", world["ws_b"], Role.ADMIN)
    user = sso_engine.login(ident(prov, ["ORCH-ops", "admins"]))
    assert Membership.objects.get(user=user, workspace=world["ws_a"]).role == Role.OPERATOR  # le motif ignore la casse
    assert not Membership.objects.filter(user=user, workspace=world["ws_b"]).exists()  # le nom exact, non


def test_roles_follow_the_groups_at_each_login_and_are_revoked(prov, world):
    rule(prov, "designers", world["ws_a"], Role.DESIGNER)
    rule(prov, "viewers", world["ws_a"], Role.VIEWER)
    user = sso_engine.login(ident(prov, ["designers"]))
    sso_engine.login(ident(prov, ["viewers"]))
    assert Membership.objects.get(user=user, workspace=world["ws_a"]).role == Role.VIEWER
    sso_engine.login(ident(prov, []))
    assert not Membership.objects.filter(user=user, workspace=world["ws_a"]).exists()
    assert {"auth.sso_grant", "auth.sso_role", "auth.sso_revoke"} <= set(
        AuditEvent.objects.values_list("action", flat=True)
    )


def test_a_manual_right_is_never_touched_by_a_provider(prov, world):
    rule(prov, "designers", world["ws_a"], Role.DESIGNER)
    user = make_user("sso.marie", world["ws_a"], Role.VIEWER)  # donné à la main
    OidcIdentity.objects.create(issuer=ISSUER, subject="u1", user=user)
    sso_engine.login(ident(prov, ["designers"]))
    m = Membership.objects.get(user=user, workspace=world["ws_a"])
    assert (m.role, m.source) == (Role.VIEWER, "")
    sso_engine.login(ident(prov, []))
    assert Membership.objects.filter(user=user, workspace=world["ws_a"]).exists()


def test_a_right_of_another_provider_is_left_alone(prov, world):
    other = IdentityProvider.objects.create(slug="other", name="Other", kind="saml", config={})
    rule(prov, "g", world["ws_a"], Role.DESIGNER)
    user = make_user("sso.marie")
    Membership.objects.create(user=user, workspace=world["ws_a"], role=Role.VIEWER, source=f"sso:{other.slug}")
    OidcIdentity.objects.create(issuer=ISSUER, subject="u1", user=user)
    sso_engine.login(ident(prov, ["g"]))
    assert Membership.objects.get(user=user, workspace=world["ws_a"]).role == Role.VIEWER


def test_workspaces_without_any_rule_are_not_managed(prov, world):
    rule(prov, "g", world["ws_a"], Role.DESIGNER)
    user = make_user("sso.marie", world["ws_b"], Role.ADMIN)
    OidcIdentity.objects.create(issuer=ISSUER, subject="u1", user=user)
    sso_engine.login(ident(prov, []))
    assert Membership.objects.filter(user=user, workspace=world["ws_b"]).exists()


def test_user_groups_are_filled_and_emptied(prov, world):
    rule(prov, "ops", world["ws_a"], Role.OPERATOR, user_group="Exploitation")
    user = sso_engine.login(ident(prov, ["ops"]))
    group = UserGroup.objects.get(workspace=world["ws_a"], name="Exploitation")
    assert group.members.filter(pk=user.pk).exists()
    sso_engine.login(ident(prov, []))
    assert not group.members.filter(pk=user.pk).exists()


def test_platform_admin_is_granted_and_revoked_only_when_it_came_from_the_provider(prov, world):
    rule(prov, "root")
    make_user("keeper").__class__.objects.filter(username="keeper").update(
        is_superuser=True
    )  # un autre administrateur existe
    user = sso_engine.login(ident(prov, ["root"]))
    assert user.is_superuser and UserProfile.objects.get(user=user).sso_platform_admin
    user = sso_engine.login(ident(prov, []))
    assert not user.is_superuser and not UserProfile.objects.get(user=user).sso_platform_admin


def test_a_hand_made_platform_admin_is_not_revoked(prov, world):
    rule(prov, "root")
    user = make_user("sso.marie")
    user.is_superuser = True
    user.save()
    OidcIdentity.objects.create(issuer=ISSUER, subject="u1", user=user)
    sso_engine.login(ident(prov, []))
    user.refresh_from_db()
    assert user.is_superuser


def test_the_last_platform_admin_is_never_removed(prov, world):
    rule(prov, "root")
    user = sso_engine.login(ident(prov, ["root"]))
    sso_engine.login(ident(prov, []))
    user.refresh_from_db()
    assert user.is_superuser  # il n'y en a pas d'autre


def test_unmapped_policy_deny_refuses_before_creating_any_account(prov, world):
    rule(prov, "g", world["ws_a"], Role.VIEWER)
    prov.unmapped = IdentityProvider.Unmapped.DENY
    prov.save()
    with pytest.raises(sso_engine.SsoDenied):
        sso_engine.login(ident(prov, ["nobody"]))
    assert not get_user_model().objects.filter(username__startswith="sso.").exists()
    assert sso_engine.login(ident(prov, ["g"]))


def test_unmapped_policy_allow_logs_in_without_access(prov, world):
    rule(prov, "g", world["ws_a"], Role.VIEWER)
    user = sso_engine.login(ident(prov, ["nobody"]))
    assert not Membership.objects.filter(user=user).exists()


def test_allowed_domains_and_auto_create(prov, world):
    prov.config = {**prov.config, "allowed_domains": ["example.org"]}
    prov.save()
    with pytest.raises(sso_engine.SsoDenied):
        sso_engine.login(ident(prov, [], email="x@evil.test"))
    prov.auto_create = False
    prov.save()
    with pytest.raises(sso_engine.SsoDenied):
        sso_engine.login(ident(prov, []))


def test_the_account_is_linked_by_subject_never_by_email(prov, world):
    victim = make_user("victim")
    victim.email = "marie@example.org"
    victim.save()
    user = sso_engine.login(ident(prov, [], sub="attacker-sub"))
    assert user.pk != victim.pk


def test_a_disabled_account_cannot_log_in_and_name_and_email_follow_the_provider(prov, world):
    user = sso_engine.login(ident(prov, [], name="Marie Curie"))
    assert (user.first_name, user.last_name) == ("Marie", "Curie")
    user = sso_engine.login(ident(prov, [], email="new@example.org", name="Marie S. Curie"))
    assert user.email == "new@example.org" and user.last_name == "S. Curie"
    user.is_active = False
    user.save()
    with pytest.raises(sso_engine.SsoDenied):
        sso_engine.login(ident(prov, []))


def test_simulation_writes_nothing_and_says_what_would_happen(prov, world):
    rule(prov, "designers", world["ws_a"], Role.DESIGNER, user_group="Concepteurs")
    rule(prov, "root")
    before = (Membership.objects.count(), UserGroup.objects.count())
    out = sso_engine.describe(prov, ["designers"])
    assert (Membership.objects.count(), UserGroup.objects.count()) == before
    assert out["workspaces"][0]["role"] == Role.DESIGNER and out["workspaces"][0]["user_groups"] == ["Concepteurs"]
    assert out["platform_admin"] is False and out["manages_platform_admin"] is True and out["login_allowed"] is True
    assert sso_engine.describe(prov, ["root"])["platform_admin"] is True


def test_invalid_rules_are_ignored_and_group_lists_are_cleaned(prov, world):
    GroupRule.objects.create(provider=prov, group="broken", grant="workspace")  # ni espace ni rôle
    assert sso_engine.evaluate(prov, ["broken"]).matched == []
    assert sso_engine.clean_groups("a") == ["a"] and sso_engine.clean_groups(["a", " ", 3, None, "b"]) == [
        "a",
        "3",
        "b",
    ]
    assert len(sso_engine.clean_groups([f"g{i}" for i in range(900)])) == sso_engine.MAX_GROUPS


def test_names_with_one_word_or_none_are_accepted(prov, world):
    """Un nom d'un seul mot (réel : l'attribut SAML d'un annuaire) est un prénom ; un nom vide ne casse rien."""
    assert sso_engine._split_name("Marie") == ("Marie", "")
    assert sso_engine._split_name("") == ("", "") and sso_engine._split_name("  ") == ("", "")
    assert sso_engine._split_name("Marie de la Tour") == ("Marie", "de la Tour")
    user = sso_engine.login(ident(prov, [], name="Marie"))
    assert (user.first_name, user.last_name) == ("Marie", "")
