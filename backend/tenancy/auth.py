import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import ApiToken

TOKEN_PREFIX = "orch_"  # noqa: S105 - public prefix, not a secret
TOKEN_SCOPES = {"view", "run", "edit", "publish"}  # never approve / administer / manage_connections


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str, str]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:12], hash_token(raw)


class BearerTokenAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate_header(self, request):
        return self.keyword

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        raw = header[7:].strip()
        if not raw.startswith(TOKEN_PREFIX):
            raise AuthenticationFailed("Jeton invalide.")
        token = ApiToken.objects.select_related("service_account__user").filter(token_hash=hash_token(raw)).first()
        now = timezone.now()
        if token is None or token.revoked_at is not None or token.expires_at <= now:
            raise AuthenticationFailed("Jeton invalide, expiré ou révoqué.")
        user = token.service_account.user
        if not user.is_active:
            raise AuthenticationFailed("Compte de service désactivé.")
        user._token = token  # consulted by get_workspace / governance
        if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=1):
            ApiToken.objects.filter(pk=token.pk).update(last_used_at=now)
        return user, token
