"""MCP de conception — SUIVRE les exécutions et DEMANDER un lancement (ADR 0025, amendement du 2026-11-26).

Lecture : `list_runs`, `get_run` (étapes, sorties, erreurs, événements), `list_run_requests`, `get_run_request`. Mêmes droits que l'interface :
dossiers privés respectés, aucun jeton de reprise d'une attente, journaux déjà masqués par les workers.

Lancement : `request_run` dépose une demande (dev ou recette seulement) qu'une personne connectée confirme dans l'interface
(Approbations › Lancements demandés par un assistant) ; sans confirmation, elle expire. Seule exception : si un administrateur a activé la « validation
automatique » de l'espace, la demande est lancée tout de suite au nom du propriétaire du jeton. Voir `runs/run_requests.py` pour les garde-fous."""

import json

from django.http import Http404
from rest_framework import serializers

from audit.models import record
from runs import run_requests
from runs.models import RunRequest, StepRun, WorkflowRun
from workflows import access

from .mcp_design_common import LIMIT, SLUG, _limit, _ok, _spec, _text
from .models import Workflow

STEP_OUTPUT = 2_000  # caractères de sortie par étape dans get_run (la sortie entière : paramètre `step`)
STEP_FULL = 20_000
LOG_TAIL = 1_500
LOG_FULL = 8_000
MAX_EVENTS = 40
RUN_STATUS = [s for s, _ in WorkflowRun.Status.choices]


