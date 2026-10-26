"""Accounts: platform administrators create, edit, deactivate people and place them in one or several workspaces;
anyone changes their own password. Passwords are never logged, audited or returned except a generated temporary one,
shown once. Accounts are deactivated, never deleted: the history (runs, audit, approvals) keeps its author."""

import secrets

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Prefetch, Q
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.models import record

from .admin_views import PRIVILEGED
from .models import Membership, Role, UserProfile, Workspace
from .platform_views import _Guarded

User = get_user_model()
USERNAME_RE = r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$"
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def temporary_password() -> str:
    """Readable (no 0/O/1/l/I), 16 characters, from the OS CSPRNG."""
    return "".join(secrets.choice(ALPHABET) for _ in range(16))


def _check_password(password: str, user=None) -> None:
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"password": list(exc.messages)}) from None


def _profile(user) -> UserProfile:
    return UserProfile.objects.get_or_create(user=user)[0]


def _row(u) -> dict:
    prof = getattr(u, "profile", None)
    return {
        "username": u.username,
        "email": u.email,
        "is_active": u.is_active,
        "is_platform_admin": u.is_superuser,
        "sso_only": not u.has_usable_password(),
        "must_change_password": bool(prof and prof.must_change_password),
        "last_login": u.last_login,
        "memberships": [
            {
                "workspace": m.workspace.slug,
                "name": m.workspace.name,
                "role": m.role,
                "archived": m.workspace.archived_at is not None,
            }
            for m in sorted(u.memberships.all(), key=lambda m: m.workspace.name.lower())
        ],
    }


def _people():
    """Real people only: service accounts are managed in their workspace."""
    return (
        User.objects.filter(service_account__isnull=True)
        .select_related("profile")
        .prefetch_related(Prefetch("memberships", queryset=Membership.objects.select_related("workspace")))
    )


def _not_found():
    return Response({"error": {"code": "not_found", "message": "Ce compte n'existe pas.", "details": None}}, status=404)


def _guard_last_platform_admin(user):
    if user.is_superuser and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).exists():
        raise serializers.ValidationError(
            {"username": "Il doit rester au moins un administrateur de la plate-forme actif."}
        )


class MembershipIn(serializers.Serializer):
    workspace = serializers.SlugField()
    role = serializers.ChoiceField(choices=Role.choices)


class CreateUserIn(serializers.Serializer):
    username = serializers.RegexField(
        USERNAME_RE,
        max_length=64,
        error_messages={"invalid": "Minuscules, chiffres, « . », « _ » ou « - » (3 à 64 caractères)."},
    )
    email = serializers.CharField(required=False, allow_blank=True, max_length=254)
    password = serializers.CharField(
        required=False, allow_blank=True, write_only=True, trim_whitespace=False, max_length=128
    )
    must_change_password = serializers.BooleanField(default=True)
    memberships = MembershipIn(many=True, required=False)

    def validate_username(self, v):
        if User.objects.filter(username__iexact=v).exists():
            raise serializers.ValidationError("Cet identifiant est déjà utilisé.")
        return v

    def validate_email(self, v):
        if v:
            try:
                validate_email(v)
            except DjangoValidationError:
                raise serializers.ValidationError("Adresse e-mail invalide.") from None
        return v


def _set_memberships(actor, user, items):
    wanted = {}
    for it in items:
        if it["workspace"] in wanted:
            raise serializers.ValidationError({"memberships": "Un espace ne peut être indiqué qu'une fois."})
        wanted[it["workspace"]] = it["role"]
    spaces = {w.slug: w for w in Workspace.objects.filter(slug__in=wanted, archived_at__isnull=True)}
    missing = sorted(set(wanted) - set(spaces))
    if missing:
        raise serializers.ValidationError({"memberships": f"Espace introuvable ou archivé : {', '.join(missing)}."})
    for slug, role in wanted.items():
        Membership.objects.create(user=user, workspace=spaces[slug], role=role)
        record("member.add", actor=actor, workspace=spaces[slug], target=user, role=role, via="platform")


class UsersView(_Guarded):
    serializer_class = CreateUserIn

    def get(self, request):
        qs = _people().order_by("username")
        if q := request.query_params.get("q", "").strip()[:100]:
            qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
        return Response({"results": [_row(u) for u in qs[:500]]})

    @transaction.atomic
    def post(self, request):
        ser = CreateUserIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        given = d.get("password") or ""
        password = given or temporary_password()
        user = User(username=d["username"], email=d.get("email", ""))
        if given:
            _check_password(given, user)
        user.set_password(password)
        user.save()
        UserProfile.objects.create(user=user, must_change_password=bool(d["must_change_password"] or not given))
        _set_memberships(request.user, user, d.get("memberships", []))
        record(
            "platform.user_create",
            actor=request.user,
            target=user,
            username=user.username,
            spaces=[m["workspace"] for m in d.get("memberships", [])],
        )
        body = _row(_people().get(pk=user.pk))
        if not given:
            body["temporary_password"] = password  # shown once, never stored in clear
        return Response(body, status=201)


