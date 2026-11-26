"""API des inventaires Ansible (ADR 0037, lot 3). Lecture : droit de voir l'espace ; écriture : droit de modifier. Chaque écriture est inscrite à l'audit."""

from django.db import transaction
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core.folders import clean_folder
from tenancy.permissions import EDIT, VIEW, get_workspace

from . import inventory as inv_lib
from .models import Inventory

MAX_INVENTORIES = 200


class InventoryHeader(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)
    folder = serializers.CharField(
        required=False, allow_blank=True, max_length=200
    )  # absent : inchangé (la mise à jour ne range pas)


class InventoryFolder(serializers.Serializer):
    folder = serializers.CharField(allow_blank=True, max_length=200)


class InventorySummary(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    folder = serializers.CharField()
    hosts = serializers.IntegerField()
    groups = serializers.IntegerField()
    updated_at = serializers.DateTimeField()


class ImportInput(serializers.Serializer):
    text = serializers.CharField(max_length=inv_lib.MAX_IMPORT_BYTES, trim_whitespace=False)


def _inventory(ws, slug) -> Inventory:
    inv = Inventory.objects.filter(workspace=ws, slug=slug).first()
    if inv is None:
        raise Http404()
    return inv


def _summary(inv: Inventory) -> dict:
    return {
        "slug": inv.slug,
        "name": inv.name,
        "description": inv.description,
        "folder": inv.folder,
        "hosts": inv.hosts.count(),
        "groups": inv.groups.count(),
        "updated_at": inv.updated_at,
    }


class InventoryListView(APIView):
    serializer_class = InventoryHeader

    @extend_schema(operation_id="workspaces_inventories_list", responses={200: InventorySummary(many=True)})
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        return Response([_summary(i) for i in Inventory.objects.filter(workspace=ws)])

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        head = InventoryHeader(data=request.data)
        head.is_valid(raise_exception=True)
        if Inventory.objects.filter(workspace=ws, slug=head.validated_data["slug"]).exists():
            raise serializers.ValidationError({"slug": "Cet identifiant existe déjà dans l'espace."})
        if Inventory.objects.filter(workspace=ws).count() >= MAX_INVENTORIES:
            raise serializers.ValidationError({"slug": f"{MAX_INVENTORIES} inventaires au plus par espace."})
        doc = inv_lib.clean_document(request.data)
        fields = {**head.validated_data, "folder": clean_folder(head.validated_data.get("folder", ""))}
        inv = Inventory.objects.create(workspace=ws, created_by=request.user, **fields)
        inv_lib.save_document(inv, doc)
        record(
            "inventory.create",
            actor=request.user,
            workspace=ws,
            target=inv,
            hosts=len(doc["hosts"]),
            groups=len(doc["groups"]),
        )
        return Response(inv_lib.load_document(inv), status=201)


class InventoryDetailView(APIView):
    serializer_class = InventoryHeader

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        return Response(inv_lib.load_document(_inventory(ws, slug)))

    @transaction.atomic
    def put(self, request, workspace, slug):
        """Remplace en bloc l'inventaire (nom, description, variables, groupes, machines) : le document est validé en entier avant que rien ne change."""
        ws = get_workspace(request.user, workspace, EDIT)
        inv = _inventory(ws, slug)
        head = (
            InventoryHeader(data={**request.data, "slug": slug})
            if isinstance(request.data, dict)
            else InventoryHeader(data={})
        )
        head.is_valid(raise_exception=True)
        doc = inv_lib.clean_document(request.data)
        inv.name = head.validated_data["name"]
        inv.description = head.validated_data["description"]
        if "folder" in head.validated_data:
            inv.folder = clean_folder(head.validated_data["folder"])
        inv_lib.save_document(inv, doc)
        record(
            "inventory.update",
            actor=request.user,
            workspace=ws,
            target=inv,
            hosts=len(doc["hosts"]),
            groups=len(doc["groups"]),
        )
        return Response(inv_lib.load_document(inv))

    @extend_schema(request=InventoryFolder, responses={200: InventorySummary})
    @transaction.atomic
    def patch(self, request, workspace, slug):
        """Range l'inventaire dans un dossier (rien d'autre ne change)."""
        ws = get_workspace(request.user, workspace, EDIT)
        inv = _inventory(ws, slug)
        ser = InventoryFolder(data=request.data)
        ser.is_valid(raise_exception=True)
        inv.folder = clean_folder(ser.validated_data["folder"])
        inv.save(update_fields=["folder"])
        record("inventory.move", actor=request.user, workspace=ws, target=inv, folder=inv.folder)
        return Response(_summary(inv))

    @transaction.atomic
    def delete(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        inv = _inventory(ws, slug)
        record("inventory.delete", actor=request.user, workspace=ws, target=inv)
        inv.delete()
        return Response(status=204)


class InventoryRenderedView(APIView):
    def get(self, request, workspace, slug):
        """L'inventaire tel que le recevra Ansible (machines désactivées absentes) : un aperçu, sans rien d'autre."""
        ws = get_workspace(request.user, workspace, VIEW)
        return Response(inv_lib.render(_inventory(ws, slug)))


class InventoryImportView(APIView):
    serializer_class = ImportInput

    def post(self, request, workspace):
        """Convertit un inventaire YAML/JSON collé en document, SANS l'enregistrer : l'écran le montre, la personne l'enregistre ensuite."""
        get_workspace(request.user, workspace, EDIT)
        ser = ImportInput(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(inv_lib.clean_document(inv_lib.parse_import(ser.validated_data["text"])))
