"""Du jeton ou de l'assertion au compte : liaison de l'identité, création éventuelle et droits tirés des groupes.

Les groupes du fournisseur deviennent des droits par les règles de `GroupRule` (rôle dans un espace, groupe d'utilisateurs, administration
de la plate-forme). Le moteur est partagé par OpenID Connect et SAML, et la simulation de l'interface l'appelle sans rien écrire.

Règles de sûreté (toutes testées) :
- le compte est lié par (émetteur, sujet), jamais par l'adresse e-mail ;
- un droit donné à la main (`Membership.source` vide) n'est jamais modifié ni retiré par un fournisseur ;
- un droit donné par un fournisseur n'est modifié ou retiré que par ce fournisseur, à chaque connexion ;
- le droit le plus élevé gagne quand plusieurs règles s'appliquent ;
- on ne retire jamais le dernier administrateur de plate-forme."""

import fnmatch
import re
from dataclasses import dataclass, field
from typing import cast

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from audit.models import record

from .models import GroupRule, IdentityProvider, Membership, OidcIdentity, Role, UserGroup, UserProfile, Workspace

User = get_user_model()
# du plus fort au plus faible
ROLE_ORDER: list[str] = [
    Role.ADMIN,
    Role.SPACE_MANAGER,
    Role.DESIGNER,
    Role.OPERATOR,
    Role.APPROVER,
    Role.VIEWER,
    Role.CATALOG,
]
MAX_GROUPS = 500


