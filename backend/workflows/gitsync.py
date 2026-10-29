"""Git synchronisation of a workspace's workflows, through the web API of GitHub or GitLab (no git program, no clone).

- `push` writes the changed workflows as `<path>/<slug>.json` on a NEW branch and opens a pull / merge request: nothing is
  ever written to the main branch, a person reviews and merges on the forge, as for any change;
- `pull_preview` / `pull_apply` read the branch and show / import the files as DRAFTS (nothing is published or run; the same
  checks and folder rules as « import all »);
- `check` tests the repository, the branch and the token.

Everything runs in a worker (a thread of `run_worker`), never in the web process, and every call goes through
`core.netsec` (public addresses only unless allowlisted, validated address pinned, no redirect). The token comes from a vault
connection, is read only here, and is masked in anything that comes back. Workflows are exported with the rights of the
person who asked: what they cannot see is not sent."""

import base64
import hashlib
import json
import logging
import re
import threading
import time
from datetime import timedelta
from urllib.parse import quote

from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import record
from core import netsec

from . import sync
from .models import GitJob, GitRepo

log = logging.getLogger("fluxonata.git")
FILE_FORMAT = "fluxonata-workflow-file/1"
MAX_FILE = 512 * 1024
MAX_FILES = 200
MAX_RESPONSE = 2 * 1024 * 1024
TIMEOUT = 20
LEASE = timedelta(minutes=10)
PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
DEFAULT_BASE = {"github": "https://api.github.com", "gitlab": "https://gitlab.com/api/v4"}
KINDS = ("check", "push", "pull_preview", "pull_apply")


class GitError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Vérifiez la configuration Git."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def validate_config(provider, repo, branch, path) -> None:
    if provider not in DEFAULT_BASE:
        raise ValueError("Fournisseur : github ou gitlab.")
    if not REPO_RE.match(repo or "") or ".." in repo:
        raise ValueError("Dépôt : « propriétaire/nom » (GitHub) ou « groupe/projet » (GitLab).")
    if not BRANCH_RE.match(branch or "") or ".." in branch or branch.startswith(("/", "-")):
        raise ValueError("Branche invalide.")
    if not PATH_RE.match(path or "") or ".." in path.split("/"):
        raise ValueError("Dossier du dépôt : un chemin relatif simple, par exemple « workflows ».")


def file_name(slug: str) -> str:
    return f"{slug}.json"


