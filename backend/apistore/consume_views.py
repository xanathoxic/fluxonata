"""Appels des consommateurs, authentifiés par une clé de souscription (jamais par session ni par jeton d'API)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from runs.models import WorkflowRun

from . import service
from .models import UsageEvent


class InvokeSerializer(serializers.Serializer):
    environment = serializers.ChoiceField(choices=["dev", "test", "prod"])
    inputs = serializers.DictField(default=dict)


def _key_from(request) -> service.ApiKey:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise service.Refused(401, "invalid_key", "Clé absente : en-tête « Authorization: Bearer fxs_… » attendu.")
    return service.authenticate(header[7:].strip())


def _refusal(exc: service.Refused) -> Response:
    return Response({"detail": exc.message, "code": exc.outcome}, status=exc.status)


class ConsumerView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]  # l'identité est la clé de souscription, contrôlée par `service.authenticate`


@extend_schema(exclude=True)  # extension optionnelle : hors du contrat OpenAPI du cœur (docs/API_STORE.md)
class InvokeView(ConsumerView):
    serializer_class = InvokeSerializer

    def post(self, request, workspace, product, workflow):
        try:
            key = _key_from(request)
            sub = key.subscription
            # Un produit ou un espace différent de celui de la clé ne révèle rien : même réponse qu'un workflow absent.
            if sub.product.slug != product or sub.product.workspace.slug != workspace:
                raise service.Refused(404, "forbidden", "Ce workflow n'appartient pas à ce produit.")
            body = InvokeSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            run, created = service.invoke(
                service.Call(
                    key=key,
                    workflow_slug=workflow,
                    environment=body.validated_data["environment"],
                    inputs=body.validated_data["inputs"],
                    idempotency_key=request.headers.get("Idempotency-Key"),
                )
            )
        except service.Refused as exc:
            return _refusal(exc)
        return Response(
            {"run_id": str(run.id), "status": run.status, "replayed": not created}, status=202 if created else 200
        )


@extend_schema(exclude=True)
class RunStatusView(ConsumerView):
    """État et résultat d'une exécution, limités à celles de la souscription de la clé."""

    def get(self, request, workspace, product, run_id):
        try:
            key = _key_from(request)
        except service.Refused as exc:
            return _refusal(exc)
        sub = key.subscription
        owned = (
            sub.product.slug == product
            and sub.product.workspace.slug == workspace
            and UsageEvent.objects.filter(subscription=sub, run_id=run_id, outcome="accepted").exists()
        )
        run = WorkflowRun.objects.filter(pk=run_id).first() if owned else None
        if run is None:
            return Response({"detail": "Exécution introuvable.", "code": "not_found"}, status=404)
        done = run.status in ("succeeded", "failed", "cancelled")
        return Response(
            {
                "run_id": str(run.id),
                "status": run.status,
                "outputs": run.outputs if run.status == "succeeded" else None,
                "finished": done,
            }
        )
