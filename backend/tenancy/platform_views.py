"""Platform administration: workspaces, the shared worker fleet and platform administrators.

Everything here requires the platform-administrator flag (`is_superuser`) and is audited. These routes are NOT under
/workspaces/<workspace>/, so the cross-workspace isolation sweep does not cover them: tests/test_platform.py does."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditEvent, record
from core import netsec
from core.health import STALE
from runs.models import ServiceInstance

from .models import EgressHost, Environment, Membership, Organization, Role, Workspace
from .permissions import require_platform_admin

User = get_user_model()
ENVIRONMENTS = (("dev", "Développement"), ("test", "Recette"), ("prod", "Production"))


def _workspace_row(w: Workspace) -> dict:
    return {
        "slug": w.slug,
        "name": w.name,
        "organization": w.organization.name,
        "members": w.memberships.count(),
        "admins": list(w.memberships.filter(role=Role.ADMIN).values_list("user__username", flat=True)),
        "environments": [e.kind for e in w.environments.order_by("kind")],
        "archived": w.archived_at is not None,
        "archived_at": w.archived_at,
    }


class _Guarded(APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_platform_admin(request)


class CreateWorkspaceSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(max_length=50)
    admin_username = serializers.CharField(required=False, allow_blank=True)

    def validate_slug(self, v):
        if Workspace.objects.filter(slug=v).exists():
            raise serializers.ValidationError("Cet identifiant est déjà utilisé.")
        return v


class WorkspacesView(_Guarded):
    def get(self, request):
        rows = Workspace.objects.select_related("organization").order_by("name")
        return Response({"results": [_workspace_row(w) for w in rows]})

    @transaction.atomic
    def post(self, request):
        s = CreateWorkspaceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        admin = User.objects.filter(username=d.get("admin_username") or request.user.username).first()
        if admin is None:
            raise serializers.ValidationError({"admin_username": "Ce compte n'existe pas."})
        org = Organization.objects.order_by("id").first() or Organization.objects.create(
            name="Mon organisation", slug="main"
        )
        ws = Workspace.objects.create(organization=org, name=d["name"], slug=d["slug"])
        for kind, name in ENVIRONMENTS:
            Environment.objects.create(workspace=ws, kind=kind, name=name)
        Membership.objects.create(user=admin, workspace=ws, role=Role.ADMIN)
        record("platform.workspace_create", actor=request.user, workspace=ws, target=ws, admin=admin.username)
        return Response(_workspace_row(ws), status=201)


class WorkspaceDetailView(_Guarded):
    def patch(self, request, slug):
        ws = Workspace.objects.select_related("organization").filter(slug=slug).first()
        if ws is None:
            return Response(
                {"error": {"code": "not_found", "message": "Espace introuvable.", "details": None}}, status=404
            )
        name = request.data.get("name")
        archived = request.data.get("archived")
        if name is not None:
            name = str(name).strip()
            if not name or len(name) > 120:
                raise serializers.ValidationError({"name": "Nom requis (120 caractères maximum)."})
            if name != ws.name:
                record("platform.workspace_rename", actor=request.user, workspace=ws, target=ws, old=ws.name, new=name)
                ws.name = name
        if archived is not None and bool(archived) != (ws.archived_at is not None):
            if archived:
                ws.archived_at = timezone.now()
                ws.schedules.update(enabled=False)
                record("platform.workspace_archive", actor=request.user, workspace=ws, target=ws)
            else:
                ws.archived_at = None
                record("platform.workspace_restore", actor=request.user, workspace=ws, target=ws)
        ws.save()
        return Response(_workspace_row(ws))


class WorkspaceAdminView(_Guarded):
    """Give an existing account the administrator role of a space (recovery path when a space has no administrator)."""

    def post(self, request, slug):
        ws = Workspace.objects.filter(slug=slug).first()
        if ws is None:
            return Response(
                {"error": {"code": "not_found", "message": "Espace introuvable.", "details": None}}, status=404
            )
        user = User.objects.filter(username=str(request.data.get("username", ""))).first()
        if user is None:
            raise serializers.ValidationError({"username": "Ce compte n'existe pas."})
        m, created = Membership.objects.update_or_create(user=user, workspace=ws, defaults={"role": Role.ADMIN})
        record(
            "platform.workspace_admin_grant",
            actor=request.user,
            workspace=ws,
            target=ws,
            user=user.username,
            created=created,
        )
        return Response(_workspace_row(ws), status=201 if created else 200)


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


class PlatformAdminsView(_Guarded):
    def get(self, request):
        users = User.objects.filter(is_superuser=True, is_active=True).order_by("username")
        return Response({"results": [{"username": u.username, "you": u.pk == request.user.pk} for u in users]})

    def post(self, request):
        user = User.objects.filter(username=str(request.data.get("username", "")), is_active=True).first()
        if user is None:
            raise serializers.ValidationError({"username": "Ce compte n'existe pas."})
        if hasattr(user, "service_account"):
            raise serializers.ValidationError(
                {"username": "Un compte de service ne peut pas être administrateur de la plate-forme."}
            )
        if not user.is_superuser:
            user.is_superuser = True
            user.save(update_fields=["is_superuser"])
            record("platform.admin_grant", actor=request.user, user=user.username)
        return Response({"username": user.username}, status=201)


class PlatformAdminDetailView(_Guarded):
    def delete(self, request, username):
        user = User.objects.filter(username=username, is_superuser=True).first()
        if user is None:
            return Response(
                {
                    "error": {
                        "code": "not_found",
                        "message": "Ce compte n'est pas administrateur de la plate-forme.",
                        "details": None,
                    }
                },
                status=404,
            )
        if User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).count() == 0:
            raise serializers.ValidationError(
                {"username": "Il doit rester au moins un administrateur de la plate-forme."}
            )
        user.is_superuser = False
        user.save(update_fields=["is_superuser"])
        record("platform.admin_revoke", actor=request.user, user=user.username)
        return Response(status=204)


class PlatformAuditView(_Guarded):
    def get(self, request):
        rows = AuditEvent.objects.filter(action__startswith="platform.").order_by("-id")[:200]
        return Response(
            {
                "results": [
                    {
                        "id": e.id,
                        "created_at": e.created_at,
                        "actor": e.actor_label,
                        "action": e.action,
                        "workspace": e.workspace.slug if e.workspace else None,
                        "data": e.data,
                    }
                    for e in rows
                ]
            }
        )


MAX_EGRESS_HOSTS = 100


def _egress_row(h: EgressHost) -> dict:
    return {
        "id": h.pk,
        "host": h.host,
        "note": h.note,
        "created_by": h.created_by.username if h.created_by else "",
        "created_at": h.created_at,
    }


class EgressSerializer(serializers.Serializer):
    host = serializers.CharField(max_length=300)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class EgressView(_Guarded):
    """Internal hosts the platform's server may call (SSRF guard exceptions). Those of the environment (`ORCH_EGRESS_ALLOWLIST`) are
    shown read-only; the others are added and removed here, by platform administrators only, each change audited. A change applies
    within seconds to every process."""

    serializer_class = EgressSerializer

    def get(self, request):
        rows = EgressHost.objects.select_related("created_by")
        return Response({"environment": sorted(netsec.env_allowlist()), "results": [_egress_row(h) for h in rows]})

    def post(self, request):
        ser = EgressSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            host = netsec.check_managed_host(ser.validated_data["host"])
        except netsec.UnsafeURL as exc:
            raise serializers.ValidationError({"host": str(exc)}) from None
        if host in netsec.env_allowlist() or EgressHost.objects.filter(host=host).exists():
            raise serializers.ValidationError({"host": "Cette adresse est déjà autorisée."})
        if EgressHost.objects.count() >= MAX_EGRESS_HOSTS:
            raise serializers.ValidationError({"host": f"{MAX_EGRESS_HOSTS} adresses au plus."})
        row = EgressHost.objects.create(host=host, note=ser.validated_data["note"].strip(), created_by=request.user)
        netsec.reset_allowlist_cache()
        record("platform.egress_add", actor=request.user, target=row, host=host, note=row.note)
        return Response(_egress_row(row), status=201)


class EgressDetailView(_Guarded):
    def delete(self, request, pk):
        row = EgressHost.objects.filter(pk=pk).first()
        if row is None:
            raise Http404()
        host = row.host
        row.delete()
        netsec.reset_allowlist_cache()
        record("platform.egress_remove", actor=request.user, host=host)
        return Response(status=204)