def render(doc: dict) -> str:
    """The text of a workflow file: stable (sorted keys, 2 spaces) so that a diff only shows real changes."""
    body = {k: doc[k] for k in ("slug", "name", "description", "folder", "graph")}
    return json.dumps({"format": FILE_FORMAT, **body}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def blob_sha(text: str) -> str:
    """The Git object id of a file's content: lets us compare with the repository without downloading the file."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324  (Git's own object id, not security)


def parse(text: str, stem: str) -> dict:
    try:
        doc = json.loads(text)
    except ValueError:
        raise GitError("GIT_BAD_FILE", f"{stem}.json n'est pas du JSON valide.") from None
    if not isinstance(doc, dict) or doc.get("format") != FILE_FORMAT or doc.get("slug") != stem:
        raise GitError("GIT_BAD_FILE", f"{stem}.json : format inconnu ou identifiant différent du nom du fichier.")
    return {k: doc.get(k) for k in ("slug", "name", "description", "folder", "graph")}


# --------------------------------------------------------------------------------------------------- the forge's API
class Client:
    def __init__(self, repo: GitRepo, conn):
        from vault import auth

        self.repo, self.conn = repo, conn
        self.base = str((conn.config or {}).get("base_url") or DEFAULT_BASE[repo.provider]).rstrip("/")
        try:
            self.creds = auth.credentials(conn)
        except auth.AuthError as exc:
            raise GitError(exc.code, exc.message, "Vérifiez la connexion dans Connexions.") from None
        self.prefix = repo.path

    def call(self, method: str, path: str, body=None, raw: bool = False):
        headers = {**self.creds.headers, "Accept": "application/json"}
        if self.repo.provider == "github":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            status, _, payload = netsec.safe_request(
                method, self.base + path, headers=headers, body=data, timeout=TIMEOUT, max_bytes=MAX_RESPONSE + 1
            )
        except netsec.UnsafeURL as exc:
            raise GitError("GIT_BLOCKED", f"Adresse refusée : {exc}", "Vérifiez l'adresse de la connexion.") from None
        except OSError as exc:
            raise GitError("GIT_UNREACHABLE", f"Le serveur Git n'a pas répondu ({type(exc).__name__}).") from None
        if len(payload) > MAX_RESPONSE:
            raise GitError("GIT_TOO_BIG", "Réponse trop volumineuse.")
        if status in (401, 403):
            raise GitError("GIT_AUTH", "Accès refusé : vérifiez le jeton de la connexion et ses droits sur le dépôt.")
        if status == 404:
            raise GitError("GIT_NOT_FOUND", "Dépôt, branche ou dossier introuvable (ou jeton sans accès).")
        if status >= 400:
            detail = payload[:200].decode("utf-8", "replace")
            for secret in self.creds.secrets:
                detail = detail.replace(secret, "***") if secret and len(secret) >= 4 else detail
            raise GitError("GIT_API", f"Le serveur Git a répondu {status} : {detail}")
        if raw:
            return payload
        try:
            return json.loads(payload) if payload else {}
        except ValueError:
            raise GitError("GIT_API", "Réponse illisible du serveur Git.") from None


class GitHub:
    def __init__(self, c: Client):
        self.c, self.repo = c, c.repo.repo
        self.branch = c.repo.branch

    def check(self) -> dict:
        ref = self.c.call("GET", f"/repos/{self.repo}/git/ref/heads/{quote(self.branch, safe='')}")
        return {"head": (ref.get("object") or {}).get("sha", "")}

    def files(self) -> dict[str, str]:
        tree = self.c.call("GET", f"/repos/{self.repo}/git/trees/{quote(self.branch, safe='')}?recursive=1")
        if tree.get("truncated"):
            raise GitError("GIT_TOO_BIG", "Le dépôt est trop volumineux pour être lu par l'API (arbre tronqué).")
        out, head = {}, self.c.prefix + "/"
        for e in tree.get("tree", []):
            p = e.get("path", "")
            if e.get("type") == "blob" and p.startswith(head) and "/" not in p[len(head) :] and p.endswith(".json"):
                out[p[len(head) :]] = e.get("sha", "")
        return out

    def read(self, name: str) -> str:
        path = quote(f"{self.c.prefix}/{name}", safe="/")
        doc = self.c.call("GET", f"/repos/{self.repo}/contents/{path}?ref={quote(self.branch, safe='')}")
        if doc.get("encoding") != "base64":
            raise GitError("GIT_BAD_FILE", f"{name} : contenu illisible.")
        return base64.b64decode(doc.get("content", "")).decode("utf-8", "replace")

    def propose(self, branch: str, files: dict, message: str, title: str, body: str) -> str:
        head = self.check()["head"]
        self.c.call("POST", f"/repos/{self.repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": head})
        for name, (text, existing) in files.items():
            payload = {"message": message, "content": base64.b64encode(text.encode()).decode(), "branch": branch}
            if existing:
                payload["sha"] = existing
            self.c.call("PUT", f"/repos/{self.repo}/contents/{quote(f'{self.c.prefix}/{name}', safe='/')}", payload)
        pr = self.c.call(
            "POST", f"/repos/{self.repo}/pulls", {"title": title, "head": branch, "base": self.branch, "body": body}
        )
        return str(pr.get("html_url", ""))


class GitLab:
    def __init__(self, c: Client):
        self.c, self.project = c, quote(c.repo.repo, safe="")
        self.branch = c.repo.branch

    def check(self) -> dict:
        b = self.c.call("GET", f"/projects/{self.project}/repository/branches/{quote(self.branch, safe='')}")
        return {"head": (b.get("commit") or {}).get("id", "")}

    def files(self) -> dict[str, str]:
        out, page = {}, 1
        while True:
            q = f"path={quote(self.c.prefix, safe='')}&ref={quote(self.branch, safe='')}&per_page=100&page={page}"
            rows = self.c.call("GET", f"/projects/{self.project}/repository/tree?{q}")
            for e in rows if isinstance(rows, list) else []:
                if e.get("type") == "blob" and str(e.get("name", "")).endswith(".json"):
                    out[e["name"]] = e.get("id", "")
            if not isinstance(rows, list) or len(rows) < 100 or page >= 10:
                return out
            page += 1

    def read(self, name: str) -> str:
        path = quote(f"{self.c.prefix}/{name}", safe="")
        raw = self.c.call(
            "GET", f"/projects/{self.project}/repository/files/{path}/raw?ref={quote(self.branch, safe='')}", raw=True
        )
        return raw.decode("utf-8", "replace")

    def propose(self, branch: str, files: dict, message: str, title: str, body: str) -> str:
        actions = [
            {"action": "update" if existing else "create", "file_path": f"{self.c.prefix}/{name}", "content": text}
            for name, (text, existing) in files.items()
        ]
        self.c.call(
            "POST",
            f"/projects/{self.project}/repository/commits",
            {"branch": branch, "start_branch": self.branch, "commit_message": message, "actions": actions},
        )
        mr = self.c.call(
            "POST",
            f"/projects/{self.project}/merge_requests",
            {"source_branch": branch, "target_branch": self.branch, "title": title, "description": body},
        )
        return str(mr.get("web_url", ""))


def _forge(repo: GitRepo):
    from vault.models import Connection

    conn = (
        Connection.objects.select_related("secret")
        .filter(workspace=repo.workspace, name=repo.connection, kind="http_bearer")
        .order_by("environment_id")
        .first()
    )
    if conn is None:
        raise GitError(
            "CONNECTION_MISSING",
            f"Connexion « {repo.connection} » introuvable (type « Service HTTP », jeton d'accès au dépôt).",
            "Créez la connexion dans Connexions.",
        )
    client = Client(repo, conn)
    return GitHub(client) if repo.provider == "github" else GitLab(client)


# ----------------------------------------------------------------------------------------------------- the operations
def _push(job, forge, ws, user) -> dict:
    docs = sync.export_documents(ws, user)
    rendered = {file_name(d["slug"]): render(d) for d in docs}
    existing = forge.files()
    changes = {n: (t, existing.get(n)) for n, t in rendered.items() if existing.get(n) != blob_sha(t)}
    base = {
        "unchanged": len(rendered) - len(changes),
        "only_in_repo": sorted(set(existing) - set(rendered)),  # never deleted from the repository
    }
    if not changes:
        return {"status": "unchanged", **base, "created": [], "updated": []}
    if len(changes) > MAX_FILES:
        raise GitError("GIT_TOO_BIG", f"Trop de fichiers modifiés ({len(changes)}) : {MAX_FILES} au plus par envoi.")
    for n, (text, _) in changes.items():
        if len(text.encode()) > MAX_FILE:
            raise GitError("GIT_TOO_BIG", f"{n} dépasse {MAX_FILE // 1024} Ko.")
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    branch = f"fluxonata/sync-{stamp}-{job.pk}"
    created = sorted(n[:-5] for n, (_, sha) in changes.items() if not sha)
    updated = sorted(n[:-5] for n, (_, sha) in changes.items() if sha)
    title = f"Fluxonata : {len(changes)} workflow(s) à relire"
    lines = [f"- créé : `{s}`" for s in created] + [f"- modifié : `{s}`" for s in updated]
    body = (
        f"Envoyé depuis l'espace « {ws.slug} » par {user.get_username()}.\n\n" + "\n".join(lines) + "\n\n"
        "Après fusion, importez la branche dans Fluxonata (Administration > Git) : les brouillons sont mis à jour, "
        "rien n'est publié automatiquement."
    )
    url = forge.propose(branch, changes, title, title, body)
    record("git.push", actor=user, workspace=ws, branch=branch, created=len(created), updated=len(updated))
    return {"status": "proposed", "url": url, "branch": branch, "created": created, "updated": updated, **base}


def _pull(job, forge, ws, user, apply: bool) -> dict:
    names = sorted(forge.files())
    if len(names) > MAX_FILES:
        raise GitError("GIT_TOO_BIG", f"Plus de {MAX_FILES} fichiers dans le dossier du dépôt.")
    items, results = [], []
    for n in names:
        stem = n[:-5]
        try:
            text = forge.read(n)
            if len(text.encode()) > MAX_FILE:
                raise GitError("GIT_BAD_FILE", f"{n} dépasse {MAX_FILE // 1024} Ko.")
            items.append(parse(text, stem))
        except GitError as exc:
            if exc.code != "GIT_BAD_FILE":
                raise
            results.append({"slug": stem, "status": "refused", "reason": exc.message})
    results += sync.import_documents(ws, user, items, bool(job.params.get("overwrite")), dry=not apply, mode="git")
    if apply:
        record(
            "git.pull_apply",
            actor=user,
            workspace=ws,
            files=len(names),
            applied=sum(r["status"] in ("created", "updated") for r in results),
        )
    return {"applied": apply, "overwrite": bool(job.params.get("overwrite")), "results": results}


def run_job(job: GitJob) -> tuple[dict | None, dict | None]:
    ws, user = job.workspace, job.requested_by
    try:
        repo = GitRepo.objects.filter(workspace=ws).first()
        if repo is None:
            raise GitError("GIT_NOT_CONFIGURED", "Aucun dépôt Git n'est configuré pour cet espace.")
        if user is None:
            raise GitError("GIT_NO_USER", "La personne qui a demandé l'opération n'existe plus.")
        forge = _forge(repo)
        if job.kind == "check":
            return {"status": "ok", **forge.check(), "provider": repo.provider, "repo": repo.repo}, None
        if job.kind == "push":
            return _push(job, forge, ws, user), None
        return _pull(job, forge, ws, user, apply=job.kind == "pull_apply"), None
    except GitError as exc:
        return None, {"code": exc.code, "message": exc.message, "next_step": exc.next_step}


# --------------------------------------------------------------------------------------------------------- the worker
def claim_next() -> GitJob | None:
    with transaction.atomic():
        job = (
            GitJob.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("workspace", "requested_by")
            .filter(status=GitJob.Status.QUEUED)
            .order_by("id")
            .first()
        )
        if job is None:
            return None
        job.status, job.claimed_at = GitJob.Status.RUNNING, timezone.now()
        job.save(update_fields=["status", "claimed_at"])
        return job


def process_one() -> bool:
    job = claim_next()
    if job is None:
        return False
    try:
        result, error = run_job(job)
    except Exception:  # noqa: BLE001
        log.exception("git job %s crashed", job.pk)
        result, error = (
            None,
            {"code": "GIT_ERROR", "message": "Erreur interne pendant l'opération Git.", "next_step": ""},
        )
    GitJob.objects.filter(pk=job.pk, status=GitJob.Status.RUNNING).update(
        status=GitJob.Status.FAILED if error else GitJob.Status.DONE,
        result=result,
        error=error,
        finished_at=timezone.now(),
    )
    return True


def reap_stale() -> None:
    """A job claimed long ago by a worker that died is failed, never retried (a push has side effects)."""
    GitJob.objects.filter(status=GitJob.Status.RUNNING).filter(
        Q(claimed_at__lt=timezone.now() - LEASE) | Q(claimed_at__isnull=True)
    ).update(
        status=GitJob.Status.FAILED,
        finished_at=timezone.now(),
        error={
            "code": "GIT_INTERRUPTED",
            "message": "L'opération a été interrompue (travailleur arrêté).",
            "next_step": "Vérifiez le dépôt puis relancez.",
        },
    )


def run_loop(worker_id: str, stop=None, poll_seconds: float = 2.0) -> None:
    stop = stop or threading.Event()
    last_reap = 0.0
    while not stop.is_set():
        close_old_connections()
        try:
            if time.monotonic() - last_reap > 60:
                reap_stale()
                last_reap = time.monotonic()
            if process_one():
                continue
        except Exception:  # noqa: BLE001
            log.exception("git loop iteration failed")
        stop.wait(poll_seconds)
