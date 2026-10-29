"""Folder-level sharing: who may see and use the workflows of a PRIVATE folder.

A folder becomes private when it has a `FolderAccess`; its `FolderGrant` rows list the people who keep access, each with a
level (`view`, `run`, `edit`). The rule that applies to a workflow is the one of the DEEPEST private folder that contains it
(a private sub-folder replaces its parent's list). Workspace administrators and space managers always have access (nobody
can lock the space out). A grant only NARROWS the person's role in the workspace: a viewer granted `edit` still cannot edit.

Everything is decided here, on the server: the lists leave out what a person may not see (a hidden workflow answers 404, like
one that does not exist), and the actions check the level."""

from django.db.models import Q
from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from tenancy.models import Membership, Role
from tenancy.permissions import APPROVE, EDIT, PUBLISH, RUN, VIEW

LEVEL_PERMS = {
    "view": {VIEW},
    "run": {VIEW, RUN, APPROVE},
    "edit": {VIEW, RUN, APPROVE, EDIT, PUBLISH},
}
BYPASS = {Role.ADMIN, Role.SPACE_MANAGER}
RUN_FOLDER = "workflow_version__workflow__folder"


def norm(folder) -> str:
    return "/".join(p.strip() for p in str(folder or "").split("/") if p.strip())


def _within(folder: str, root: str) -> bool:
    return folder == root or folder.startswith(root + "/")


class Rules:
    """What one person may do in the folders of one workspace (read once: a handful of rows)."""

    def __init__(self, user, workspace):
        from .models import FolderAccess

        m = Membership.objects.filter(user=user, workspace=workspace).first()
        self.bypass = m is not None and m.role in BYPASS
        self.locks: dict[str, dict[int, str]] = {}
        self.private: list[str] = []
        for a in FolderAccess.objects.filter(workspace=workspace).prefetch_related("grants"):
            self.private.append(a.folder)
            if not self.bypass:
                self.locks[a.folder] = {g.user_id: g.level for g in a.grants.all()}
        self.user_id = user.pk

    def lock_for(self, folder) -> str | None:
        folder = norm(folder)
        found = [lock for lock in self.locks if _within(folder, lock)]
        return max(found, key=len) if found else None

    def level(self, folder) -> str | None:
        """The person's level in that folder: None = no restriction here (public folder or administrator)."""
        lock = self.lock_for(folder)
        if lock is None:
            return None
        return self.locks[lock].get(self.user_id, "none")

    def allows(self, folder, perm: str) -> bool:
        lvl = self.level(folder)
        return lvl is None or (lvl in LEVEL_PERMS and perm in LEVEL_PERMS[lvl])

    def hidden(self, prefix: str = "folder") -> Q:
        """Rows (workflows, or runs through `prefix`) in a private folder the person has no grant for."""
        q = Q()
        for lock, grants in self.locks.items():
            if self.user_id in grants:
                continue
            here = Q(**{prefix: lock}) | Q(**{f"{prefix}__startswith": lock + "/"})
            for deeper in self.locks:
                if deeper != lock and _within(deeper, lock):
                    here &= ~(Q(**{prefix: deeper}) | Q(**{f"{prefix}__startswith": deeper + "/"}))
            q |= here
        return q


def rules(user, workspace) -> Rules:
    return Rules(user, workspace)


def visible(qs, user, workspace, prefix: str = "folder"):
    """`qs` without what lives in a private folder the person has no grant for."""
    q = Rules(user, workspace).hidden(prefix)
    return qs.exclude(q) if q else qs


def visible_runs(qs, user, workspace):
    return visible(qs, user, workspace, RUN_FOLDER)


def require(user, workspace, folder, perm: str = VIEW) -> None:
    """404 when the folder is hidden from the person, 403 when it is visible but the level is too low."""
    r = Rules(user, workspace)
    if not r.allows(folder, VIEW):
        raise Http404()
    if not r.allows(folder, perm):
        raise PermissionDenied("Votre niveau d'accès à ce dossier ne permet pas cette action.")


def workflow_or_404(user, workspace, slug, perm: str = VIEW):
    from .models import Workflow

    wf = Workflow.objects.filter(workspace=workspace, slug=slug).first()
    if wf is None:
        raise Http404()
    require(user, workspace, wf.folder, perm)
    return wf


def run_folder(run) -> str:
    """The folder that governs a run: the one of the workflow the person launched (a sub-run follows its parent)."""
    while run.parent_step_id:
        run = run.parent_step.run
    return run.workflow_version.workflow.folder


def require_run(user, workspace, run, perm: str = VIEW) -> None:
    require(user, workspace, run_folder(run), perm)


def run_or_404(user, workspace, qs, perm: str = VIEW):
    """The run (from `qs`, already narrowed to the workspace) if its workflow's folder allows `perm`."""
    run = qs.select_related("workflow_version__workflow").first()
    if run is None:
        raise Http404()
    require_run(user, workspace, run, perm)
    return run
