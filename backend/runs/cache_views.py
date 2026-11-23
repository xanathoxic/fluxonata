"""Gestion du cache nommé d'un espace (ADR 0032, lot 2) : lister, lire et invalider des entrées.

Droits côté serveur : EDIT (concepteurs et plus), car une valeur est lisible par tout workflow de l'espace et peut être une donnée métier.
La liste ne renvoie jamais les valeurs ; le détail renvoie la valeur d'une entrée."""

from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from tenancy.models import Environment
from tenancy.permissions import ADMINISTER, EDIT, get_workspace, has_perm

from .models import CacheEntry

MAX_LIST = 500


class CacheEntrySerializer(serializers.ModelSerializer):
    environment = serializers.CharField(source="environment.kind", read_only=True)
    expired = serializers.SerializerMethodField()

    class Meta:
        model = CacheEntry
        fields = [
            "id",
            "key",
            "environment",
            "version",
            "size_bytes",
            "expires_at",
            "expired",
            "sensitive",
            "written_by_workflow",
            "written_by_run",
            "created_at",
            "updated_at",
        ]

    def get_expired(self, obj) -> bool:
        return obj.expires_at is not None and obj.expires_at <= timezone.now()


class CacheEntryDetailSerializer(CacheEntrySerializer):
    """La valeur d'une entrée sensible n'est rendue qu'aux administrateurs de l'espace (`masked` : vrai, `value` : null pour les autres)."""

    value = serializers.SerializerMethodField()
    masked = serializers.SerializerMethodField()

    class Meta(CacheEntrySerializer.Meta):
        fields = [*CacheEntrySerializer.Meta.fields, "value", "masked"]

    def _hidden(self, obj) -> bool:
        return obj.sensitive and not self.context["can_see_sensitive"]

    @extend_schema_field(OpenApiTypes.ANY)
    def get_value(self, obj):
        return None if self._hidden(obj) else obj.value

    def get_masked(self, obj) -> bool:
        return self._hidden(obj)


class CacheListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = CacheEntrySerializer(many=True)


def _entries(ws):
    return CacheEntry.objects.filter(workspace=ws).select_related("environment")


class CacheListView(APIView):
    """GET : les entrées de l'espace (sans valeur), filtrables par `environment` (dev | test | prod), `key` (clé exacte) et `q` (partie de la clé)."""

    @extend_schema(operation_id="workspace_caches_list", responses=CacheListSerializer)
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        rows = _entries(ws)
        env = request.query_params.get("environment", "")
        if env:
            if env not in Environment.Kind.values:
                raise serializers.ValidationError({"environment": "Environnement inconnu."})
            rows = rows.filter(environment__kind=env)
        exact = request.query_params.get("key", "").strip()
        if exact:
            rows = rows.filter(key=exact[:120])
        q = request.query_params.get("q", "").strip()
        if q:
            rows = rows.filter(key__icontains=q[:120])
        total = rows.count()
        data = CacheEntrySerializer(rows.order_by("key", "environment__kind")[:MAX_LIST], many=True).data
        return Response({"count": total, "results": data})


class CacheDetailView(APIView):
    """GET : une entrée avec sa valeur. DELETE : invalide l'entrée (journalisé ; la valeur n'est pas écrite dans l'audit)."""

    def _entry(self, ws, pk):
        entry = _entries(ws).filter(pk=pk).first()
        if entry is None:
            raise Http404
        return entry

    @extend_schema(operation_id="workspace_cache_retrieve", responses=CacheEntryDetailSerializer)
    def get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, EDIT)
        context = {"can_see_sensitive": has_perm(request.user, ws, ADMINISTER)}
        return Response(CacheEntryDetailSerializer(self._entry(ws, pk), context=context).data)

    @extend_schema(operation_id="workspace_cache_delete", responses={204: None})
    def delete(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, EDIT)
        entry = self._entry(ws, pk)
        record(
            "cache.delete",
            actor=request.user,
            workspace=ws,
            key=entry.key,
            environment=entry.environment.kind,
            version=entry.version,
        )
        entry.delete()
        return Response(status=204)
