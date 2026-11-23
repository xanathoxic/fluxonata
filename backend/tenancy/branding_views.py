"""API de l'image de marque (ADR 0033).

- Publique, sans session (la page de connexion en a besoin) : `GET /branding/` et `GET /branding/assets/<logo|icon>/`.
- Administrateurs de la plate-forme seulement, contrôlé côté serveur et audité : `/platform/branding/…`.
Les images sont servies avec un en-tête qui interdit toute exécution (`sandbox`) et ne sont jamais interprétées comme une page."""

from django.db import transaction
from django.db.models.functions import Length
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record

from . import branding as b
from .models import Branding, BrandingAsset
from .platform_views import _Guarded

TEXT_FIELDS = {
    "product_name": 60,
    "tagline": 140,
    "login_message": 300,
    "footer_text": 200,
}


def _assets() -> dict[str, BrandingAsset]:
    return {a.kind: a for a in BrandingAsset.objects.defer("data").annotate(size=Length("data"))}


def _asset_ref(a: BrandingAsset | None) -> dict | None:
    if a is None:
        return None
    return {
        "url": f"/api/v1/branding/assets/{a.kind}/?v={a.sha256[:12]}",
        "content_type": a.content_type,
        "width": a.width,
        "height": a.height,
        "bytes": getattr(a, "size", None),
    }


def public_payload() -> dict:
    """Ce qui s'applique à tous, avant même la connexion : rien de secret, rien d'interne."""
    br = Branding.get()
    assets = _assets()
    return {
        "version": br.version,
        "name": br.product_name or b.DEFAULT_NAME,
        "custom_name": bool(br.product_name),
        "tagline": br.tagline,
        "login_message": br.login_message,
        "footer": {"text": br.footer_text, "url": br.footer_url},
        "accent": br.accent or b.DEFAULT_ACCENT,
        "custom_accent": bool(br.accent),
        "topbar_bg": br.topbar_bg,
        "sidebar_bg": br.sidebar_bg,
        "login_style": br.login_style,
        "logo_has_name": br.logo_has_name,
        "logo": _asset_ref(assets.get("logo")),
        "logo_dark": _asset_ref(assets.get("logo_dark")),
        "icon": _asset_ref(assets.get("icon")),
    }


class PublicBrandingView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(operation_id="branding_retrieve", responses=OpenApiTypes.OBJECT)
    def get(self, request):
        r = Response(public_payload())
        r["Cache-Control"] = "no-cache"
        return r


