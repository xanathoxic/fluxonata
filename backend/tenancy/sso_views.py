"""Connexion par un fournisseur d'identité réglé dans l'interface : liste publique, départ, retour (OpenID Connect) et réponse (SAML).

Une erreur ne montre jamais de JSON à la personne : elle revient sur la page de connexion avec un message (`?sso_error=`)."""

from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import login
from django.http import Http404, HttpResponse, HttpResponseRedirect
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record

from . import oidc, sso_oidc, sso_saml
from .models import IdentityProvider
from .sso_engine import SsoDenied
from .sso_engine import login as provision


def _back(message: str) -> HttpResponseRedirect:
    return HttpResponseRedirect("/?" + urlencode({"sso_error": message[:200]}))


def _enabled(slug: str) -> IdentityProvider:
    p = IdentityProvider.objects.filter(slug=slug, enabled=True).first()
    if p is None:
        raise Http404()
    return p


class _Public(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []


class ProvidersView(_Public):
    def get(self, request):
        rows = [
            {"slug": p.slug, "name": p.name, "kind": p.kind, "login_url": f"/api/v1/auth/sso/{p.slug}/login/"}
            for p in IdentityProvider.objects.filter(enabled=True)
        ]
        if oidc.enabled():
            rows.append(
                {
                    "slug": "environment",
                    "name": "SSO de l'organisation",
                    "kind": "oidc",
                    "login_url": "/api/v1/auth/oidc/login/",
                }
            )
        return Response({"results": rows})


class LoginView(_Public):
    def get(self, request, slug):
        p = _enabled(slug)
        try:
            url = sso_oidc.begin(request, p) if p.kind == IdentityProvider.Kind.OIDC else sso_saml.begin(request, p)
        except (oidc.OidcError, sso_saml.SamlError) as exc:
            record("auth.sso_failed", reason=exc.message[:150], provider=slug)
            return _back(exc.message)
        return HttpResponseRedirect(url)


def _finish(request, p: IdentityProvider, identity) -> HttpResponseRedirect:
    try:
        user = provision(identity)
    except SsoDenied as exc:
        record("auth.sso_failed", reason=exc.message[:150], provider=p.slug)
        return _back(exc.message)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    record("auth.login", actor=user, method=p.kind, provider=p.slug)
    return HttpResponseRedirect(settings.ORCH_OIDC_POST_LOGIN_URL)


class CallbackView(_Public):
    def get(self, request, slug):
        p = _enabled(slug)
        if p.kind != IdentityProvider.Kind.OIDC:
            raise Http404()
        try:
            identity = sso_oidc.complete(request, p)
        except oidc.OidcError as exc:
            record("auth.sso_failed", reason=exc.message[:150], provider=slug)
            return _back(exc.message)
        return _finish(request, p, identity)


class AcsView(_Public):
    """Point où le fournisseur SAML dépose sa réponse (liaison HTTP-POST). Pas de jeton CSRF : la protection est le RelayState lié à la session."""

    def post(self, request, slug):
        p = _enabled(slug)
        if p.kind != IdentityProvider.Kind.SAML:
            raise Http404()
        try:
            identity = sso_saml.complete(request, p)
        except sso_saml.SamlError as exc:
            record("auth.sso_failed", reason=exc.message[:150], provider=slug)
            return _back(exc.message)
        return _finish(request, p, identity)


class MetadataView(_Public):
    def get(self, request, slug):
        p = IdentityProvider.objects.filter(slug=slug, kind=IdentityProvider.Kind.SAML).first()
        if p is None:
            raise Http404()
        return HttpResponse(sso_saml.sp_metadata(p, request), content_type="application/samlmetadata+xml")
