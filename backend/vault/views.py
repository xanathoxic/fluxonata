from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record
from core import netsec
from tenancy.models import Environment
from tenancy.permissions import MANAGE_CONNECTIONS, VIEW, get_workspace

from .models import Connection, Secret

MAX_SECRET = 8192


class SecretCreateSerializer(serializers.Serializer):
    name = serializers.SlugField(max_length=60)
    value = serializers.CharField(max_length=MAX_SECRET, trim_whitespace=False, write_only=True)


class SecretListView(APIView):
    serializer_class = SecretCreateSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, MANAGE_CONNECTIONS)
        rows = Secret.objects.filter(workspace=ws).order_by("name")
        return Response(
            {
                "results": [
                    {
                        "name": s.name,
                        "created_at": s.created_at,
                        "rotated_at": s.rotated_at,
                        "key_id": s.key_id,
                        "used_by": s.connections.count(),
                    }
                    for s in rows
                ]
            }
        )

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, MANAGE_CONNECTIONS)
        ser = SecretCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if Secret.objects.filter(workspace=ws, name=ser.validated_data["name"]).exists():
            raise serializers.ValidationError({"name": "Ce secret existe déjà."})
        s = Secret(workspace=ws, name=ser.validated_data["name"], created_by=request.user)
        s.set_value(ser.validated_data["value"])
        s.save()
        record("secret.create", actor=request.user, workspace=ws, target=s)  # never the value
        return Response({"name": s.name}, status=201)


class SecretDetailView(APIView):
    def put(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, MANAGE_CONNECTIONS)
        s = Secret.objects.filter(workspace=ws, name=name).first()
        value = request.data.get("value")
        if s is None:
            raise Http404()
        if not isinstance(value, str) or not value or len(value) > MAX_SECRET:
            raise serializers.ValidationError({"value": "Valeur requise."})
        s.set_value(value)
        s.rotated_at = timezone.now()
        s.save()
        record("secret.replace", actor=request.user, workspace=ws, target=s)
        return Response(status=204)

    def delete(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, MANAGE_CONNECTIONS)
        s = Secret.objects.filter(workspace=ws, name=name).first()
        if s is None:
            raise Http404()
        if s.connections.exists():
            raise serializers.ValidationError({"name": "Ce secret est utilisé par une connexion."})
        record("secret.delete", actor=request.user, workspace=ws, target=s)
        s.delete()
        return Response(status=204)


class ConnectionSerializer(serializers.ModelSerializer):
    secret = serializers.SlugRelatedField(slug_field="name", read_only=True)
    environment = serializers.CharField(source="environment.kind", default=None, read_only=True)

    class Meta:
        model = Connection
        fields = [
            "id",
            "name",
            "label",
            "kind",
            "config",
            "secret",
            "environment",
            "last_test_ok",
            "last_test_at",
            "last_test_message",
        ]


class ConnectionCreateSerializer(serializers.Serializer):
    name = serializers.SlugField(max_length=60)
    label = serializers.CharField(max_length=120)
    kind = serializers.ChoiceField(choices=Connection.Kind.choices)
    config = serializers.DictField(required=False, default=dict)
    secret = serializers.SlugField(required=False, help_text="Nom d'un secret existant")
    secret_value = serializers.CharField(required=False, write_only=True, max_length=MAX_SECRET, trim_whitespace=False)
    environment = serializers.ChoiceField(choices=["dev", "test", "prod"], required=False, allow_null=True)


def _check_config(kind, config):
    url = config.get("base_url")
    if kind == Connection.Kind.HTTP_BEARER:
        if not url:
            raise serializers.ValidationError({"config": "L'adresse du service (base_url) est requise."})
        try:
            netsec.validate_url(url)
        except netsec.UnsafeURL as exc:
            raise serializers.ValidationError({"config": str(exc)}) from None
    return {k: v for k, v in config.items() if k in ("base_url", "description") and isinstance(v, str)}