class UserDetailView(_Guarded):
    @transaction.atomic
    def patch(self, request, username):
        user = _people().select_for_update(of=("self",)).filter(username=username).first()
        if user is None:
            return _not_found()
        changes = {}
        if "email" in request.data:
            email = str(request.data.get("email") or "").strip()[:254]
            if email:
                try:
                    validate_email(email)
                except DjangoValidationError:
                    raise serializers.ValidationError({"email": "Adresse e-mail invalide."}) from None
            if email != user.email:
                user.email = email
                changes["email"] = True
        if "is_active" in request.data:
            active = bool(request.data["is_active"])
            if not active and user.pk == request.user.pk:
                raise serializers.ValidationError({"username": "Vous ne pouvez pas désactiver votre propre compte."})
            if not active:
                _guard_last_platform_admin(user)
            if active != user.is_active:
                user.is_active = active
                changes["is_active"] = active
        if changes:
            user.save()
        if "must_change_password" in request.data and user.has_usable_password():
            prof = _profile(user)
            prof.must_change_password = bool(request.data["must_change_password"])
            prof.save(update_fields=["must_change_password"])
            changes["must_change_password"] = prof.must_change_password
        if changes:
            event = {True: "platform.user_reactivate", False: "platform.user_deactivate"}.get(
                changes.get("is_active"), "platform.user_update"
            )
            record(event, actor=request.user, target=user, username=user.username, fields=sorted(changes))
        return Response(_row(_people().get(pk=user.pk)))


class UserResetPasswordView(_Guarded):
    @transaction.atomic
    def post(self, request, username):
        user = _people().filter(username=username).first()
        if user is None:
            return _not_found()
        if not user.has_usable_password():
            raise serializers.ValidationError(
                {"username": "Ce compte se connecte par SSO : il n'a pas de mot de passe local."}
            )
        password = temporary_password()
        user.set_password(password)
        user.save(update_fields=["password"])
        prof = _profile(user)
        prof.must_change_password = True
        prof.save(update_fields=["must_change_password"])
        record("platform.user_reset_password", actor=request.user, target=user, username=user.username)
        return Response({"username": user.username, "temporary_password": password})


class UserMembershipsView(_Guarded):
    """Add the person to a workspace, or change their role there."""

    serializer_class = MembershipIn

    @transaction.atomic
    def post(self, request, username):
        user = _people().filter(username=username).first()
        if user is None:
            return _not_found()
        ser = MembershipIn(data=request.data)
        ser.is_valid(raise_exception=True)
        ws = Workspace.objects.filter(slug=ser.validated_data["workspace"], archived_at__isnull=True).first()
        if ws is None:
            raise serializers.ValidationError({"workspace": "Espace introuvable ou archivé."})
        role = ser.validated_data["role"]
        m = Membership.objects.select_for_update().filter(user=user, workspace=ws).first()
        if m is None:
            Membership.objects.create(user=user, workspace=ws, role=role)
            record("member.add", actor=request.user, workspace=ws, target=user, role=role, via="platform")
        elif m.role != role:
            _guard_last_space_admin(ws, m, role)
            old, m.role = m.role, role
            m.save(update_fields=["role"])
            record("member.role", actor=request.user, workspace=ws, target=user, old=old, new=role, via="platform")
        record(
            "platform.user_membership",
            actor=request.user,
            target=user,
            username=user.username,
            space=ws.slug,
            role=role,
        )
        return Response(_row(_people().get(pk=user.pk)), status=201)


def _guard_last_space_admin(ws, membership, new_role=None):
    if membership.role in PRIVILEGED and new_role not in PRIVILEGED:
        if not Membership.objects.filter(workspace=ws, role__in=PRIVILEGED).exclude(pk=membership.pk).exists():
            raise serializers.ValidationError({"role": "L'espace doit conserver au moins un responsable."})


class UserMembershipDetailView(_Guarded):
    @transaction.atomic
    def delete(self, request, username, workspace):
        user = _people().filter(username=username).first()
        if user is None:
            return _not_found()
        m = (
            Membership.objects.select_for_update()
            .select_related("workspace")
            .filter(user=user, workspace__slug=workspace)
            .first()
        )
        if m is None:
            return _not_found()
        _guard_last_space_admin(m.workspace, m)
        m.delete()
        record("member.remove", actor=request.user, workspace=m.workspace, username=user.username, via="platform")
        record(
            "platform.user_membership_remove",
            actor=request.user,
            target=user,
            username=user.username,
            space=workspace,
        )
        return Response(status=204)


class PasswordChangeIn(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


class PasswordChangeView(APIView):
    """Everyone changes their own password (the only route open to a temporary-password session)."""

    allow_password_change = True
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = PasswordChangeIn

    @transaction.atomic
    def post(self, request):
        if getattr(request.user, "_token", None) is not None or hasattr(request.user, "service_account"):
            raise serializers.ValidationError({"current_password": "Un compte de service n'a pas de mot de passe."})
        ser = PasswordChangeIn(data=request.data)
        ser.is_valid(raise_exception=True)
        user = request.user
        if not user.has_usable_password():
            raise serializers.ValidationError(
                {
                    "current_password": "Ce compte se connecte par SSO : le mot de passe se change chez votre fournisseur d'identité."
                }
            )
        if not user.check_password(ser.validated_data["current_password"]):
            record("auth.password_change_failed", actor=user)
            raise serializers.ValidationError({"current_password": "Mot de passe actuel incorrect."})
        new = ser.validated_data["new_password"]
        if new == ser.validated_data["current_password"]:
            raise serializers.ValidationError({"new_password": "Choisissez un mot de passe différent de l'actuel."})
        try:
            validate_password(new, user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)}) from None
        user.set_password(new)
        user.save(update_fields=["password"])
        UserProfile.objects.filter(user=user).update(must_change_password=False)
        update_session_auth_hash(request, user)  # keep this session, end the others
        record("auth.password_change", actor=user)
        return Response(status=204)
