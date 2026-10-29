"""Git synchronisation: configuration of the repository and the operations (run by a worker, followed by polling)."""

from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.permissions import ADMINISTER, EDIT, get_workspace
from vault.models import Connection

from . import gitsync
from .models import GitJob, GitRepo


def _repo_row(r: GitRepo | None) -> dict | None:
    return (
        None
        if r is None
        else {
            "provider": r.provider,
            "repo": r.repo,
            "branch": r.branch,
            "path": r.path,
            "connection": r.connection,
        }
    )


def _job_row(j: GitJob) -> dict:
    return {
        "id": j.pk,
        "kind": j.kind,
        "status": j.status,
        "status_label": j.get_status_display(),
        "params": j.params,
        "result": j.result,
        "error": j.error,
        "requested_by": j.requested_by.get_username() if j.requested_by else None,
        "created_at": j.created_at,
        "finished_at": j.finished_at,
    }


class GitConfigView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        return Response({"repo": _repo_row(GitRepo.objects.filter(workspace=ws).first())})

    def put(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        d = request.data
        provider, repo = str(d.get("provider", "")), str(d.get("repo", "")).strip()
        branch, path = str(d.get("branch") or "main").strip(), str(d.get("path") or "workflows").strip().strip("/")
        conn = str(d.get("connection", "")).strip()
        try:
            gitsync.validate_config(provider, repo, branch, path)
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from None
        if not Connection.objects.filter(workspace=ws, name=conn, kind="http_bearer").exists():
            raise serializers.ValidationError(
                {"connection": "Connexion « Service HTTP » introuvable : elle porte le jeton d'accès au dépôt."}
            )
        r, _ = GitRepo.objects.update_or_create(
            workspace=ws,
            defaults={"provider": provider, "repo": repo, "branch": branch, "path": path, "connection": conn},
        )
        record("git.configure", actor=request.user, workspace=ws, provider=provider, repo=repo, branch=branch)
        return Response({"repo": _repo_row(r)})

    def delete(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        n, _ = GitRepo.objects.filter(workspace=ws).delete()
        if not n:
            raise Http404()
        record("git.unconfigure", actor=request.user, workspace=ws)
        return Response(status=204)


class GitJobsView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        rows = GitJob.objects.filter(workspace=ws).select_related("requested_by")[:20]
        return Response({"results": [_job_row(j) for j in rows]})

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        kind = request.data.get("kind")
        if kind not in gitsync.KINDS:
            raise serializers.ValidationError({"kind": "check, push, pull_preview ou pull_apply."})
        if not GitRepo.objects.filter(workspace=ws).exists():
            raise serializers.ValidationError({"detail": "Configurez d'abord le dépôt Git."})
        if GitJob.objects.filter(workspace=ws, status__in=[GitJob.Status.QUEUED, GitJob.Status.RUNNING]).exists():
            raise serializers.ValidationError({"detail": "Une opération Git est déjà en cours pour cet espace."})
        job = GitJob.objects.create(
            workspace=ws,
            kind=kind,
            params={"overwrite": bool(request.data.get("overwrite"))},
            requested_by=request.user,
        )
        return Response(_job_row(job), status=202)


class GitJobDetailView(APIView):
    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, EDIT)
        job = GitJob.objects.filter(workspace=ws, pk=pk).select_related("requested_by").first()
        if job is None:
            raise Http404()
        return Response(_job_row(job))
