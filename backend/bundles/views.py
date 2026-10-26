from django.http import Http404, HttpResponse
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from runs.artifacts import get_store
from tenancy.permissions import ADMINISTER, PUBLISH, VIEW, get_workspace
from vault import signing

from . import archive, service
from .models import PackageVersion, TrustedKey


class RefSerializer(serializers.Serializer):
    slug = serializers.SlugField()
    version = serializers.RegexField(r"^\d+\.\d+\.\d+$")


class ExportSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    slug = serializers.SlugField(max_length=50)
    version = serializers.RegexField(r"^\d+\.\d+\.\d+$")
    description = serializers.CharField(allow_blank=True, default="", max_length=1000)
    license = serializers.CharField(default="Apache-2.0", max_length=40)
    actions = RefSerializer(many=True, default=list)
    workflows = RefSerializer(many=True, default=list)
    sign = serializers.BooleanField(default=True)


class PackageVersionSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(source="package.slug")
    name = serializers.CharField(source="package.name")
    download_url = serializers.SerializerMethodField()
    contents = serializers.SerializerMethodField()

    class Meta:
        model = PackageVersion
        fields = ["slug", "name", "version", "checksum", "origin", "created_at", "download_url", "contents"]

    def get_download_url(self, obj) -> str:
        return f"/api/v1/workspaces/{obj.package.workspace.slug}/packages/{obj.package.slug}/versions/{obj.version}/download/"

    def get_contents(self, obj) -> dict:
        return {k: len(v) for k, v in obj.manifest["contents"].items()}


class ExportView(APIView):
    serializer_class = ExportSerializer

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        ser = ExportSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        meta = {k: d[k] for k in ("name", "slug", "version", "description", "license")}
        pv = service.export_package(
            ws,
            request.user,
            meta=meta,
            actions=[dict(a) for a in d["actions"]],
            workflows=[dict(w) for w in d["workflows"]],
            sign=d["sign"],
        )
        return Response(PackageVersionSerializer(pv).data, status=201)


class PackageListView(ListAPIView):
    serializer_class = PackageVersionSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        return (
            PackageVersion.objects.filter(package__workspace=ws)
            .select_related("package__workspace")
            .order_by("-created_at")
        )


class DownloadView(APIView):
    def get(self, request, workspace, slug, version):
        ws = get_workspace(request.user, workspace, VIEW)
        pv = PackageVersion.objects.filter(package__workspace=ws, package__slug=slug, version=version).first()
        if pv is None:
            raise Http404()
        resp = HttpResponse(get_store().get(pv.archive_key), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{slug}-{version}.zip"'
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


class ImportView(APIView):
    """multipart/form-data: file, mode (preview|apply), strategy (abort|skip)."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        up = request.FILES.get("file")
        if up is None:
            raise serializers.ValidationError({"file": "Fichier requis."})
        if up.size > archive.MAX_ARCHIVE:
            raise serializers.ValidationError({"file": "Archive trop volumineuse (5 Mo maximum)."})
        mode = request.data.get("mode", "preview")
        strategy = request.data.get("strategy", "abort")
        if mode not in ("preview", "apply") or strategy not in ("abort", "skip"):
            raise serializers.ValidationError({"mode": "Mode ou stratégie inconnu."})
        report = service.import_package(ws, request.user, up.read(), apply=(mode == "apply"), strategy=strategy)
        return Response(report, status=200 if report.get("can_apply") or not report["errors"] else 400)


class TrustedKeysView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rows = TrustedKey.objects.filter(workspace=ws).order_by("name")
        return Response(
            {
                "instance_public_key": signing.public_b64(),
                "instance_key_id": signing.key_id(signing.public_b64()),
                "results": [{"id": k.pk, "name": k.name, "key_id": k.key_id, "public_key": k.public_key} for k in rows],
            }
        )

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        name, pub = str(request.data.get("name", "")).strip()[:120], str(request.data.get("public_key", "")).strip()
        try:
            kid = signing.key_id(pub)
            if len(__import__("base64").b64decode(pub, validate=True)) != 32:
                raise ValueError
        except ValueError:
            raise serializers.ValidationError(
                {"public_key": "Clé publique Ed25519 invalide (base64, 32 octets)."}
            ) from None
        if not name:
            raise serializers.ValidationError({"name": "Nom requis."})
        key, created = TrustedKey.objects.get_or_create(
            workspace=ws, public_key=pub, defaults={"name": name, "key_id": kid, "created_by": request.user}
        )
        if created:
            record("package.trust_key", actor=request.user, workspace=ws, target=key, key_id=kid)
        return Response({"id": key.pk, "key_id": kid}, status=201 if created else 200)


class TrustedKeyDeleteView(APIView):
    def delete(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        key = TrustedKey.objects.filter(workspace=ws, pk=pk).first()
        if key is None:
            raise Http404()
        record("package.untrust_key", actor=request.user, workspace=ws, target=key)
        key.delete()
        return Response(status=204)
