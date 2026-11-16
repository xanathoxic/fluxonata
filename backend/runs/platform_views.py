"""The shared worker fleet, seen by platform administrators (list, forget the ones that stopped reporting)."""

from datetime import timedelta

from django.utils import timezone
from rest_framework.response import Response

from audit.models import record
from tenancy.platform_views import _Guarded

from .health import STALE
from .models import ServiceInstance


class WorkersView(_Guarded):
    def get(self, request):
        now = timezone.now()
        rows = ServiceInstance.objects.order_by("kind", "name")
        return Response(
            {
                "results": [
                    {
                        "kind": w.kind,
                        "name": w.name,
                        "hostname": w.hostname,
                        "pid": w.pid,
                        "started_at": w.started_at,
                        "last_seen_at": w.last_seen_at,
                        "alive": now - w.last_seen_at <= STALE,
                        "info": w.info,
                    }
                    for w in rows
                ]
            }
        )


class WorkersPurgeView(_Guarded):
    """Forget processes that stopped reporting (never touches a live one). Starting/stopping processes is an operations task."""

    def post(self, request):
        cutoff = timezone.now() - max(STALE, timedelta(minutes=1))
        stale = ServiceInstance.objects.filter(last_seen_at__lt=cutoff)
        n = stale.count()
        stale.delete()
        record("platform.workers_purge", actor=request.user, removed=n)
        return Response({"removed": n})