def _json_clip(value, limit: int):
    """La valeur telle quelle si elle tient ; sinon son JSON tronqué (texte), avec l'indication."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value, False
    return text[:limit], True


def _run_row(r: WorkflowRun) -> dict:
    return {
        "run_id": str(r.id),
        "workflow": r.definition["workflow"]["slug"],
        "version": r.definition["workflow"]["version"],
        "environment": r.environment.kind,
        "status": r.status,
        "requested_by": getattr(r.requested_by, "username", None),
        "created_at": r.created_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "error": (r.error or {}).get("message") if r.error else None,
    }


def _visible_runs(ws, user):
    qs = WorkflowRun.objects.filter(workspace=ws, parent_step__isnull=True).select_related(
        "environment", "requested_by", "workflow_version__workflow"
    )
    return access.visible_runs(qs, user, ws)


def list_runs(request, ws, args):
    qs = _visible_runs(ws, request.user)
    slug, status, env = _text(args, "workflow"), _text(args, "status"), _text(args, "environment")
    if slug:
        qs = qs.filter(workflow_version__workflow__slug=slug)
    if status in RUN_STATUS:
        qs = qs.filter(status=status)
    if env in ("dev", "test", "prod"):
        qs = qs.filter(environment__kind=env)
    rows = [_run_row(r) for r in qs.order_by("-created_at")[: _limit(args)]]
    return _ok({"count": len(rows), "results": rows})


def _step_row(s: StepRun, names: dict, detail: bool) -> dict:
    out, cut = _json_clip(s.output, STEP_FULL if detail else STEP_OUTPUT)
    row = {
        "step": s.node_id,
        "name": names.get(s.node_id, s.node_id),
        "type": s.node_type,
        "status": s.status,
        "attempt": s.attempt,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        "output": out,
        "output_truncated": cut,
        "error": s.error,
    }
    ex = s.executions.order_by("-created_at").first()
    if ex is not None:
        logs = "\n".join(a.logs for a in ex.attempts.order_by("number") if a.logs)
        row["logs_tail"] = logs[-(LOG_FULL if detail else LOG_TAIL) :]
    return row


def get_run(request, ws, args):
    try:
        pk = serializers.UUIDField().run_validation(args.get("run_id"))
    except serializers.ValidationError:
        raise Http404() from None
    run = _visible_runs(ws, request.user).filter(pk=pk).first()
    if run is None:
        raise Http404()
    names = {n["id"]: n.get("name", n["id"]) for n in run.definition["graph"]["nodes"]}
    only = args.get("step") if isinstance(args.get("step"), str) else None
    steps = run.steps.order_by("started_at", "id")
    if only:
        steps = steps.filter(node_id=only)
        if not steps.exists():
            raise Http404()
    inputs, inputs_cut = _json_clip(run.inputs, STEP_OUTPUT)
    outputs, outputs_cut = _json_clip(run.outputs, STEP_FULL if only else STEP_OUTPUT)
    out = {
        **_run_row(run),
        "finished": run.status in ("succeeded", "failed", "cancelled"),
        "inputs": inputs,
        "inputs_truncated": inputs_cut,
        "outputs": outputs,
        "outputs_truncated": outputs_cut,
        "error_detail": run.error,
        "steps": [_step_row(s, names, bool(only)) for s in steps],
        "events": [
            {"type": e.type, "step": e.node_id, "message": e.message, "at": e.created_at.isoformat()}
            for e in run.events.order_by("-seq")[:MAX_EVENTS][::-1]
        ],
        "children": run.steps.filter(child_runs__isnull=False).distinct().count(),
        "hint": "Rappelez get_run tant que finished est faux ; `step` donne la sortie complète d'une étape.",
    }
    return _ok(out)


def _request_row(r: RunRequest) -> dict:
    return {
        "request_id": str(r.id),
        "workflow": r.workflow_version.workflow.slug,
        "version": r.workflow_version.version,
        "environment": r.environment.kind,
        "status": r.status,
        "reason": r.reason,
        "created_at": r.created_at.isoformat(),
        "expires_at": r.expires_at.isoformat(),
        "decided_by": getattr(r.decided_by, "username", None),
        "comment": r.comment,
        "run_id": str(r.run_id) if r.run_id else None,
    }


def _requests(ws, user):
    qs = RunRequest.objects.filter(workspace=ws, requested_by=user).select_related(
        "workflow_version__workflow", "environment", "decided_by"
    )
    return access.visible(qs, user, ws, "workflow_version__workflow__folder")


def list_run_requests(request, ws, args):
    run_requests.expire_stale(ws)
    rows = [_request_row(r) for r in _requests(ws, request.user)[: _limit(args)]]
    return _ok({"count": len(rows), "results": rows})


def get_run_request(request, ws, args):
    run_requests.expire_stale(ws)
    try:
        pk = serializers.UUIDField().run_validation(args.get("request_id"))
    except serializers.ValidationError:
        raise Http404() from None
    r = _requests(ws, request.user).filter(pk=pk).first()
    if r is None:
        raise Http404()
    row = _request_row(r)
    if r.status == RunRequest.Status.LAUNCHED:
        row["next"] = "Lancée : suivez-la avec get_run(run_id)."
    elif r.status == RunRequest.Status.PENDING:
        row["next"] = "En attente de la confirmation d'une personne dans l'application ; ne relancez pas la demande."
    return _ok(row)


def request_run(request, ws, args):
    """Dépose une demande de lancement : une personne la confirme, sauf si l'espace a activé la validation automatique (dev et recette)."""
    wf = access.visible(Workflow.objects.filter(workspace=ws, slug=str(args.get("slug", ""))), request.user, ws).first()
    if wf is None:
        raise Http404()
    req = run_requests.create(
        workspace=ws,
        user=request.user,
        workflow=wf,
        version_name=args.get("version") or None,
        environment=str(args.get("environment", "")),
        inputs=args.get("inputs") or {},
        reason=args.get("reason", ""),
        client=str(request.META.get("HTTP_USER_AGENT", ""))[:120],
    )
    record(
        "run_request.create",
        actor=request.user,
        workspace=ws,
        target=req,
        workflow=wf.slug,
        environment=req.environment.kind,
        external_assistant=True,
    )
    req, waiting = run_requests.auto_launch(
        ws, req
    )  # vide si l'espace a activé la validation automatique et que la demande est partie
    if not waiting:
        return _ok(
            _request_row(req)
            | {
                "automatic": True,
                "next": "Lancé automatiquement (validation automatique activée dans cet espace). Suivez avec get_run(run_id).",
            }
        )
    return _ok(
        _request_row(req)
        | {
            "next": f"{waiting} Rien n'est lancé tant qu'une personne n'a pas confirmé dans l'application (Approbations › Lancements demandés par un assistant). "
            "Appelez get_run_request(request_id) pour savoir si elle a confirmé, puis get_run(run_id) pour suivre.",
        }
    )