class PublicAssetView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(operation_id="branding_asset_retrieve", responses={200: OpenApiTypes.BINARY})
    def get(self, request, kind):
        a = BrandingAsset.objects.filter(kind=kind).first() if kind in b.KINDS else None
        if a is None:
            return HttpResponse(status=404)
        etag = f'"{a.sha256[:16]}"'
        if request.headers.get("If-None-Match") == etag:
            return HttpResponse(status=304, headers={"ETag": etag})
        res = HttpResponse(bytes(a.data), content_type=a.content_type)
        res["ETag"] = etag
        # l'adresse porte l'empreinte (?v=) : changer l'image change l'adresse, le cache peut donc être long
        res["Cache-Control"] = "public, max-age=31536000, immutable" if request.GET.get("v") else "public, max-age=300"
        res["X-Content-Type-Options"] = "nosniff"
        res["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        res["Cross-Origin-Resource-Policy"] = "same-origin"
        return res


def admin_payload() -> dict:
    br = Branding.get()
    pub = public_payload()
    return pub | {
        "defaults": {"name": b.DEFAULT_NAME, "accent": b.DEFAULT_ACCENT},
        "contrast": b.contrast_report(pub["accent"]),
        "limits": {"image_bytes": b.MAX_IMAGE_BYTES, "side": b.MAX_SIDE, "formats": ["PNG", "JPEG", "WebP", "SVG"]},
        "stored": {
            "product_name": br.product_name,
            "accent": br.accent,
            "topbar_bg": br.topbar_bg,
            "sidebar_bg": br.sidebar_bg,
        },
        "updated_at": br.updated_at,
        "updated_by": br.updated_by.username if br.updated_by else "",
    }


class BrandingSerializer(serializers.Serializer):
    product_name = serializers.CharField(max_length=60, required=False, allow_blank=True)
    tagline = serializers.CharField(max_length=140, required=False, allow_blank=True)
    login_message = serializers.CharField(max_length=300, required=False, allow_blank=True)
    footer_text = serializers.CharField(max_length=200, required=False, allow_blank=True)
    footer_url = serializers.CharField(max_length=300, required=False, allow_blank=True)
    accent = serializers.CharField(max_length=7, required=False, allow_blank=True)
    topbar_bg = serializers.CharField(max_length=7, required=False, allow_blank=True)
    sidebar_bg = serializers.CharField(max_length=7, required=False, allow_blank=True)
    login_style = serializers.ChoiceField(choices=Branding.LoginStyle.choices, required=False)
    logo_has_name = serializers.BooleanField(required=False)

    def validate_accent(self, v):
        try:
            return b.validate_accent(v)
        except b.BrandingError as e:
            raise serializers.ValidationError(str(e)) from None

    def _nav(self, v):
        try:
            return b.validate_nav_color(v)
        except b.BrandingError as e:
            raise serializers.ValidationError(str(e)) from None

    def validate_topbar_bg(self, v):
        return self._nav(v)

    def validate_sidebar_bg(self, v):
        return self._nav(v)

    def validate_footer_url(self, v):
        try:
            return b.validate_https_url(v)
        except b.BrandingError as e:
            raise serializers.ValidationError(str(e)) from None

    def validate(self, attrs):
        for name in ("product_name", "tagline", "login_message", "footer_text"):
            if name in attrs:
                attrs[name] = " ".join(attrs[name].split())
                if any(ord(c) < 32 for c in attrs[name]):
                    raise serializers.ValidationError({name: "Caractères de contrôle interdits."})
        return attrs


class BrandingAdminView(_Guarded):
    @extend_schema(operation_id="platform_branding_retrieve", responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(admin_payload())

    @extend_schema(operation_id="platform_branding_update", request=BrandingSerializer, responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def patch(self, request):
        s = BrandingSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        br = Branding.objects.select_for_update().get_or_create(pk=1)[0]
        changed = [k for k, v in s.validated_data.items() if getattr(br, k) != v]
        for k in changed:
            setattr(br, k, s.validated_data[k])
        if changed:
            br.version += 1
            br.updated_by = request.user
            br.save()
            record("platform.branding_update", actor=request.user, target=br, fields=changed)
        return Response(admin_payload())


class BrandingAssetAdminView(_Guarded):
    parser_classes = [MultiPartParser]

    @extend_schema(
        operation_id="platform_branding_asset_upload",
        request={
            "multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}
        },
        responses=OpenApiTypes.OBJECT,
    )
    @transaction.atomic
    def put(self, request, kind):
        if kind not in b.KINDS:
            return Response({"error": {"code": "not_found", "message": "Image inconnue.", "details": None}}, status=404)
        upload = request.FILES.get("file")
        if upload is None:
            raise serializers.ValidationError({"file": "Choisissez un fichier."})
        if upload.size > b.MAX_IMAGE_BYTES:
            raise serializers.ValidationError({"file": f"Le fichier dépasse {b.MAX_IMAGE_BYTES // 1024} Ko."})
        try:
            img = b.inspect_image(upload.read(b.MAX_IMAGE_BYTES + 1))
            if kind == "icon":
                b.validate_icon_shape(img)
        except b.BrandingError as e:
            raise serializers.ValidationError({"file": str(e)}) from None
        BrandingAsset.objects.update_or_create(
            kind=kind,
            defaults={
                "content_type": img.content_type,
                "data": img.data,
                "sha256": img.sha256,
                "width": img.width,
                "height": img.height,
            },
        )
        br = Branding.objects.select_for_update().get_or_create(pk=1)[0]
        br.version += 1
        br.updated_by = request.user
        br.save()
        record(
            "platform.branding_asset_set",
            actor=request.user,
            target=br,
            kind=kind,
            content_type=img.content_type,
            bytes=len(img.data),
        )
        return Response(admin_payload())

    @extend_schema(operation_id="platform_branding_asset_delete", responses={200: OpenApiTypes.OBJECT})
    @transaction.atomic
    def delete(self, request, kind):
        if kind not in b.KINDS:
            return Response({"error": {"code": "not_found", "message": "Image inconnue.", "details": None}}, status=404)
        removed, _ = BrandingAsset.objects.filter(kind=kind).delete()
        if removed:
            br = Branding.objects.select_for_update().get_or_create(pk=1)[0]
            br.version += 1
            br.updated_by = request.user
            br.save()
            record("platform.branding_asset_remove", actor=request.user, target=br, kind=kind)
        return Response(admin_payload())


class BrandingResetView(_Guarded):
    @extend_schema(operation_id="platform_branding_reset", request=None, responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def post(self, request):
        br = Branding.objects.select_for_update().get_or_create(pk=1)[0]
        before = br.version
        BrandingAsset.objects.all().delete()
        for f in (*TEXT_FIELDS, "footer_url", "accent", "topbar_bg", "sidebar_bg"):
            setattr(br, f, "")
        br.login_style = Branding.LoginStyle.PLAIN
        br.logo_has_name = True
        br.version = before + 1
        br.updated_by = request.user
        br.save()
        record("platform.branding_reset", actor=request.user, target=br)
        return Response(admin_payload())