class SsoDenied(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class Identity:
    provider: IdentityProvider
    issuer: str
    subject: str
    username: str = ""
    email: str = ""
    name: str = ""
    groups: list[str] = field(default_factory=list)


@dataclass
class Access:
    roles: dict[int, str] = field(default_factory=dict)  # espace -> rôle
    platform_admin: bool = False
    user_groups: set[tuple[int, str]] = field(default_factory=set)
    matched: list[tuple[int, str]] = field(default_factory=list)  # (règle, groupe)
    managed_workspaces: set[int] = field(default_factory=set)  # les espaces que ce fournisseur pilote
    managed_groups: set[tuple[int, str]] = field(default_factory=set)
    manages_platform_admin: bool = False


def clean_groups(raw) -> list[str]:
    """La liste de groupes d'un jeton : une chaîne, une liste ; au plus MAX_GROUPS textes."""
    if raw in (None, ""):
        return []
    items = [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, (list, tuple)) else []
    return [str(g).strip() for g in items if isinstance(g, (str, int)) and str(g).strip()][:MAX_GROUPS]


def rule_matches(rule: GroupRule, groups: list[str]) -> list[str]:
    if rule.match == GroupRule.Match.GLOB:
        pat = rule.group.lower()
        return [g for g in groups if fnmatch.fnmatchcase(g.lower(), pat)]
    return [g for g in groups if g == rule.group]


def valid_rule(rule: GroupRule) -> bool:
    if rule.grant == GroupRule.Grant.PLATFORM_ADMIN:
        return True
    return bool(rule.workspace_id and rule.role in Role.values)


def _apply_rule(access: Access, rule: GroupRule, hit: list[str]) -> None:
    """Ce qu'une règle fait de plus quand l'un des groupes de la personne lui correspond."""
    access.matched.append((rule.pk, hit[0]))
    if rule.grant == GroupRule.Grant.PLATFORM_ADMIN:
        access.platform_admin = True
        return
    ws_id = cast(int, rule.workspace_id)  # une règle de rôle valide a toujours son espace (`valid_rule`)
    cur = access.roles.get(ws_id)
    if cur is None or ROLE_ORDER.index(rule.role) < ROLE_ORDER.index(cur):
        access.roles[ws_id] = rule.role
    if rule.user_group:
        access.user_groups.add((ws_id, rule.user_group))


def evaluate(provider: IdentityProvider, groups: list[str]) -> Access:
    access = Access()
    for rule in provider.rules.select_related("workspace"):
        if not valid_rule(rule):
            continue
        if rule.grant == GroupRule.Grant.PLATFORM_ADMIN:
            access.manages_platform_admin = True
        else:
            ws_id = cast(int, rule.workspace_id)
            access.managed_workspaces.add(ws_id)
            if rule.user_group:
                access.managed_groups.add((ws_id, rule.user_group))
        if hit := rule_matches(rule, groups):
            _apply_rule(access, rule, hit)
    return access


def describe(provider: IdentityProvider, groups: list[str]) -> dict:
    """Ce que ces groupes donneraient, sans rien écrire : pour la simulation de l'interface."""
    access = evaluate(provider, groups)
    names = {w.pk: w for w in Workspace.objects.filter(pk__in=access.managed_workspaces)}
    rules = {r.pk: r for r in provider.rules.all()}
    return {
        "groups": groups,
        "matched": [
            {"rule": rid, "group": g, "pattern": rules[rid].group} for rid, g in access.matched if rid in rules
        ],
        "workspaces": [
            {
                "workspace": names[w].slug,
                "name": names[w].name,
                "role": access.roles.get(w),
                "user_groups": sorted(n for ws, n in access.user_groups if ws == w),
            }
            for w in sorted(access.managed_workspaces, key=lambda i: names[i].slug)
            if w in names
        ],
        "platform_admin": access.platform_admin,
        "manages_platform_admin": access.manages_platform_admin,
        "login_allowed": not (provider.unmapped == IdentityProvider.Unmapped.DENY and not access.matched),
        "login_denied_reason": "Aucun groupe ne correspond à une règle : la connexion est refusée."
        if provider.unmapped == IdentityProvider.Unmapped.DENY and not access.matched
        else "",
    }


# ---- compte ----------------------------------------------------------------------------------------------------------------
def _username(identity: Identity) -> str:
    seed = identity.username or identity.email.split("@")[0] or "sso"
    base = re.sub(r"[^A-Za-z0-9._-]", "", seed)[:30] or "sso"
    name, i = f"sso.{base}", 1
    while User.objects.filter(username=name).exists():
        i += 1
        name = f"sso.{base}{i}"
    return name


def _domain_allowed(provider: IdentityProvider, email: str) -> bool:
    allowed = (provider.config or {}).get("allowed_domains") or []
    if not allowed:
        return True
    return "@" in email and email.rsplit("@", 1)[1].lower() in allowed


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(None, 1)  # « Marie » (un seul mot) est un prénom ; « Marie Curie », prénom et nom
    return (parts[0][:150], parts[1][:150] if len(parts) > 1 else "") if parts else ("", "")


@transaction.atomic
def login(identity: Identity):
    """Le compte de cette identité, créé au besoin, avec ses droits à jour. Lève `SsoDenied` avec un message pour la personne."""
    provider = identity.provider
    if not _domain_allowed(provider, identity.email):
        record("auth.sso_failed", reason="domain", provider=provider.slug)
        raise SsoDenied("Le domaine de votre adresse e-mail n'est pas autorisé pour cette connexion.")
    access = evaluate(provider, identity.groups)
    if provider.unmapped == IdentityProvider.Unmapped.DENY and not access.matched:
        record("auth.sso_failed", reason="unmapped", provider=provider.slug)
        raise SsoDenied("Votre compte n'appartient à aucun groupe autorisé à utiliser cette plate-forme.")
    ident = OidcIdentity.objects.select_related("user").filter(issuer=identity.issuer, subject=identity.subject).first()
    if ident is None:
        if not provider.auto_create:
            raise SsoDenied("Aucun compte local n'est associé à cette identité. Contactez un administrateur.")
        user = User(username=_username(identity), email=identity.email[:254])
        first, last = _split_name(identity.name)
        user.first_name, user.last_name = first, last
        user.set_unusable_password()
        user.save()
        ident = OidcIdentity.objects.create(
            issuer=identity.issuer, subject=identity.subject, user=user, provider=provider
        )
        record("auth.sso_create", actor=user, issuer=identity.issuer, provider=provider.slug)
    user = ident.user
    if not user.is_active:
        raise SsoDenied("Ce compte est désactivé.")
    changed = []
    if identity.email and user.email != identity.email[:254]:
        user.email = identity.email[:254]
        changed.append("email")
    if identity.name:
        first, last = _split_name(identity.name)
        if (user.first_name, user.last_name) != (first, last):
            user.first_name, user.last_name = first, last
            changed += ["first_name", "last_name"]
    if changed:
        user.save(update_fields=changed)
    ident.last_login_at = timezone.now()
    ident.provider = provider
    ident.save(update_fields=["last_login_at", "provider"])
    apply_access(user, provider, access)
    return user


def _apply_memberships(user, provider: IdentityProvider, access: Access) -> None:
    tag = f"sso:{provider.slug}"
    for ws in Workspace.objects.filter(pk__in=access.managed_workspaces):
        role = access.roles.get(ws.pk)
        existing = Membership.objects.filter(user=user, workspace=ws).first()
        if existing is not None and existing.source != tag:
            continue  # droit donné à la main (ou par un autre fournisseur) : jamais touché
        if role is None:
            if existing is not None:
                existing.delete()
                record("auth.sso_revoke", actor=user, workspace=ws, provider=provider.slug)
        elif existing is None:
            Membership.objects.create(user=user, workspace=ws, role=role, source=tag)
            record("auth.sso_grant", actor=user, workspace=ws, role=role, provider=provider.slug)
        elif existing.role != role:
            existing.role = role
            existing.save(update_fields=["role"])
            record("auth.sso_role", actor=user, workspace=ws, role=role, provider=provider.slug)


def _apply_user_groups(user, provider: IdentityProvider, access: Access) -> None:
    for ws_id, name in access.managed_groups:
        group = UserGroup.objects.filter(workspace_id=ws_id, name=name).first()
        wanted = (ws_id, name) in access.user_groups and ws_id in access.roles
        if wanted:
            if group is None:
                group = UserGroup.objects.create(
                    workspace_id=ws_id, name=name, description=f"Alimenté par le fournisseur « {provider.name} »."
                )
            if not group.members.filter(pk=user.pk).exists():
                group.members.add(user)
                record("auth.sso_group_add", actor=user, workspace=group.workspace, group=name, provider=provider.slug)
        elif group is not None and group.members.filter(pk=user.pk).exists():
            group.members.remove(user)
            record("auth.sso_group_remove", actor=user, workspace=group.workspace, group=name, provider=provider.slug)


def apply_access(user, provider: IdentityProvider, access: Access) -> None:
    _apply_memberships(user, provider, access)
    _apply_user_groups(user, provider, access)
    if access.manages_platform_admin:
        _sync_platform_admin(user, provider, access.platform_admin)


def _sync_platform_admin(user, provider: IdentityProvider, wanted: bool) -> None:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if wanted:
        if not user.is_superuser:
            user.is_superuser = True
            user.save(update_fields=["is_superuser"])
            profile.sso_platform_admin = True
            profile.save(update_fields=["sso_platform_admin"])
            record("auth.sso_platform_admin", actor=user, provider=provider.slug, granted=True)
        return
    if profile.sso_platform_admin and user.is_superuser:
        if not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).exists():
            return  # le dernier administrateur de la plate-forme n'est jamais retiré
        user.is_superuser = False
        user.save(update_fields=["is_superuser"])
        profile.sso_platform_admin = False
        profile.save(update_fields=["sso_platform_admin"])
        record("auth.sso_platform_admin", actor=user, provider=provider.slug, granted=False)