def read_tools() -> dict:
    ids = {"run_id": {"type": "string", "description": "Identifiant d'une exécution (list_runs)."}}
    return {
        "list_runs": (
            _spec(
                "list_runs",
                "Lister les exécutions",
                "Exécutions récentes (les plus récentes d'abord), tous environnements, avec leur état et leur erreur. Filtres : workflow (slug), status "
                f"({', '.join(RUN_STATUS)}), environment (dev, test, prod).",
                {
                    "workflow": SLUG,
                    "status": {"type": "string", "enum": RUN_STATUS},
                    "environment": {"type": "string", "enum": ["dev", "test", "prod"]},
                }
                | LIMIT,
            ),
            list_runs,
        ),
        "get_run": (
            _spec(
                "get_run",
                "Suivre une exécution",
                "État d'une exécution : entrées, sorties, erreur, et pour chaque étape son état, sa sortie, son erreur et la fin de ses journaux, puis les derniers événements. "
                "Rappelez tant que `finished` est faux. `step` (identifiant d'étape) renvoie la sortie et les journaux complets de cette étape.",
                ids
                | {"step": {"type": "string", "description": "Identifiant d'une étape : sortie et journaux complets."}},
                ["run_id"],
            ),
            get_run,
        ),
        "list_run_requests": (
            _spec(
                "list_run_requests",
                "Lister mes demandes de lancement",
                "Les demandes de lancement déposées avec ce jeton : en attente, lancées, refusées ou expirées.",
                LIMIT,
            ),
            list_run_requests,
        ),
        "get_run_request": (
            _spec(
                "get_run_request",
                "Lire une demande de lancement",
                "État d'une demande déposée par request_run ; une fois confirmée, donne le run_id à suivre avec get_run.",
                {"request_id": {"type": "string"}},
                ["request_id"],
            ),
            get_run_request,
        ),
    }


def write_tools() -> dict:
    spec = _spec(
        "request_run",
        "Demander le lancement d'un workflow (confirmation humaine, sauf validation automatique)",
        "DEMANDE le lancement de la dernière version publiée d'un workflow en développement (dev) ou en recette (test) ; la production est refusée. "
        "Vous ne lancez pas vous-même : une personne connectée examine la demande (workflow, version, environnement, entrées, votre motif) et la confirme ou la refuse dans l'application, "
        "sinon elle expire ; SAUF si un administrateur a activé la validation automatique de l'espace : la réponse indique alors `automatic: true` et un run_id à suivre. Donnez des entrées réalistes et un motif clair. Puis suivez avec get_run_request et get_run. Une seule demande par besoin : ne la répétez pas.",
        {
            "slug": SLUG,
            "environment": {"type": "string", "enum": list(run_requests.ASSISTANT_ENVIRONMENTS)},
            "inputs": {
                "type": "object",
                "description": "Entrées du workflow (codes du formulaire / schéma d'entrées).",
            },
            "version": {"type": "string", "description": "Version publiée précise (défaut : la dernière)."},
            "reason": {
                "type": "string",
                "maxLength": 300,
                "description": "Pourquoi ce lancement, en une phrase : montré à la personne.",
            },
        },
        ["slug", "environment", "reason"],
    )
    spec["annotations"] = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    return {"request_run": (spec, request_run)}
