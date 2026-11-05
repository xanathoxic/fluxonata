"""Server-side RBAC. The UI is never an authorization boundary."""

from django.http import Http404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .models import Membership, Role, Workspace

VIEW, EDIT, PUBLISH, RUN, APPROVE, MANAGE_CONNECTIONS, ADMINISTER, CATALOG = (
    "view",
    "edit",
    "publish",
    "run",
    "approve",
    "manage_connections",
    "administer",
    "catalog",
)
ALL = {VIEW, EDIT, PUBLISH, RUN, APPROVE, MANAGE_CONNECTIONS, ADMINISTER}  # CATALOG is held by its own role only

ROLE_PERMISSIONS = {
    Role.ADMIN: ALL,
    Role.SPACE_MANAGER: ALL,
    Role.DESIGNER: {VIEW, EDIT, PUBLISH, RUN, MANAGE_CONNECTIONS},
    Role.OPERATOR: {VIEW, RUN},
    Role.APPROVER: {VIEW, APPROVE},
    Role.VIEWER: {VIEW},
    # Deny by default: without VIEW every workspace route answers 403; only the catalog routes accept this permission.
    Role.CATALOG: {CATALOG},
}


def effective_permissions(user, membership) -> set[str]:
    perms = set(ROLE_PERMISSIONS[membership.role])
    token = getattr(user, "_token", None)
    if token is not None:
        perms &= set(token.scopes)
    return perms


def get_workspace(user, slug: str, perm: str = VIEW) -> Workspace:
    """Return the workspace if `user` is a member, else 404 (no existence leak);
    403 if member but lacking `perm`. API tokens are further limited to their workspace and scopes."""
    membership = (
        Membership.objects.select_related("workspace")
        .filter(user=user, workspace__slug=slug, workspace__archived_at__isnull=True)
        .first()
    )
    token = getattr(user, "_token", None)
    if membership is None or (token is not None and token.workspace_id != membership.workspace_id):
        raise Http404()
    if perm not in effective_permissions(user, membership):
        raise PermissionDenied("Vous n'avez pas le droit d'effectuer cette action dans cet espace.")
    return membership.workspace


def has_perm(user, workspace, perm: str) -> bool:
    if workspace.archived_at is not None:
        return False
    m = Membership.objects.filter(user=user, workspace=workspace).first()
    return m is not None and perm in effective_permissions(user, m)


def require_human_session(request) -> None:
    """Acte réservé à une personne connectée (relecture, activation) : jamais un jeton de service, donc jamais un assistant externe (ADR 0025)."""
    if getattr(request.user, "_token", None) is not None:
        raise PermissionDenied("Cette action est réservée à une personne connectée à l'interface, pas à un jeton.")


def require_platform_admin(request) -> None:
    """Platform administrators (Django `is_superuser`) manage workspaces and the shared worker fleet. The flag grants NO
    access to any workspace's data: they must be made a member of a space like anyone else (and that is audited).
    API tokens never carry platform rights."""
    user = request.user
    if (
        not getattr(user, "is_authenticated", False)
        or getattr(user, "_token", None) is not None
        or not user.is_superuser
    ):
        raise PermissionDenied("Réservé aux administrateurs de la plate-forme.")


class IsAuthenticatedAndReady(BasePermission):
    """Authenticated, and — for a person who signed in with a temporary password — only the routes that let them
    choose their own (views set `allow_password_change = True`). Enforced here, server-side, not in the UI."""

    message = "Vous devez d'abord choisir un nouveau mot de passe."
    code = "password_change_required"

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "_token", None) is not None or getattr(view, "allow_password_change", False):
            return True
        from .models import UserProfile

        return not UserProfile.objects.filter(user=user, must_change_password=True).exists()
