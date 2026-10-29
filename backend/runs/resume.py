"""Resume URLs of « Attendre » steps set to resume on an HTTP call.

The URL is a capability: `<step id>.<HMAC of the step id>`. Nothing secret is stored (the token is recomputed from the
server key for people allowed to run the workflow); an unknown or forged token answers like a missing page."""

import hashlib
import hmac

from django.conf import settings


def _mac(step_id: int) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"resume:{step_id}".encode(), hashlib.sha256).hexdigest()[:32]


def token_for(step_id: int) -> str:
    return f"{step_id}.{_mac(step_id)}"


def step_id_from(token: str) -> int | None:
    head, _, mac = token.partition(".")
    if not head.isdigit() or not hmac.compare_digest(_mac(int(head)), mac):
        return None
    return int(head)
