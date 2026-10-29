import json

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
from workflows import access
from workflows.models import Workflow

from . import jobs, service
from .models import AiCall, AiJob, AiPolicy, AiProvider
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
                "dry_run": p.dry_run,
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
        for k in ("redact_cloud", "log_content", "dry_run"):
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
            dry_run=p.dry_run,
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
        run = access.run_or_404(
            request.user, ws, WorkflowRun.objects.filter(workspace=ws, pk=request.data.get("run_id"))
        )
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
                diagnosis=service.run_diagnosis(run),
                provider_name=request.data.get("provider"),
            )
        except AiRefused as exc:
            return _refuse(exc)
        return Response(
            {
                **out,
                "workflow": run.workflow_version.workflow.slug,
                "notice": "Explication générée par IA : indicative, vérifiez avant d'agir.",
            }
        )


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
        if len(text) > service.MAX_REQUEST:
            raise serializers.ValidationError(
                {"request": f"Demande trop longue ({service.MAX_REQUEST} caractères au plus) : découpez-la."}
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


class DraftJobView(AiView):
    """Draft generation as a job the worker runs: POST creates it (202); DraftJobDetailView follows it live."""

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        text, slug, name = (
            str(request.data.get("request", "")).strip(),
            str(request.data.get("slug", "")).lower(),
            str(request.data.get("name", "")).strip(),
        )
        outline = request.data.get("outline")
        if outline is not None:  # a plan the person validated (and maybe edited): it becomes the precise request
            if not isinstance(outline, dict):
                raise serializers.ValidationError({"outline": "Plan invalide."})
            try:
                text = service.outline_to_request(text, outline)
            except AiRefused as exc:
                return _refuse(exc)
        if not text or not name or not slug.replace("-", "").isalnum() or len(slug) > 50:
            raise serializers.ValidationError(
                {"request": "Demande, nom et identifiant (lettres, chiffres, tirets) requis."}
            )
        if len(text) > service.MAX_REQUEST:
            raise serializers.ValidationError(
                {"request": f"Demande trop longue ({service.MAX_REQUEST} caractères au plus) : découpez-la."}
            )
        if Workflow.objects.filter(workspace=ws, slug=slug).exists():
            raise serializers.ValidationError({"slug": "Un workflow avec cet identifiant existe déjà."})
        try:  # policy and provider are checked now, so that a refusal is immediate and not a failed job
            service.select_provider(ws, request.data.get("provider"))
        except AiRefused as exc:
            return _refuse(exc)
        job = jobs.enqueue_draft(ws, request.user, text, slug, name[:160], request.data.get("provider"))
        return Response({"id": str(job.id), "status": job.status}, status=202)

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        return Response({"results": jobs.recent_for(request.user, ws)})


class DraftJobCancelView(AiView):
    """Cancel one of one's own generations: it stops at its next phase and nothing is saved."""

    def post(self, request, workspace, job_id):
        ws = get_workspace(request.user, workspace, EDIT)
        job = AiJob.objects.filter(workspace=ws, pk=job_id, user=request.user).first()
        if job is None:
            raise Http404()
        job = jobs.cancel(job)
        record("ai.draft_cancelled", actor=request.user, workspace=ws, job=str(job.id), status=job.status)
        return Response({"id": str(job.id), "status": job.status})


class OutlineJobView(AiView):
    """The plan the assistant envisages before generating: POST creates the job (202), followed with the same detail
    view as a draft job; `previous` + `feedback` ask for a revision of a plan the person has been editing."""

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, EDIT)
        text = str(request.data.get("request", "")).strip()
        if not text or len(text) > service.MAX_REQUEST:
            raise serializers.ValidationError(
                {"request": f"Décrivez le workflow souhaité ({service.MAX_REQUEST} caractères au plus)."}
            )
        previous, feedback = request.data.get("previous"), str(request.data.get("feedback") or "")[:2000]
        if previous is not None and (not isinstance(previous, dict) or len(json.dumps(previous)) > 64 * 1024):
            raise serializers.ValidationError({"previous": "Plan précédent invalide."})
        try:
            service.select_provider(ws, request.data.get("provider"))
        except AiRefused as exc:
            return _refuse(exc)
        job = jobs.enqueue_outline(ws, request.user, text, previous, feedback, request.data.get("provider"))
        return Response({"id": str(job.id), "status": job.status}, status=202)


