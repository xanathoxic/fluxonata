"""Administration du SSO (administrateurs de la plate-forme seulement, chaque changement est audité) : fournisseurs OpenID Connect et SAML,
règles « groupe → droit » et simulation de ces règles. Le secret d'un client n'est jamais renvoyé."""

from django.conf import settings
from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response

from audit.models import record
from core import netsec

from . import oidc, sso_config, sso_engine, sso_oidc, sso_saml
from .models import GroupRule, IdentityProvider, Membership, Role, Workspace
from .platform_views import _Guarded

MAX_PROVIDERS = 20
MAX_RULES = 200
LEGACY = "environment"


def _row(p: IdentityProvider, request) -> dict:
    return {
        "slug": p.slug,
        "name": p.name,
        "kind": p.kind,
        "enabled": p.enabled,
        "auto_create": p.auto_create,
        "unmapped": p.unmapped,
        "config": p.config,
        "has_secret": bool(p.secret_token),
        "endpoints": sso_config.endpoints(p, request),
        "rules": p.rules.count(),
        "identities": p.identities.count(),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _provider(slug: str) -> IdentityProvider:
    p = IdentityProvider.objects.filter(slug=slug).first()
    if p is None:
        raise Http404()
    return p


def _fail(problems: dict) -> None:
    raise serializers.ValidationError(problems)


def _name(value) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= 80:
        _fail({"name": "Un nom de 80 caractères au plus."})
    return name


def _choice(value, allowed, label: str, default):
    value = default if value in (None, "") else value
    if value not in allowed:
        _fail({label: "Valeur inconnue."})
    return value


class ProvidersView(_Guarded):
    def get(self, request):
        legacy = None
        if oidc.enabled():
            legacy = {
                "issuer": settings.ORCH_OIDC_ISSUER,
                "client_id": settings.ORCH_OIDC_CLIENT_ID,
                "login": "/api/v1/auth/oidc/login/",
                "group_roles": bool(settings.ORCH_OIDC_GROUP_ROLES),
            }
        return Response(
            {
                "results": [_row(p, request) for p in IdentityProvider.objects.all()],
                "environment": legacy,
                "base_url": sso_config.base_url(request),
                "public_url_configured": bool(settings.ORCH_PUBLIC_URL),
                "roles": [{"id": r.value, "label": r.label} for r in Role],
                "name_id_formats": [{"id": k, "label": v} for k, v in sso_config.NAME_ID_FORMATS.items()],
            }
        )

    @transaction.atomic
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        if IdentityProvider.objects.count() >= MAX_PROVIDERS:
            _fail({"name": f"{MAX_PROVIDERS} fournisseurs au plus."})
        slug = str(data.get("slug") or "").strip()
        if msg := sso_config.check_slug(slug):
            _fail({"slug": msg})
        kind = _choice(data.get("kind"), IdentityProvider.Kind.values, "kind", None)
        config, problems = sso_config.clean(kind, data.get("config"))
        if problems:
            _fail(problems)
        p = IdentityProvider(
            slug=slug,
            name=_name(data.get("name")),
            kind=kind,
            config=config,
            auto_create=bool(data.get("auto_create", True)),
            unmapped=_choice(
                data.get("unmapped"), IdentityProvider.Unmapped.values, "unmapped", IdentityProvider.Unmapped.ALLOW
            ),
        )
        if kind == IdentityProvider.Kind.OIDC and data.get("secret"):
            sso_config.set_secret(p, str(data["secret"])[:500])
        p.save()
        record("platform.sso_create", actor=request.user, target=p, provider=slug, kind=kind)
        return Response(_row(p, request), status=201)


class ProviderView(_Guarded):
    def get(self, request, slug):
        return Response(_row(_provider(slug), request))

    @transaction.atomic
    def patch(self, request, slug):
        p = _provider(slug)
        data = request.data if isinstance(request.data, dict) else {}
        changed = []
        if "name" in data:
            p.name = _name(data["name"])
            changed.append("name")
        if "auto_create" in data:
            p.auto_create = bool(data["auto_create"])
            changed.append("auto_create")
        if "unmapped" in data:
            p.unmapped = _choice(data["unmapped"], IdentityProvider.Unmapped.values, "unmapped", None)
            changed.append("unmapped")
        if "config" in data:
            config, problems = sso_config.clean(p.kind, data["config"])
            if problems:
                _fail(problems)
            p.config = config
            changed.append("config")
        if data.get("clear_secret"):
            sso_config.set_secret(p, "")
            changed.append("secret_cleared")
        elif data.get("secret"):
            sso_config.set_secret(p, str(data["secret"])[:500])
            changed.append("secret")
        if "enabled" in data:
            want = bool(data["enabled"])
            if want and not p.config:
                _fail({"enabled": "Réglez le fournisseur avant de l'activer."})
            p.enabled = want
            changed.append("enabled" if want else "disabled")
        p.save()
        record("platform.sso_update", actor=request.user, target=p, provider=slug, changed=changed)
        return Response(_row(p, request))

    @transaction.atomic
    def delete(self, request, slug):
        p = _provider(slug)
        # les droits donnés par ce fournisseur restent, mais deviennent des droits « à la main » : personne ne perd son accès sans le savoir
        kept = Membership.objects.filter(source=f"sso:{slug}").update(source="")
        p.delete()
        record("platform.sso_delete", actor=request.user, provider=slug, kept_memberships=kept)
        return Response(status=204)


class ProviderTestView(_Guarded):
    """Essai sans connexion. Un échec est une réponse normale (`ok: false`) : c'est l'objet de l'essai."""

    def post(self, request, slug):
        p = _provider(slug)
        try:
            detail = sso_oidc.test(p) if p.kind == IdentityProvider.Kind.OIDC else sso_saml.test(p)
        except (oidc.OidcError, sso_saml.SamlError) as exc:
            record("platform.sso_test", actor=request.user, provider=slug, ok=False)
            return Response({"ok": False, "message": exc.message})
        record("platform.sso_test", actor=request.user, provider=slug, ok=True)
        return Response({"ok": True, "detail": detail})


class SamlMetadataImportView(_Guarded):
    """Lit les métadonnées d'un fournisseur SAML (adresse ou XML collé) et renvoie les réglages à reprendre ; n'écrit rien."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            if data.get("url"):
                raw = sso_saml.fetch_metadata(str(data["url"]).strip())
            elif data.get("xml"):
                raw = str(data["xml"]).encode()
            else:
                _fail({"url": "Indiquez l'adresse des métadonnées ou collez leur XML."})
            return Response(sso_saml.read_idp_metadata(raw))
        except (sso_saml.SamlError, netsec.UnsafeURL) as exc:
            _fail({"metadata": getattr(exc, "message", None) or str(exc)})


def _rule_row(r: GroupRule) -> dict:
    ws = r.workspace
    return {
        "id": r.pk,
        "group": r.group,
        "match": r.match,
        "grant": r.grant,
        "workspace": ws.slug if ws else None,
        "workspace_name": ws.name if ws else None,
        "role": r.role,
        "user_group": r.user_group,
    }


def _build_rule(
    p: IdentityProvider, i: int, item, spaces: dict[str, Workspace]
) -> tuple[GroupRule | None, dict[str, str]]:
    """Une règle lue dans la requête : (la règle ou None, les problèmes par champ)."""
    at = f"rules.{i}"
    if not isinstance(item, dict):
        return None, {at: "Règle invalide."}
    problems: dict[str, str] = {}
    group = str(item.get("group") or "").strip()
    match = item.get("match") or GroupRule.Match.EXACT
    grant = item.get("grant") or GroupRule.Grant.WORKSPACE
    if not 1 <= len(group) <= 255:
        problems[f"{at}.group"] = "Nom de groupe obligatoire (255 caractères au plus)."
    if match not in GroupRule.Match.values or grant not in GroupRule.Grant.values:
        return None, {**problems, at: "Type de correspondance ou de droit inconnu."}
    rule = GroupRule(provider=p, position=i, group=group, match=match, grant=grant)
    if grant == GroupRule.Grant.WORKSPACE:
        ws = spaces.get(str(item.get("workspace") or ""))
        role = item.get("role") or ""
        user_group = str(item.get("user_group") or "").strip()
        if ws is None:
            problems[f"{at}.workspace"] = "Choisissez un espace existant."
        if role not in Role.values:
            problems[f"{at}.role"] = "Choisissez un rôle."
        if len(user_group) > 80:
            problems[f"{at}.user_group"] = "Nom de groupe d'utilisateurs de 80 caractères au plus."
        rule.workspace, rule.role, rule.user_group = ws, role if role in Role.values else "", user_group
    return rule, problems


class RulesView(_Guarded):
    def get(self, request, slug):
        p = _provider(slug)
        return Response({"results": [_rule_row(r) for r in p.rules.select_related("workspace")]})

    @transaction.atomic
    def put(self, request, slug):
        p = _provider(slug)
        raw = request.data.get("rules") if isinstance(request.data, dict) else None
        if not isinstance(raw, list) or len(raw) > MAX_RULES:
            _fail({"rules": f"Une liste de {MAX_RULES} règles au plus."})
        spaces = {w.slug: w for w in Workspace.objects.filter(archived_at__isnull=True)}
        built, problems = [], {}
        for i, item in enumerate(raw):
            rule, found = _build_rule(p, i, item, spaces)
            problems.update(found)
            if rule is not None:
                built.append(rule)
        if problems:
            _fail(problems)
        p.rules.all().delete()
        GroupRule.objects.bulk_create(built)
        record("platform.sso_rules", actor=request.user, target=p, provider=slug, rules=len(built))
        return Response({"results": [_rule_row(r) for r in p.rules.select_related("workspace")]})


class SimulateView(_Guarded):
    """Ce que donneraient ces groupes avec les règles ENREGISTRÉES de ce fournisseur ; n'écrit rien."""

    def post(self, request, slug):
        p = _provider(slug)
        groups = sso_engine.clean_groups(request.data.get("groups") if isinstance(request.data, dict) else None)
        return Response(sso_engine.describe(p, groups))
