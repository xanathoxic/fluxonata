"""Management of runners: by platform administrators (shared or dedicated, any space) and by space administrators
(runners dedicated to their own space only, like a GitLab project runner). The token is returned once, at creation or
rotation; only its hash is kept."""

import re

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from catalog.library import TAG_RE
from tenancy.models import Workspace
from tenancy.permissions import ADMINISTER, get_workspace, require_platform_admin

from .models import Runner
from .runner_auth import new_token
from .runners import RUNTIMES

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,58}[a-z0-9]")
ALL_RUNTIMES = sorted(RUNTIMES)  # never "ai": AI steps run on the platform itself (they need the AI provider secrets)


def runner_row(r: Runner) -> dict:
    online = (
        r.last_contact_at is not None
        and (timezone.now() - r.last_contact_at).total_seconds() <= settings.ORCH_RUNNER_ONLINE_SECONDS
    )
    return {
        "id": str(r.id),
        "name": r.name,
        "description": r.description,
        "scope": r.scope,
        "workspaces": sorted(w.slug for w in r.workspaces.all()),
        "tags": r.tags,
        "runtimes": r.runtimes,
        "max_concurrent": r.max_concurrent,
        "paused": r.paused,
        "status": "paused" if r.paused else ("online" if online else ("offline" if r.last_contact_at else "never")),
        "last_contact_at": r.last_contact_at,
        "last_ip": r.last_ip,
        "agent_version": r.agent_version,
        "platform": r.platform,
        "token_prefix": r.token_prefix,
        "created_by": r.created_by.username if r.created_by else None,
        "created_at": r.created_at,
        "running_jobs": r.executions.filter(status="running").count(),
    }


class RunnerIn(serializers.Serializer):
    name = serializers.CharField(max_length=60)
    description = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    scope = serializers.ChoiceField(choices=["shared", "dedicated"], required=False, default="dedicated")
    workspaces = serializers.ListField(child=serializers.SlugField(), required=False, default=list, max_length=50)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=30), required=False, default=list, max_length=10
    )
    runtimes = serializers.ListField(child=serializers.CharField(), required=False, default=lambda: list(ALL_RUNTIMES))
    max_concurrent = serializers.IntegerField(min_value=1, max_value=32, required=False, default=2)

    def validate_name(self, v):
        if not NAME_RE.fullmatch(v):
            raise serializers.ValidationError("3 à 60 caractères : minuscules, chiffres, - et _.")
        return v

    def validate_tags(self, tags):
        if any(not TAG_RE.fullmatch(t) for t in tags):
            raise serializers.ValidationError("Étiquettes : minuscules, chiffres, - _ . (30 caractères au plus).")
        return sorted(set(tags))

    def validate_runtimes(self, rt):
        if not rt or any(r not in ALL_RUNTIMES for r in rt):
            raise serializers.ValidationError(f"Runtimes autorisés : {', '.join(ALL_RUNTIMES)}.")
        return sorted(set(rt))


def _workspaces(slugs: list[str]) -> list[Workspace]:
    found = list(Workspace.objects.filter(slug__in=slugs, archived_at__isnull=True))
    missing = set(slugs) - {w.slug for w in found}
    if missing:
        raise serializers.ValidationError({"workspaces": f"Espace(s) inconnu(s) : {', '.join(sorted(missing))}."})
    return found


@transaction.atomic
def create_runner(data: dict, user, *, workspaces: list[Workspace], scope: str) -> tuple[Runner, str]:
    if Runner.objects.filter(name=data["name"]).exists():
        raise serializers.ValidationError({"name": "Ce nom est déjà utilisé par un autre runner."})
    if scope == "dedicated" and not workspaces:
        raise serializers.ValidationError({"workspaces": "Un runner dédié sert au moins un espace."})
    raw, digest, prefix = new_token()
    r = Runner.objects.create(
        name=data["name"],
        description=data["description"],
        scope=scope,
        tags=data["tags"],
        runtimes=data["runtimes"],
        max_concurrent=data["max_concurrent"],
        token_hash=digest,
        token_prefix=prefix,
        created_by=user,
    )
    if scope == "dedicated":
        r.workspaces.set(workspaces)
    record(
        "runner.create",
        actor=user,
        workspace=workspaces[0] if len(workspaces) == 1 else None,
        target=r,
        name=r.name,
        scope=scope,
        workspaces=[w.slug for w in workspaces],
    )
    return r, raw


@transaction.atomic
def update_runner(r: Runner, d: dict, user, *, allow_scope: bool) -> Runner:
    for key in ("description", "tags", "runtimes", "max_concurrent", "paused"):
        if key in d:
            setattr(r, key, d[key])
    if "name" in d and d["name"] != r.name:
        if Runner.objects.filter(name=d["name"]).exclude(pk=r.pk).exists():
            raise serializers.ValidationError({"name": "Ce nom est déjà utilisé par un autre runner."})
        r.name = d["name"]
    if allow_scope and ("scope" in d or "workspaces" in d):
        scope = d.get("scope", r.scope)
        spaces = _workspaces(d["workspaces"]) if "workspaces" in d else list(r.workspaces.all())
        if scope == "dedicated" and not spaces:
            raise serializers.ValidationError({"workspaces": "Un runner dédié sert au moins un espace."})
        r.scope = scope
        r.save()
        r.workspaces.set(spaces if scope == "dedicated" else [])
    r.save()
    record("runner.update", actor=user, target=r, name=r.name, fields=sorted(d))
    return r


