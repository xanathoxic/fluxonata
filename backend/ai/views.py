from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.models import record
from catalog.models import Action
from core import netsec
from runs.models import StepRun, WorkflowRun
from tenancy.permissions import ADMINISTER, EDIT, PUBLISH, VIEW, get_workspace
from vault.models import Secret

from . import service
from .models import AiCall, AiPolicy, AiProvider
from .service import AiRefused


def _refuse(exc: AiRefused):
    return Response({"error": {"code": exc.code, "message": exc.message, "details": None}}, status=exc.status)


class AiView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai"


class PolicyView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        p = service.policy_for(ws)
        return Response(
            {
                "mode": p.mode,
                "daily_token_budget": p.daily_token_budget,
                "redact_cloud": p.redact_cloud,
                "log_content": p.log_content,
                "used_today": service.used_today(ws),
                "modes": {k: v for k, v in AiPolicy.Mode.choices},
            }
        )

    def put(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        p = service.policy_for(ws)
        d = request.data
        if d.get("mode") not in AiPolicy.Mode.values:
            raise serializers.ValidationError({"mode": "Mode inconnu."})
        p.mode = d["mode"]
        if isinstance(d.get("daily_token_budget"), int) and 0 <= d["daily_token_budget"] <= 50_000_000:
            p.daily_token_budget = d["daily_token_budget"]
        for k in ("redact_cloud", "log_content"):
            if isinstance(d.get(k), bool):
                setattr(p, k, d[k])
        p.updated_by = request.user
        p.save()
        record(
            "ai.policy",
            actor=request.user,
            workspace=ws,
            mode=p.mode,
            budget=p.daily_token_budget,
            log_content=p.log_content,
        )
        return Response({"mode": p.mode})


class ProviderSerializer(serializers.ModelSerializer):
    secret = serializers.SlugRelatedField(
        slug_field="name", queryset=Secret.objects.none(), required=False, allow_null=True
    )
    cloud = serializers.BooleanField(source="is_cloud", read_only=True)

    class Meta:
        model = AiProvider
        fields = ["name", "kind", "base_url", "model", "secret", "timeout_seconds", "max_tokens", "enabled", "cloud"]


class ProviderListView(APIView):
    serializer_class = ProviderSerializer

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        return Response(
            {"results": ProviderSerializer(AiProvider.objects.filter(workspace=ws).order_by("name"), many=True).data}
        )

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        ser = ProviderSerializer(data=request.data)
        ser.fields["secret"].queryset = Secret.objects.filter(workspace=ws)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if AiProvider.objects.filter(workspace=ws, name=d["name"]).exists():
            raise serializers.ValidationError({"name": "Ce fournisseur existe déjà."})
        if d["kind"] != AiProvider.Kind.FAKE:
            if not d.get("model"):
                raise serializers.ValidationError({"model": "Indiquez le nom du modèle."})
            url = d.get("base_url") or (
                "https://api.anthropic.com" if d["kind"] == AiProvider.Kind.CLOUD_ANTHROPIC else ""
            )
            try:
                netsec.validate_url(url)
            except netsec.UnsafeURL as exc:
                raise serializers.ValidationError(
                    {"base_url": f"{exc} (un serveur interne doit figurer dans ORCH_EGRESS_ALLOWLIST)."}
                ) from None
        if d["kind"] == AiProvider.Kind.CLOUD_ANTHROPIC and not d.get("secret"):
            raise serializers.ValidationError(
                {"secret": "Une clé d'API (secret) est requise pour un fournisseur cloud."}
            )
        p = AiProvider.objects.create(workspace=ws, **d)
        record("ai.provider.create", actor=request.user, workspace=ws, target=p, kind=p.kind, cloud=p.is_cloud)
        return Response(ProviderSerializer(p).data, status=201)


class ProviderDetailView(APIView):
    def delete(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        p = AiProvider.objects.filter(workspace=ws, name=name).first()
        if p is None:
            raise Http404()
        record("ai.provider.delete", actor=request.user, workspace=ws, target=p)
        p.delete()
        return Response(status=204)


class CapabilitiesView(APIView):
    def get(self, request, workspace, name):
        ws = get_workspace(request.user, workspace, VIEW)
        cfg = AiProvider.objects.filter(workspace=ws, name=name).first()
        if cfg is None:
            raise Http404()
        pol = service.policy_for(ws)
        caps = service.build_provider(cfg).capabilities() if not (cfg.is_cloud and not cfg.secret) else {}
        allowed = pol.mode != "off" and not (cfg.is_cloud and pol.mode == "local_strict")
        return Response(
            {
                "name": cfg.name,
                "kind": cfg.kind,
                "cloud": cfg.is_cloud,
                "capabilities": caps,
                "allowed_by_policy": allowed,
                "model": cfg.model,
                "timeout_seconds": cfg.timeout_seconds,
                "max_tokens": cfg.max_tokens,
            }
        )


class UsageView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        rows = AiCall.objects.filter(workspace=ws).order_by("-id")[:100]
        return Response(
            {
                "used_today": service.used_today(ws),
                "results": [
                    {
                        "at": c.created_at,
                        "provider": c.provider,
                        "kind": c.kind,
                        "model": c.model,
                        "use_case": c.use_case,
                        "prompt_version": c.prompt_version,
                        "tokens": c.input_tokens + c.output_tokens,
                        "latency_ms": c.latency_ms,
                        "status": c.status,
                        "error_code": c.error_code,
                    }
                    for c in rows
                ],
            }
        )


class CatalogSearchView(AiView):
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        q = str(request.data.get("query", "")).strip()
        if not q:
            raise serializers.ValidationError({"query": "Décrivez ce que vous cherchez."})
        try:
            return Response(service.catalog_search(ws, request.user, q, provider_name=request.data.get("provider")))
        except AiRefused as exc:
            return _refuse(exc)


class ExplainErrorView(AiView):
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        run = WorkflowRun.objects.filter(workspace=ws, pk=request.data.get("run_id")).first()
        if run is None:
            raise Http404()
        step = StepRun.objects.filter(run=run, status="failed").order_by("-seq").first()
        err = (step.error if step else None) or run.error or {}
        node = next((n for n in run.definition["graph"]["nodes"] if step and n["id"] == step.node_id), {})
        try:
            out = service.explain_error(
                ws,
                request.user,
                code=str(err.get("code", "")),
                message=str(err.get("message", "")),
                step_name=str(node.get("name", "")),
                provider_name=request.data.get("provider"),
            )
        except AiRefused as exc:
            return _refuse(exc)
        return Response({**out, "notice": "Explication générée par IA : indicative, vérifiez avant d'agir."})


class DraftWorkflowView(AiView):
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        text, slug, name = (
            str(request.data.get("request", "")).strip(),
            str(request.data.get("slug", "")),
            str(request.data.get("name", "")).strip(),
        )
        if not text or not name or not slug.replace("-", "").isalnum() or len(slug) > 50:
            raise serializers.ValidationError(
                {"request": "Demande, nom et identifiant (lettres, chiffres, tirets) requis."}
            )
        try:
            return Response(
                service.draft_workflow(
                    ws, request.user, text, slug.lower(), name[:160], provider_name=request.data.get("provider")
                ),
                status=201,
            )
        except AiRefused as exc:
            return _refuse(exc)


class SuggestTestsView(AiView):
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        action = Action.objects.filter(workspace=ws, slug=slug).first()
        if action is None:
            raise Http404()
        try:
            return Response(service.suggest_tests(ws, request.user, action, provider_name=request.data.get("provider")))
        except AiRefused as exc:
            return _refuse(exc)


class ProposeActionView(AiView):
    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, PUBLISH)
        need = str(request.data.get("need", "")).strip()
        if not need:
            raise serializers.ValidationError({"need": "Décrivez le besoin."})
        try:
            return Response(service.propose_action(ws, request.user, need, provider_name=request.data.get("provider")))
        except AiRefused as exc:
            return _refuse(exc)
