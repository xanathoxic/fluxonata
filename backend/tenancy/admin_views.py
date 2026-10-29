"""Workspace administration API: members, service accounts, tokens, policy, promotions, audit."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404, StreamingHttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditEvent, record

from . import governance
from .auth import TOKEN_SCOPES, new_token
from .models import ApiToken, Environment, Membership, Promotion, Role, ServiceAccount
from .permissions import ADMINISTER, RUN, VIEW, get_workspace

User = get_user_model()
PRIVILEGED = (Role.ADMIN, Role.SPACE_MANAGER)


class MemberSerializer(serializers.Serializer):
    username = serializers.CharField()
    role = serializers.ChoiceField(choices=Role.choices)


class MembersView(APIView):
    serializer_class = MemberSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rows = Membership.objects.filter(workspace=ws).select_related("user").order_by("user__username")
        return Response(
            {
                "results": [
                    {"username": m.user.username, "role": m.role, "is_service": hasattr(m.user, "service_account")}
                    for m in rows
                ]
            }
        )

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = MemberSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = User.objects.filter(username=ser.validated_data["username"]).first()
        if user is None:
            raise serializers.ValidationError({"username": "Aucun compte avec cet identifiant."})
        if Membership.objects.filter(user=user, workspace=ws).exists():
            raise serializers.ValidationError({"username": "Cette personne est déjà membre de l'espace."})
        Membership.objects.create(user=user, workspace=ws, role=ser.validated_data["role"])
        record("member.add", actor=request.user, workspace=ws, target=user, role=ser.validated_data["role"])
        return Response(status=201)


class MemberDetailView(APIView):
    serializer_class = MemberSerializer

    def _guard_last_admin(self, ws, membership):
        others = Membership.objects.filter(workspace=ws, role__in=PRIVILEGED).exclude(pk=membership.pk).count()
        if membership.role in PRIVILEGED and others == 0:
            raise serializers.ValidationError({"role": "L'espace doit conserver au moins un responsable."})

    @transaction.atomic
    def patch(self, request, workspace, username):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        m = Membership.objects.select_for_update().filter(workspace=ws, user__username=username).first()
        if m is None:
            raise Http404()
        role = request.data.get("role")
        if role not in Role.values:
            raise serializers.ValidationError({"role": "Rôle inconnu."})
        if role not in PRIVILEGED:
            self._guard_last_admin(ws, m)
        old, m.role = m.role, role
        m.save()
        record("member.role", actor=request.user, workspace=ws, target=m.user, old=old, new=role)
        return Response(status=204)

    @transaction.atomic
    def delete(self, request, workspace, username):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        m = Membership.objects.select_for_update().filter(workspace=ws, user__username=username).first()
        if m is None:
            raise Http404()
        self._guard_last_admin(ws, m)
        m.delete()
        record("member.remove", actor=request.user, workspace=ws, username=username)
        return Response(status=204)


class ServiceAccountSerializer(serializers.Serializer):
    name = serializers.SlugField(max_length=60)
    role = serializers.ChoiceField(choices=[Role.OPERATOR, Role.DESIGNER, Role.VIEWER], default=Role.OPERATOR)


class ServiceAccountsView(APIView):
    serializer_class = ServiceAccountSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rows = ServiceAccount.objects.filter(workspace=ws).select_related("user")
        roles = dict(Membership.objects.filter(workspace=ws).values_list("user_id", "role"))
        return Response({"results": [{"id": s.pk, "name": s.name, "role": roles.get(s.user_id)} for s in rows]})

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = ServiceAccountSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        name = ser.validated_data["name"]
        if ServiceAccount.objects.filter(workspace=ws, name=name).exists():
            raise serializers.ValidationError({"name": "Ce compte de service existe déjà."})
        user = User(username=f"svc.{ws.slug}.{name}"[:150])
        user.set_unusable_password()
        user.save()
        sa = ServiceAccount.objects.create(user=user, workspace=ws, name=name, created_by=request.user)
        Membership.objects.create(user=user, workspace=ws, role=ser.validated_data["role"])
        record("service_account.create", actor=request.user, workspace=ws, target=sa, role=ser.validated_data["role"])
        return Response({"id": sa.pk, "name": name}, status=201)


class TokenCreateSerializer(serializers.Serializer):
    service_account = serializers.IntegerField()
    name = serializers.CharField(max_length=80)
    scopes = serializers.ListField(child=serializers.ChoiceField(choices=sorted(TOKEN_SCOPES)), min_length=1)
    environments = serializers.ListField(child=serializers.ChoiceField(choices=["dev", "test", "prod"]), default=list)
    days = serializers.IntegerField(min_value=1, max_value=365, default=30)


class TokensView(APIView):
    serializer_class = TokenCreateSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rows = ApiToken.objects.filter(workspace=ws).select_related("service_account").order_by("-created_at")
        return Response(
            {
                "results": [
                    {
                        "id": t.pk,
                        "name": t.name,
                        "prefix": t.prefix,
                        "service_account": t.service_account.name,
                        "scopes": t.scopes,
                        "environments": t.environments,
                        "expires_at": t.expires_at,
                        "last_used_at": t.last_used_at,
                        "revoked": t.revoked_at is not None,
                    }
                    for t in rows
                ]
            }
        )

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = TokenCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        sa = ServiceAccount.objects.filter(workspace=ws, pk=d["service_account"]).first()
        if sa is None:
            raise serializers.ValidationError({"service_account": "Compte de service inconnu."})
        raw, prefix, digest = new_token()
        t = ApiToken.objects.create(
            workspace=ws,
            service_account=sa,
            name=d["name"],
            prefix=prefix,
            token_hash=digest,
            scopes=d["scopes"],
            environments=d["environments"],
            expires_at=timezone.now() + timedelta(days=d["days"]),
            created_by=request.user,
        )
        record(
            "token.create",
            actor=request.user,
            workspace=ws,
            target=t,
            scopes=d["scopes"],
            environments=d["environments"],
        )
        return Response(
            {
                "id": t.pk,
                "token": raw,
                "prefix": prefix,
                "expires_at": t.expires_at,
                "notice": "Ce jeton ne sera plus jamais affiché : copiez-le maintenant.",
            },
            status=201,
        )


class TokenRevokeView(APIView):
    def delete(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        t = ApiToken.objects.filter(workspace=ws, pk=pk).first()
        if t is None:
            raise Http404()
        t.revoked_at = timezone.now()
        t.save()
        record("token.revoke", actor=request.user, workspace=ws, target=t)
        return Response(status=204)


class TokenRotateView(APIView):
    """Renew a token: a NEW token with the same service account, scopes, environments and lifetime is returned once; the old
    one keeps working for an overlap (default 24 h, 0 to 168) so the client can be updated without a gap, then expires.
    The platform cannot push the new value to the client: whoever uses the token must receive it."""

    @transaction.atomic
    def post(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        old = ApiToken.objects.select_for_update().filter(workspace=ws, pk=pk, revoked_at__isnull=True).first()
        if old is None or old.expires_at <= timezone.now():
            raise Http404()
        overlap = request.data.get("overlap_hours", 24)
        if not isinstance(overlap, int) or isinstance(overlap, bool) or not 0 <= overlap <= 168:
            raise serializers.ValidationError({"overlap_hours": "De 0 à 168 heures."})
        lifetime = max(1, round((old.expires_at - old.created_at).total_seconds() / 86400))
        raw, prefix, digest = new_token()
        new = ApiToken.objects.create(
            workspace=ws,
            service_account=old.service_account,
            name=old.name,
            prefix=prefix,
            token_hash=digest,
            scopes=old.scopes,
            environments=old.environments,
            expires_at=timezone.now() + timedelta(days=lifetime),
            created_by=request.user,
        )
        old.expires_at = min(old.expires_at, timezone.now() + timedelta(hours=overlap))
        old.save(update_fields=["expires_at"])
        record("token.rotate", actor=request.user, workspace=ws, target=new, replaces=old.prefix, overlap_hours=overlap)
        return Response(
            {
                "id": new.pk,
                "token": raw,
                "prefix": prefix,
                "expires_at": new.expires_at,
                "old_expires_at": old.expires_at,
                "notice": "Ce jeton ne sera plus jamais affiché : copiez-le maintenant et mettez à jour le client avant la fin du chevauchement.",
            },
            status=201,
        )


class PolicyView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        pv, rules = governance.current_policy(ws)
        return Response({"version": pv.version if pv else 0, "rules": rules})

    def put(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rules = request.data.get("rules")
        if not isinstance(rules, dict):
            raise serializers.ValidationError({"rules": "Objet attendu."})
        pv = governance.publish_policy(ws, rules, request.user)
        return Response({"version": pv.version, "rules": pv.rules})


class PromotionSerializer(serializers.ModelSerializer):
    workflow = serializers.CharField(source="workflow_version.workflow.name")
    workflow_slug = serializers.CharField(source="workflow_version.workflow.slug")
    version = serializers.CharField(source="workflow_version.version")
    environment = serializers.CharField(source="environment.kind")
    requested_by = serializers.CharField(source="requested_by.username", default=None)
    reviewed_by = serializers.CharField(source="reviewed_by.username", default=None)
    can_decide = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = [
            "id",
            "workflow",
            "workflow_slug",
            "version",
            "environment",
            "status",
            "requested_by",
            "reviewed_by",
            "comment",
            "created_at",
            "decided_at",
            "can_decide",
        ]

    def get_can_decide(self, obj) -> bool:
        u = self.context["request"].user
        _, rules = governance.current_policy(obj.workspace)
        return obj.status == "requested" and not (rules["forbid_self_review"] and obj.requested_by_id == u.id)


class PromotionListView(ListAPIView):
    serializer_class = PromotionSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = (
            Promotion.objects.filter(workspace=ws)
            .select_related("workflow_version__workflow", "environment", "requested_by", "reviewed_by")
            .order_by("-created_at")
        )
        from workflows import access

        qs = access.visible(qs, self.request.user, ws, "workflow_version__workflow__folder")
        return qs.filter(status=self.request.query_params["status"]) if "status" in self.request.query_params else qs


class PromoteView(APIView):
    def post(self, request, workspace, slug, version):
        from workflows.models import WorkflowVersion

        ws = get_workspace(request.user, workspace, VIEW)
        wv = WorkflowVersion.objects.filter(workflow__workspace=ws, workflow__slug=slug, version=version).first()
        env = Environment.objects.filter(workspace=ws, kind=request.data.get("environment")).first()
        if wv is None or env is None:
            raise Http404()
        from workflows import access

        access.require(request.user, ws, wv.workflow.folder, RUN)
        promo = governance.request_promotion(wv, env, request.user, str(request.data.get("comment", "")))
        return Response(PromotionSerializer(promo, context={"request": request}).data, status=201)


class PromotionDecideView(APIView):
    def post(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, VIEW)
        found = Promotion.objects.filter(workspace=ws, pk=pk).select_related("workflow_version__workflow").first()
        if found is None:
            raise Http404()
        from workflows import access

        access.require(request.user, ws, found.workflow_version.workflow.folder, VIEW)
        promo = governance.decide_promotion(
            pk, request.user, bool(request.data.get("approve")), str(request.data.get("comment", ""))
        )
        return Response(PromotionSerializer(promo, context={"request": request}).data)


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ["id", "created_at", "actor_label", "action", "target_type", "target_id", "data"]


class AuditListView(ListAPIView):
    serializer_class = AuditSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], ADMINISTER)
        qs = AuditEvent.objects.filter(workspace=ws).order_by("-id")
        p = self.request.query_params
        if p.get("action"):
            qs = qs.filter(action__startswith=p["action"])
        if p.get("actor"):
            qs = qs.filter(actor_label=p["actor"])
        return qs


class AuditExportView(APIView):
    """NDJSON export, meant to be shipped to storage independent of the database."""

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        record("audit.export", actor=request.user, workspace=ws)

        def lines():
            for ev in AuditEvent.objects.filter(workspace=ws).order_by("id").iterator(chunk_size=500):
                yield json.dumps(AuditSerializer(ev).data, default=str) + "\n"

        resp = StreamingHttpResponse(lines(), content_type="application/x-ndjson")
        resp["Content-Disposition"] = f'attachment; filename="audit-{ws.slug}.ndjson"'
        return resp