class DraftJobDetailView(AiView):
    """Live state of a draft job: progress, log of the phases, then the result or the error."""

    def get(self, request, workspace, job_id):
        ws = get_workspace(request.user, workspace, EDIT)
        job = AiJob.objects.filter(workspace=ws, pk=job_id, user=request.user).first()
        if job is None:
            raise Http404()
        return Response(
            {
                "id": str(job.id),
                "status": job.status,
                "progress": job.progress,
                "events": job.events,
                "result": job.result,
                "error": job.error,
            }
        )


class EditWorkflowView(AiView):
    """Conversation turn of the editor's assistant: proposes changes to the draft ; stores nothing."""

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        wf = access.workflow_or_404(request.user, ws, slug, EDIT)
        message = str(request.data.get("message", "")).strip()
        graph = request.data.get("graph")
        if not message or len(message) > service.MAX_REQUEST:
            raise serializers.ValidationError(
                {"message": f"Écrivez une demande ({service.MAX_REQUEST} caractères au plus)."}
            )
        if not isinstance(graph, dict) or len(json.dumps(graph)) > 512 * 1024:
            raise serializers.ValidationError({"graph": "Workflow manquant ou trop volumineux."})
        try:
            return Response(
                service.edit_workflow(
                    ws,
                    request.user,
                    wf,
                    message,
                    request.data.get("history"),
                    graph,
                    provider_name=request.data.get("provider"),
                )
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


class EvalRunsView(AiView):
    """Evaluations of the assistant: list (with the comparison to the previous run) and start one (run by the worker)."""

    def get(self, request, workspace):
        from .models import AiEvalRun

        ws = get_workspace(request.user, workspace, ADMINISTER)
        rows = list(AiEvalRun.objects.filter(workspace=ws).order_by("-created_at")[:50])
        out = []
        for r in rows:
            score = round(100 * r.passed / r.total, 1) if r.total else None
            prev = next(
                (
                    p
                    for p in rows
                    if p.pk < r.pk
                    and p.suite == r.suite
                    and p.provider == r.provider
                    and p.status == "done"
                    and p.total
                ),
                None,
            )
            prev_score = round(100 * prev.passed / prev.total, 1) if prev else None
            out.append(
                {
                    "id": r.pk,
                    "provider": r.provider,
                    "model": r.model,
                    "suite": r.suite,
                    "status": r.status,
                    "passed": r.passed,
                    "total": r.total,
                    "score": score,
                    "tokens": r.tokens,
                    "prompts": r.prompts,
                    "previous_score": prev_score,
                    "regression": bool(score is not None and prev_score is not None and score < prev_score - 10),
                    "error": r.error,
                    "created_at": r.created_at,
                    "finished_at": r.finished_at,
                }
            )
        return Response({"results": out})

    def post(self, request, workspace):
        from .evals import runner
        from .models import AiEvalRun

        ws = get_workspace(request.user, workspace, ADMINISTER)
        suite = request.data.get("suite", "search")
        if suite not in runner.FLAGS:
            raise serializers.ValidationError({"suite": f"Suites : {', '.join(runner.FLAGS)}."})
        prov = AiProvider.objects.filter(workspace=ws, name=request.data.get("provider"), enabled=True).first()
        if prov is None:
            raise serializers.ValidationError({"provider": "Fournisseur inconnu ou désactivé."})
        repeat = request.data.get("repeat", 1)
        if not isinstance(repeat, int) or not 1 <= repeat <= 3:
            raise serializers.ValidationError({"repeat": "De 1 à 3 passes."})
        if AiEvalRun.objects.filter(workspace=ws, status__in=["queued", "running"]).exists():
            raise serializers.ValidationError({"detail": "Une évaluation est déjà en cours dans cet espace."})
        run = AiEvalRun.objects.create(
            workspace=ws,
            provider=prov.name,
            model=prov.model,
            suite=suite,
            created_by=request.user,
            options={
                "repeat": repeat,
                **({"set": request.data["set"]} if request.data.get("set") in ("tuned", "heldout") else {}),
            },
            prompts=runner.prompt_versions(),
        )
        record("ai.eval_start", actor=request.user, workspace=ws, target=run, suite=suite, provider=prov.name)
        return Response({"id": run.pk, "status": run.status}, status=202)


class EvalRunDetailView(AiView):
    def get(self, request, workspace, pk):
        from .models import AiEvalRun

        ws = get_workspace(request.user, workspace, ADMINISTER)
        run = AiEvalRun.objects.filter(workspace=ws, pk=pk).first()
        if run is None:
            raise Http404()
        return Response({"id": run.pk, "status": run.status, "log": run.log, "error": run.error})
