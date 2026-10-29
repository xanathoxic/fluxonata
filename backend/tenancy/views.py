from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.http import Http404
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.models import record

from .models import Membership, UserProfile
from .permissions import ROLE_PERMISSIONS


def _me_payload(user):
    memberships = (
        Membership.objects.filter(user=user, workspace__archived_at__isnull=True)
        .select_related("workspace__organization")
        .order_by("id")  # stable (oldest membership first): the first one is the default space
    )
    return {
        "username": user.username,
        "email": user.email,
        "sso_only": not user.has_usable_password(),
        "must_change_password": UserProfile.objects.filter(user=user, must_change_password=True).exists(),
        "is_platform_admin": bool(user.is_superuser and getattr(user, "_token", None) is None),
        "workspaces": [
            {
                "slug": m.workspace.slug,
                "name": m.workspace.name,
                "role": m.role,
                "permissions": sorted(ROLE_PERMISSIONS[m.role]),
                "environments": [{"kind": e.kind, "name": e.name} for e in m.workspace.environments.order_by("kind")],
            }
            for m in memberships
        ],
    }


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = LoginSerializer

    def post(self, request):
        SessionAuthentication().enforce_csrf(request)
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = authenticate(request, **ser.validated_data)
        if user is None:
            record("auth.login_failed", username=ser.validated_data["username"][:150])
            return Response(
                {"error": {"code": "invalid_credentials", "message": "Identifiants incorrects.", "details": None}},
                status=401,
            )
        login(request, user)
        record("auth.login", actor=user)
        return Response(_me_payload(user))


class LogoutView(APIView):
    allow_password_change = True

    def post(self, request):
        record("auth.logout", actor=request.user)
        logout(request)
        return Response(status=204)


class MeView(APIView):
    allow_password_change = True

    def get(self, request):
        return Response(_me_payload(request.user))


class OidcConfigView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from . import oidc

        return Response(
            {"enabled": oidc.enabled(), "login_url": "/api/v1/auth/oidc/login/" if oidc.enabled() else None}
        )


class OidcLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.http import HttpResponseRedirect

        from . import oidc

        if not oidc.enabled():
            raise Http404()
        try:
            return HttpResponseRedirect(oidc.begin(request))
        except oidc.OidcError as exc:
            return Response(
                {"error": {"code": "oidc_unavailable", "message": exc.message, "details": None}}, status=502
            )


class OidcCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.http import HttpResponseRedirect

        from . import oidc

        if not oidc.enabled():
            raise Http404()
        try:
            user = oidc.complete(request)
        except oidc.OidcError as exc:
            record("auth.sso_failed", reason=exc.message[:150])
            return Response({"error": {"code": "oidc_failed", "message": exc.message, "details": None}}, status=401)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        record("auth.login", actor=user, method="oidc")
        return HttpResponseRedirect(settings.ORCH_OIDC_POST_LOGIN_URL)
