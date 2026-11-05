"""User groups of a workspace and the catalog items each group may launch (for the « Catalogue » role). Administrators only."""

from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from workflows.models import CatalogGrant, Workflow

from .models import Membership, UserGroup
from .permissions import ADMINISTER, get_workspace

MAX_GROUPS = 200


class GroupSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    members = serializers.ListField(
        child=serializers.CharField(max_length=150), required=False, default=list, max_length=1000
    )
    workflows = serializers.ListField(child=serializers.SlugField(), required=False, default=list, max_length=1000)


def _card(g: UserGroup) -> dict:
    return {
        "id": g.pk,
        "name": g.name,
        "description": g.description,
        "members": sorted(g.members.values_list("username", flat=True)),
        "workflows": sorted(g.catalog_grants.values_list("workflow__slug", flat=True)),
    }


def _apply(ws, g: UserGroup, d: dict) -> None:
    if "members" in d:
        usernames = set(d["members"])
        found = {
            m.user.username: m.user
            for m in Membership.objects.filter(workspace=ws, user__username__in=usernames).select_related("user")
        }
        missing = sorted(usernames - set(found))
        if missing:
            raise serializers.ValidationError({"members": f"Pas membres de cet espace : {', '.join(missing)}."})
        g.members.set(found.values())
    if "workflows" in d:
        slugs = set(d["workflows"])
        wfs = {w.slug: w for w in Workflow.objects.filter(workspace=ws, slug__in=slugs)}
        missing = sorted(slugs - set(wfs))
        if missing:
            raise serializers.ValidationError({"workflows": f"Automatisations inconnues : {', '.join(missing)}."})
        g.catalog_grants.exclude(workflow__slug__in=slugs).delete()
        for w in wfs.values():
            CatalogGrant.objects.get_or_create(workflow=w, group=g)


class GroupListCreateView(APIView):
    serializer_class = GroupSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        return Response(
            {
                "results": [
                    _card(g)
                    for g in UserGroup.objects.filter(workspace=ws).prefetch_related(
                        "members", "catalog_grants__workflow"
                    )
                ]
            }
        )

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = GroupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if UserGroup.objects.filter(workspace=ws).count() >= MAX_GROUPS:
            raise serializers.ValidationError({"name": f"Limite de {MAX_GROUPS} groupes atteinte."})
        try:
            with transaction.atomic():
                g = UserGroup.objects.create(workspace=ws, name=d["name"].strip(), description=d["description"])
        except IntegrityError:
            raise serializers.ValidationError({"name": "Ce nom de groupe existe déjà."}) from None
        _apply(ws, g, d)
        record("group.create", actor=request.user, workspace=ws, target=g)
        return Response(_card(g), status=201)


class GroupDetailView(APIView):
    serializer_class = GroupSerializer

    def _get(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        g = UserGroup.objects.filter(workspace=ws, pk=pk).first()
        if g is None:
            raise Http404()
        return ws, g

    @transaction.atomic
    def patch(self, request, workspace, pk):
        ws, g = self._get(request, workspace, pk)
        ser = GroupSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if "name" in d:
            if UserGroup.objects.filter(workspace=ws, name=d["name"].strip()).exclude(pk=g.pk).exists():
                raise serializers.ValidationError({"name": "Ce nom de groupe existe déjà."})
            g.name = d["name"].strip()
        if "description" in d:
            g.description = d["description"]
        g.save()
        _apply(ws, g, d)
        record("group.update", actor=request.user, workspace=ws, target=g, fields=sorted(request.data))
        return Response(_card(g))

    def delete(self, request, workspace, pk):
        ws, g = self._get(request, workspace, pk)
        record("group.delete", actor=request.user, workspace=ws, target=g)
        g.delete()
        return Response(status=204)