class _PartialRunnerIn(RunnerIn):
    """PATCH: every field optional, nothing defaulted (a missing field is left alone)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.required, f.default = False, serializers.empty
        self.fields["paused"] = serializers.BooleanField(required=False)


def _patch_data(request) -> dict:
    ser = _PartialRunnerIn(data=request.data)
    ser.is_valid(raise_exception=True)
    return dict(ser.validated_data)


def _rotate(r: Runner, user) -> str:
    raw, digest, prefix = new_token()
    r.token_hash, r.token_prefix = digest, prefix
    r.save(update_fields=["token_hash", "token_prefix"])
    record("runner.rotate_token", actor=user, target=r, name=r.name)
    return raw


def _with_token(r: Runner, raw: str, status=200):
    return Response(
        {**runner_row(r), "token": raw, "token_notice": "Ce jeton ne sera plus jamais affiché : copiez-le maintenant."},
        status=status,
    )


# ------------------------------------------------------------------ platform administrators
class _Guarded(APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_platform_admin(request)


class PlatformRunnersView(_Guarded):
    def get(self, request):
        rows = Runner.objects.prefetch_related("workspaces").select_related("created_by")
        return Response({"results": [runner_row(r) for r in rows]})

    def post(self, request):
        ser = RunnerIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        spaces = _workspaces(d["workspaces"]) if d["scope"] == "dedicated" else []
        r, raw = create_runner(d, request.user, workspaces=spaces, scope=d["scope"])
        return _with_token(r, raw, 201)


def _runner_or_404(pk) -> Runner:
    r = Runner.objects.prefetch_related("workspaces").filter(pk=pk).first()
    if r is None:
        raise Http404()
    return r


class PlatformRunnerDetailView(_Guarded):
    def patch(self, request, pk):
        r = update_runner(_runner_or_404(pk), _patch_data(request), request.user, allow_scope=True)
        return Response(runner_row(r))

    def delete(self, request, pk):
        r = _runner_or_404(pk)
        record("runner.delete", actor=request.user, name=r.name)
        r.delete()
        return Response(status=204)


class PlatformRunnerRotateView(_Guarded):
    def post(self, request, pk):
        r = _runner_or_404(pk)
        return _with_token(r, _rotate(r, request.user))


# ------------------------------------------------------------------ space administrators
def _managed(r: Runner, ws: Workspace) -> bool:
    """A space administrator manages a runner only if it is dedicated to this space alone."""
    return r.scope == "dedicated" and [w.pk for w in r.workspaces.all()] == [ws.pk]


class WorkspaceRunnersView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        mine = (
            Runner.objects.filter(scope="dedicated", workspaces=ws)
            .prefetch_related("workspaces")
            .select_related("created_by")
        )
        shared = Runner.objects.filter(scope="shared")
        return Response(
            {
                "results": [{**runner_row(r), "managed": _managed(r, ws)} for r in mine],
                "use_shared_runners": ws.use_shared_runners,
                "shared_online": sum(1 for r in shared if runner_row(r)["status"] == "online"),
            }
        )

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = RunnerIn(data=request.data)
        ser.is_valid(raise_exception=True)
        r, raw = create_runner(
            ser.validated_data, request.user, workspaces=[ws], scope="dedicated"
        )  # whatever scope was asked
        return _with_token(r, raw, 201)


def _managed_or_error(request, workspace, pk) -> tuple[Workspace, Runner]:
    ws = get_workspace(request.user, workspace, ADMINISTER)
    r = Runner.objects.prefetch_related("workspaces").filter(pk=pk, scope="dedicated", workspaces=ws).first()
    if r is None:
        raise Http404()  # shared runners and other spaces' runners are not visible here
    if not _managed(r, ws):
        raise PermissionDenied(
            "Ce runner sert aussi d'autres espaces : il est géré par les administrateurs de la plate-forme."
        )
    return ws, r


class WorkspaceRunnerDetailView(APIView):
    def patch(self, request, workspace, pk):
        _, r = _managed_or_error(request, workspace, pk)
        d = _patch_data(request)
        d.pop("scope", None)
        d.pop("workspaces", None)  # a space administrator cannot widen a runner beyond their own space
        return Response(runner_row(update_runner(r, d, request.user, allow_scope=False)))

    def delete(self, request, workspace, pk):
        ws, r = _managed_or_error(request, workspace, pk)
        record("runner.delete", actor=request.user, workspace=ws, name=r.name)
        r.delete()
        return Response(status=204)


class WorkspaceRunnerRotateView(APIView):
    def post(self, request, workspace, pk):
        _, r = _managed_or_error(request, workspace, pk)
        return _with_token(r, _rotate(r, request.user))


class WorkspaceRunnerSettingsView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        return Response({"use_shared_runners": ws.use_shared_runners})

    def patch(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        value = request.data.get("use_shared_runners")
        if not isinstance(value, bool):
            raise serializers.ValidationError({"use_shared_runners": "Un booléen est attendu."})
        if value != ws.use_shared_runners:
            ws.use_shared_runners = value
            ws.save(update_fields=["use_shared_runners"])
            record("workspace.runner_settings", actor=request.user, workspace=ws, use_shared_runners=value)
        return Response({"use_shared_runners": ws.use_shared_runners})