class ConnectionListCreateView(APIView):
    serializer_class = ConnectionCreateSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        rows = Connection.objects.filter(workspace=ws).select_related("secret", "environment").order_by("name")
        return Response({"results": ConnectionSerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, MANAGE_CONNECTIONS)
        ser = ConnectionCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        env = Environment.objects.filter(workspace=ws, kind=d["environment"]).first() if d.get("environment") else None
        if d.get("environment") and env is None:
            raise serializers.ValidationError({"environment": "Environnement inconnu."})
        if Connection.objects.filter(workspace=ws, name=d["name"], environment=env).exists():
            raise serializers.ValidationError({"name": "Une connexion de ce nom existe déjà pour cet environnement."})
        config = _check_config(d["kind"], d.get("config", {}))
        secret = None
        if d.get("secret_value"):
            secret = Secret(workspace=ws, name=f"{d['name']}-{(env.kind if env else 'all')}", created_by=request.user)
            if Secret.objects.filter(workspace=ws, name=secret.name).exists():
                raise serializers.ValidationError({"name": "Un secret de ce nom existe déjà."})
            secret.set_value(d["secret_value"])
            secret.save()
        elif d.get("secret"):
            secret = Secret.objects.filter(workspace=ws, name=d["secret"]).first()
            if secret is None:
                raise serializers.ValidationError({"secret": "Secret inconnu dans cet espace."})
        conn = Connection.objects.create(
            workspace=ws,
            name=d["name"],
            label=d["label"],
            kind=d["kind"],
            config=config,
            secret=secret,
            environment=env,
            created_by=request.user,
        )
        record("connection.create", actor=request.user, workspace=ws, target=conn, kind=conn.kind)
        return Response(ConnectionSerializer(conn).data, status=201)


class ConnectionDetailView(APIView):
    def delete(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, MANAGE_CONNECTIONS)
        conn = Connection.objects.filter(workspace=ws, pk=pk).first()
        if conn is None:
            raise Http404()
        record("connection.delete", actor=request.user, workspace=ws, target=conn)
        conn.delete()
        return Response(status=204)


class ConnectionTestView(APIView):
    """Tests a connection without ever revealing the secret (neither in the response, the log nor the audit)."""

    def post(self, request, workspace, pk):
        ws = get_workspace(request.user, workspace, MANAGE_CONNECTIONS)
        conn = Connection.objects.select_related("secret").filter(workspace=ws, pk=pk).first()
        if conn is None:
            raise Http404()
        ok, message = self._run(conn)
        conn.last_test_ok, conn.last_test_at, conn.last_test_message = ok, timezone.now(), message[:300]
        conn.save(update_fields=["last_test_ok", "last_test_at", "last_test_message"])
        record("connection.test", actor=request.user, workspace=ws, target=conn, ok=ok)
        return Response({"ok": ok, "message": message})

    @staticmethod
    def _run(conn) -> tuple[bool, str]:
        if conn.secret is None:
            return False, "Aucun secret n'est associé à cette connexion."
        try:
            secret = conn.secret.reveal()
        except Exception:  # noqa: BLE001
            return False, "Le secret est illisible (clé absente ou donnée corrompue)."
        if conn.kind != Connection.Kind.HTTP_BEARER:
            return True, "Le secret est présent et déchiffrable."
        try:
            status, _, _ = netsec.safe_request(
                "GET", conn.config["base_url"], headers={"Authorization": f"Bearer {secret}"}, timeout=5
            )
        except netsec.UnsafeURL as exc:
            return False, f"Adresse refusée : {exc}"
        except OSError:
            return False, "Le service n'a pas répondu (réseau, délai dépassé ou certificat refusé)."
        if 200 <= status < 300:
            return True, f"Le service a répondu (HTTP {status})."
        if status in (401, 403):
            return False, f"Le service a refusé le jeton (HTTP {status})."
        return False, f"Réponse inattendue du service (HTTP {status})."
