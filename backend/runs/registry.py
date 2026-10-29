import os
import socket
import time

from django.utils import timezone

from .models import ServiceInstance

_last: dict[str, float] = {}


def beat(kind: str, name: str, min_interval: float = 3.0, **info) -> None:
    """Upsert this process' heartbeat (throttled)."""
    now = time.monotonic()
    if now - _last.get(name, -1e9) < min_interval:
        return
    _last[name] = now
    ServiceInstance.objects.update_or_create(
        name=name,
        defaults={
            "kind": kind,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "last_seen_at": timezone.now(),
            "info": info,
        },
    )
