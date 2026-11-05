"""Jetons personnels pour l'assistant externe (ADR 0025, amendement) : chaque personne s'en génère un, temporaire, depuis « Mon compte ».

Le jeton agit au nom de la personne : ses droits dans l'espace ∩ les portées du jeton, qui sont `view` (+ `edit` si son rôle le permet) et
jamais davantage. Il ne peut ni publier, ni lancer, ni approuver. Créer ou révoquer un jeton exige une session humaine (un jeton ne fabrique
pas de jeton). La valeur n'est montrée qu'une fois ; seule l'empreinte est conservée."""

from datetime import timedelta

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record

from .auth import new_token
from .models import ApiToken, Membership
from .permissions import VIEW, effective_permissions, get_workspace, require_human_session

MAX_ACTIVE = 5
MAX_HOURS = 24 * 7
DEFAULT_HOURS = 8
DESIGN_SCOPES = ("view", "edit")


def allowed_scopes(user, ws) -> list[str]:
    m = Membership.objects.filter(user=user, workspace=ws).first()
    perms = effective_permissions(user, m) if m else set()
    return [s for s in DESIGN_SCOPES if s in perms]


class PersonalTokenSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, default="Assistant", allow_blank=False)
    hours = serializers.IntegerField(min_value=1, max_value=MAX_HOURS, default=DEFAULT_HOURS)
    write = serializers.BooleanField(default=False)  # « edit » : créer des brouillons et des propositions


def _row(t: ApiToken) -> dict:
    return {
        "id": t.pk,
        "name": t.name,
        "prefix": t.prefix,
        "scopes": t.scopes,
        "created_at": t.created_at,
        "expires_at": t.expires_at,
        "last_used_at": t.last_used_at,
    }


class McpTokensView(APIView):
    serializer_class = PersonalTokenSerializer

    def get(self, request, workspace):
        require_human_session(request)
        ws = get_workspace(request.user, workspace, VIEW)
        rows = ApiToken.objects.filter(
            workspace=ws, user=request.user, revoked_at__isnull=True, expires_at__gt=timezone.now()
        ).order_by("-created_at")
        return Response({"results": [_row(t) for t in rows], "allowed_scopes": allowed_scopes(request.user, ws)})

    @transaction.atomic
    def post(self, request, workspace):
        require_human_session(request)
        ws = get_workspace(request.user, workspace, VIEW)
        ser = PersonalTokenSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        allowed = allowed_scopes(request.user, ws)
        scopes = ["view"]
        if d["write"]:
            if "edit" not in allowed:
                raise PermissionDenied("Votre rôle ne permet pas de modifier : le jeton ne peut être qu'en lecture.")
            scopes.append("edit")
        active = ApiToken.objects.select_for_update().filter(
            workspace=ws, user=request.user, revoked_at__isnull=True, expires_at__gt=timezone.now()
        )
        if active.count() >= MAX_ACTIVE:
            raise serializers.ValidationError(
                {"name": f"{MAX_ACTIVE} jetons actifs au plus : révoquez-en un avant d'en créer un autre."}
            )
        raw, prefix, digest = new_token()
        t = ApiToken.objects.create(
            workspace=ws,
            user=request.user,
            name=d["name"],
            prefix=prefix,
            token_hash=digest,
            scopes=scopes,
            environments=[],
            expires_at=timezone.now() + timedelta(hours=d["hours"]),
            created_by=request.user,
        )
        record(
            "token.create", actor=request.user, workspace=ws, target=t, scopes=scopes, personal=True, hours=d["hours"]
        )
        return Response(
            {
                **_row(t),
                "token": raw,
                "notice": "Ce jeton ne sera plus jamais affiché : copiez-le maintenant.",
            },
            status=201,
        )


class McpTokenRevokeView(APIView):
    def delete(self, request, workspace, pk):
        require_human_session(request)
        ws = get_workspace(request.user, workspace, VIEW)
        t = ApiToken.objects.filter(workspace=ws, user=request.user, pk=pk, revoked_at__isnull=True).first()
        if t is None:
            raise Http404()
        t.revoked_at = timezone.now()
        t.save(update_fields=["revoked_at"])
        record("token.revoke", actor=request.user, workspace=ws, target=t, personal=True)
        return Response(status=204)
