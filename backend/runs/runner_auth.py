"""Authentication of runner agents: `Authorization: Bearer orr_…`. Only the SHA-256 of a token is stored; a revoked
(deleted) or rotated token stops working immediately."""

import hashlib
import secrets

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.throttling import SimpleRateThrottle

from .models import Runner

TOKEN_PREFIX = "orr_"  # noqa: S105 - a public prefix, not a secret


def new_token() -> tuple[str, str, str]:
    """(raw token — shown once —, sha256 hex, display prefix)."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[:12]


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class RunnerPrincipal:
    """What `request.user` is for a runner: authenticated, never a person, never a platform administrator."""

    is_authenticated = True
    is_anonymous = False
    is_superuser = False
    is_staff = False
    _token = None

    def __init__(self, runner: Runner):
        self.runner = runner
        self.pk = runner.pk
        self.username = f"runner:{runner.name}"


class RunnerAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer " + TOKEN_PREFIX):
            return None
        runner = Runner.objects.filter(token_hash=hash_token(header[len("Bearer ") :].strip())).first()
        if runner is None:
            raise AuthenticationFailed("Jeton de runner invalide ou révoqué.")
        ip = request.META.get("REMOTE_ADDR")
        Runner.objects.filter(pk=runner.pk).update(last_contact_at=timezone.now(), last_ip=ip or None)
        runner.last_contact_at, runner.last_ip = timezone.now(), ip
        return RunnerPrincipal(runner), runner

    def authenticate_header(self, request):
        return 'Bearer realm="runner"'


class IsRunner(BasePermission):
    def has_permission(self, request, view):
        if not isinstance(request.user, RunnerPrincipal):
            raise PermissionDenied("Réservé aux agents runner (jeton « orr_… » requis).")
        return True


class RunnerThrottle(SimpleRateThrottle):
    """Per runner (not per IP, not shared between runners)."""

    scope = "runner"

    def get_cache_key(self, request, view):
        runner = getattr(request, "auth", None)
        return self.cache_format % {"scope": self.scope, "ident": str(runner.pk) if runner else self.get_ident(request)}
