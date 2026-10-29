"""Liveness vs readiness vs a human-readable system health summary, plus Prometheus metrics."""

from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from runs.models import CallbackDelivery, Execution, Runner, ServiceInstance, StepRun, WorkflowRun
from tenancy.permissions import ADMINISTER, get_workspace

STALE = timedelta(seconds=30)


def live(request):
    """Liveness: the process answers. Never touches dependencies (a restart cannot fix a dead database)."""
    return JsonResponse({"status": "alive"})


def ready(request):
    """Readiness: the control plane can serve traffic (database reachable). 503 otherwise."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:  # noqa: BLE001
        return JsonResponse({"status": "unavailable", "database": "down"}, status=503)
    return JsonResponse({"status": "ready", "database": "up"})


def _item(key, label, status, message, hint=""):
    return {"key": key, "label": label, "status": status, "message": message, "hint": hint}


def _runners_item(workspace, now) -> dict:
    """Who will run this space's actions: dedicated runners, the shared pool, or nobody (jobs would wait forever)."""
    window = timedelta(seconds=settings.ORCH_RUNNER_ONLINE_SECONDS)
    live = lambda qs: qs.filter(paused=False, last_contact_at__gte=now - window).count()  # noqa: E731
    dedicated = live(Runner.objects.filter(scope="dedicated", workspaces=workspace))
    waiting = Execution.objects.filter(workspace=workspace, status="queued").count()
    if not workspace.use_shared_runners and dedicated == 0:
        return _item(
            "runners",
            "Runners",
            "error",
            f"Aucun runner dédié actif et les runners partagés sont désactivés : {waiting} action(s) en attente.",
            "Démarrez un runner dédié (Administration → Runners) ou réactivez les runners partagés.",
        )
    where = f"{dedicated} runner(s) dédié(s) actif(s)" + (
        ", runners partagés autorisés." if workspace.use_shared_runners else ", runners partagés désactivés."
    )
    return _item("runners", "Runners", "ok", where)


def summary(workspace=None) -> list[dict]:
    now = timezone.now()
    items = []
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        items.append(_item("db", "Base de données", "ok", "La base de données répond."))
    except Exception:  # noqa: BLE001
        return [
            _item(
                "db",
                "Base de données",
                "error",
                "La base de données ne répond pas.",
                "Vérifiez PostgreSQL (scripts/doctor.sh).",
            )
        ]
    orch = ServiceInstance.objects.filter(kind="orchestrator").order_by("-last_seen_at").first()
    if orch is None or now - orch.last_seen_at > STALE:
        age = "jamais" if orch is None else f"il y a {int((now - orch.last_seen_at).total_seconds())} s"
        items.append(
            _item(
                "orchestrator",
                "Orchestrateur",
                "error",
                f"L'orchestrateur ne donne plus signe de vie (vu : {age}).",
                "Les workflows n'avancent plus. Relancez le processus orchestrateur (scripts/dev-up.sh).",
            )
        )
    else:
        items.append(_item("orchestrator", "Orchestrateur", "ok", "L'orchestrateur est actif."))
    workers = [w for w in ServiceInstance.objects.filter(kind="worker") if now - w.last_seen_at <= STALE]
    queued = Execution.objects.filter(status="queued")
    if not workers:
        items.append(
            _item(
                "workers",
                "Workers",
                "error",
                "Aucun worker actif.",
                "Les actions ne s'exécutent plus. Relancez un worker.",
            )
        )
    else:
        items.append(_item("workers", "Workers", "ok", f"{len(workers)} worker(s) actif(s)."))
    depth = queued.count()
    oldest = queued.order_by("created_at").first()
    wait = int((now - oldest.created_at).total_seconds()) if oldest else 0
    if depth and wait > 120:
        items.append(
            _item(
                "queue",
                "File d'attente",
                "warn",
                f"{depth} action(s) en attente, la plus ancienne depuis {wait} s.",
                "Augmentez le nombre de workers ou vérifiez le plafond de concurrence par espace.",
            )
        )
    else:
        items.append(_item("queue", "File d'attente", "ok", f"{depth} action(s) en attente."))
    stuck = Execution.objects.filter(status="running", lease_expires_at__lt=now - STALE).count()
    if stuck:
        items.append(
            _item(
                "leases",
                "Exécutions orphelines",
                "warn",
                f"{stuck} exécution(s) sans worker (lease expirée).",
                "Elles seront reprises automatiquement ; sinon vérifiez les workers.",
            )
        )
    uncertain = StepRun.objects.filter(status="uncertain")
    if workspace:
        uncertain = uncertain.filter(run__workspace=workspace)
    if uncertain.exists():
        items.append(
            _item(
                "uncertain",
                "Effets externes incertains",
                "warn",
                f"{uncertain.count()} étape(s) attendent une décision humaine.",
                "Ouvrez l'exécution concernée et indiquez si l'effet a eu lieu.",
            )
        )
    if workspace is not None:
        items.append(_runners_item(workspace, now))
    failed_cb = CallbackDelivery.objects.filter(status="failed").count()
    pending_cb = CallbackDelivery.objects.filter(status="pending", attempts__gt=0).count()
    if failed_cb or pending_cb:
        items.append(
            _item(
                "callbacks",
                "Rappels sortants",
                "warn",
                f"{pending_cb} en nouvelle tentative, {failed_cb} abandonné(s).",
                "Vérifiez que l'adresse de rappel est joignable et autorisée.",
            )
        )
    return items


class SystemHealthView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        items = summary(ws)
        overall = (
            "error"
            if any(i["status"] == "error" for i in items)
            else "warn"
            if any(i["status"] == "warn" for i in items)
            else "ok"
        )
        return Response(
            {
                "status": overall,
                "items": items,
                "running_runs": WorkflowRun.objects.filter(
                    workspace=ws, status__in=["running", "compensating"]
                ).count(),
            }
        )


class MetricsView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        token = settings.ORCH_METRICS_TOKEN
        if not token:
            raise Http404()
        import hmac

        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            return HttpResponse(status=401)
        now = timezone.now()
        lines = []

        def gauge(name, help_, value, labels=""):
            lines.extend([f"# HELP {name} {help_}", f"# TYPE {name} gauge", f"{name}{labels} {value}"])

        for status in ("queued", "running", "succeeded", "failed", "cancelled"):
            gauge(
                "fluxonata_executions",
                "Action executions by status",
                Execution.objects.filter(status=status).count(),
                f'{{status="{status}"}}',
            )
        for status in ("running", "compensating", "succeeded", "failed", "cancelled"):
            gauge(
                "fluxonata_workflow_runs",
                "Workflow runs by status",
                WorkflowRun.objects.filter(status=status).count(),
                f'{{status="{status}"}}',
            )
        gauge(
            "fluxonata_workers_alive",
            "Workers seen in the last 30 s",
            sum(1 for w in ServiceInstance.objects.filter(kind="worker") if now - w.last_seen_at <= STALE),
        )
        gauge(
            "fluxonata_uncertain_steps",
            "Steps awaiting a human decision",
            StepRun.objects.filter(status="uncertain").count(),
        )
        gauge(
            "fluxonata_callbacks_failed",
            "Abandoned outbound callbacks",
            CallbackDelivery.objects.filter(status="failed").count(),
        )
        return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; version=0.0.4")
