"""Platform administration of the external vault: the server and its AppRole, a connection test, and what each workspace
may reach. Platform administrators only, everything audited, the AppRole `secret_id` is write-only."""

import ssl

from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core import netsec
from tenancy.models import Workspace
from tenancy.permissions import require_platform_admin

from . import external
from .models import Secret, VaultConfig, VaultGrant

SEG = external.SEGMENT


class _Guarded(APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_platform_admin(request)


def _row(cfg: VaultConfig | None) -> dict:
    cfg = cfg or VaultConfig()
    return {
        "address": cfg.address,
        "namespace": cfg.namespace,
        "kv_mount": cfg.kv_mount,
        "base_prefix": cfg.base_prefix,
        "auth_mount": cfg.auth_mount,
        "role_id": cfg.role_id,
        "secret_id_set": bool(cfg.secret_id_ciphertext),
        "ca_set": bool(cfg.ca_pem),
        "verify_tls": cfg.verify_tls,
        "enabled": cfg.enabled,
        "last_test_ok": cfg.last_test_ok,
        "last_test_at": cfg.last_test_at,
        "last_test_message": cfg.last_test_message,
        "secrets": {
            "internal": Secret.objects.filter(provider=Secret.Provider.INTERNAL).count(),
            "vault": Secret.objects.filter(provider=Secret.Provider.VAULT).count(),
        },
    }


class ConfigSerializer(serializers.Serializer):
    address = serializers.URLField(max_length=300)
    namespace = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    kv_mount = serializers.CharField(max_length=80, default="secret")
    base_prefix = serializers.CharField(max_length=120, default="fluxonata")
    auth_mount = serializers.CharField(max_length=80, default="approle")
    role_id = serializers.CharField(max_length=120)
    secret_id = serializers.CharField(
        max_length=300, required=False, allow_blank=True, write_only=True, trim_whitespace=False
    )
    ca_pem = serializers.CharField(max_length=20000, required=False, allow_blank=True)
    verify_tls = serializers.BooleanField(default=True)
    enabled = serializers.BooleanField(default=False)

    def validate_address(self, v):
        v = v.rstrip("/")
        parts = netsec.urlsplit(v)
        if parts.scheme != "https" and not netsec.is_allowlisted(parts.hostname or ""):
            raise serializers.ValidationError(
                "Utilisez https : l'identifiant AppRole et les secrets circuleraient en clair (http n'est accepté que pour "
                "un hôte explicitement autorisé par l'opérateur)."
            )
        return v

    def validate(self, d):
        for key in ("kv_mount", "auth_mount"):
            if not SEG.match(d[key].strip("/")):
                raise serializers.ValidationError({key: "Lettres, chiffres, . _ - uniquement."})
            d[key] = d[key].strip("/")
        try:
            d["base_prefix"] = external.clean_path(d["base_prefix"], "préfixe")
        except external.ExternalVaultError as exc:
            raise serializers.ValidationError({"base_prefix": exc.message}) from None
        if d.get("ca_pem"):
            try:
                ssl.create_default_context(cadata=d["ca_pem"])
            except ssl.SSLError:
                raise serializers.ValidationError({"ca_pem": "Certificat illisible (format PEM attendu)."}) from None
        return d


class VaultConfigView(_Guarded):
    def get(self, request):
        return Response(_row(external.config()))

    def put(self, request):
        ser = ConfigSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        cfg, _ = VaultConfig.objects.get_or_create(pk=1)
        for f in ("address", "namespace", "kv_mount", "base_prefix", "auth_mount", "role_id", "verify_tls"):
            setattr(cfg, f, d[f])
        if "ca_pem" in d:
            cfg.ca_pem = d["ca_pem"].strip()
        if d.get("secret_id"):
            cfg.set_secret_id(d["secret_id"])
        if d["enabled"] and not cfg.secret_id_ciphertext:
            raise serializers.ValidationError(
                {"secret_id": "Le secret_id de l'AppRole est requis pour activer le coffre."}
            )
        cfg.enabled = d["enabled"]
        cfg.updated_by = request.user
        cfg.last_test_ok, cfg.last_test_at, cfg.last_test_message = None, None, ""  # the new settings are untested
        cfg.save()
        external.forget()
        # never the secret_id: only what changed in the settings
        record(
            "platform.vault_configure",
            actor=request.user,
            enabled=cfg.enabled,
            address=cfg.address,
            kv_mount=cfg.kv_mount,
            secret_id_changed=bool(d.get("secret_id")),
        )
        return Response(_row(cfg))


class VaultTestView(_Guarded):
    def post(self, request):
        cfg = external.config()
        if cfg is None or not cfg.address or not cfg.role_id or not cfg.secret_id_ciphertext:
            raise serializers.ValidationError({"detail": "Enregistrez d'abord l'adresse, le role_id et le secret_id."})
        result = external.test(cfg)
        failed = next((s for s in result["steps"] if not s["ok"]), None)
        cfg.last_test_ok = result["ok"]
        cfg.last_test_at = timezone.now()
        cfg.last_test_message = ("Tout fonctionne." if result["ok"] else (failed or {}).get("message", "Échec."))[:300]
        cfg.save(update_fields=["last_test_ok", "last_test_at", "last_test_message"])
        record("platform.vault_test", actor=request.user, ok=result["ok"])
        return Response(result)


class VaultGrantsView(_Guarded):
    """What each workspace may reach besides its own area. GET: every workspace; PUT: replace one workspace's list."""

    def get(self, request):
        cfg = external.config() or VaultConfig()
        rows = []
        for ws in Workspace.objects.order_by("name"):
            rows.append(
                {
                    "workspace": ws.slug,
                    "name": ws.name,
                    "own_prefix": f"{cfg.kv_mount}/{external.own_prefix(cfg, ws)}",
                    "grants": list(ws.vault_grants.order_by("pattern").values("pattern", "writable")),
                    "secrets": ws.secrets.filter(provider=Secret.Provider.VAULT).count(),
                }
            )
        return Response({"results": rows})

    def put(self, request, workspace):
        ws = Workspace.objects.filter(slug=workspace).first()
        if ws is None:
            raise serializers.ValidationError({"workspace": "Espace inconnu."})
        grants = request.data.get("grants")
        if not isinstance(grants, list) or len(grants) > 50:
            raise serializers.ValidationError({"grants": "Une liste de 50 motifs au plus."})
        clean: dict[str, bool] = {}
        try:
            for g in grants:
                if not isinstance(g, dict):
                    raise external.ExternalVaultError("VAULT_PATTERN_REFUSED", "Chaque motif : {pattern, writable}.")
                clean[external.check_pattern(g.get("pattern"))] = bool(g.get("writable"))
        except external.ExternalVaultError as exc:
            raise serializers.ValidationError({"grants": exc.message}) from None
        ws.vault_grants.exclude(pattern__in=list(clean)).delete()
        for pattern, writable in clean.items():
            VaultGrant.objects.update_or_create(
                workspace=ws, pattern=pattern, defaults={"writable": writable, "created_by": request.user}
            )
        external.forget()
        record("platform.vault_grants", actor=request.user, workspace=ws, patterns=sorted(clean))
        return Response(
            {"workspace": ws.slug, "grants": [{"pattern": p, "writable": w} for p, w in sorted(clean.items())]}
        )
