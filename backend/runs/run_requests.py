"""Demandes de lancement d'un assistant externe (ADR 0025, amendement du 2026-11-26).

Règle de fond : l'assistant NE LANCE JAMAIS. Il dépose une demande ; une PERSONNE connectée la confirme dans l'interface, et le run est alors
créé au nom de cette personne, avec ses droits et la gouvernance de l'espace. Sans confirmation, rien ne s'exécute, et la demande expire.

Garde-fous tenus ici, côté serveur :
- environnements `dev` et `test` (recette) seulement, à la demande ET à la confirmation ; la production n'est jamais atteignable par ce chemin ;
- la personne dont le jeton dépose la demande doit avoir, par son rôle, le droit de lancer ce workflow (dossiers privés compris) ;
- la version et les entrées montrées à la personne sont celles qui sont lancées (figées dans la demande, relues au moment de confirmer) ;
- confirmer ou refuser exige une session humaine : un jeton, donc un assistant, ne le peut pas ; chaque étape est auditée ;
- au plus `MAX_PENDING` demandes en attente par personne ; une demande expire après `ORCH_RUN_REQUEST_TTL_MINUTES` (15 par défaut)."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from audit.models import record
from tenancy.models import Environment, Membership
from tenancy.permissions import ROLE_PERMISSIONS, RUN, has_perm
from workflows import access

from . import engine
from .models import RunRequest
from .serializers import validate_inputs_against, with_defaults

ASSISTANT_ENVIRONMENTS = (
    "dev",
    "test",
)  # jamais « prod » : une mise en production passe par la promotion, faite par une personne
MAX_PENDING = 5
MAX_INPUT_BYTES = 64 * 1024


def ttl() -> timedelta:
    return timedelta(minutes=int(getattr(settings, "ORCH_RUN_REQUEST_TTL_MINUTES", 15)))


def expire_stale(workspace) -> None:
    """Les demandes dont le délai est passé deviennent « expirées » (jamais lancées après coup)."""
    RunRequest.objects.filter(
        workspace=workspace, status=RunRequest.Status.PENDING, expires_at__lte=timezone.now()
    ).update(status=RunRequest.Status.EXPIRED)


def _role_allows_run(user, workspace) -> bool:
    """Le droit de lancer tient au RÔLE de la personne, pas aux portées du jeton (un jeton de conception n'a jamais « run »)."""
    m = Membership.objects.filter(user=user, workspace=workspace).first()
    return m is not None and workspace.archived_at is None and RUN in ROLE_PERMISSIONS[m.role]


def create(*, workspace, user, workflow, version_name, environment, inputs, reason, client) -> RunRequest:
    if environment not in ASSISTANT_ENVIRONMENTS:
        raise serializers.ValidationError(
            {
                "environment": "Un assistant ne peut demander un lancement qu'en développement (dev) ou en recette (test)."
            }
        )
    if not _role_allows_run(user, workspace) or not access.Rules(user, workspace).allows(workflow.folder, RUN):
        raise PermissionDenied("La personne qui a créé ce jeton n'a pas le droit de lancer ce workflow.")
    version = workflow.versions.filter(version=version_name).first() if version_name else workflow.latest_version()
    if version is None:
        raise serializers.ValidationError(
            {"slug": "Ce workflow n'a pas de version publiée (ou cette version n'existe pas)."}
        )
    env = Environment.objects.filter(workspace=workspace, kind=environment).first()
    if env is None:
        raise serializers.ValidationError({"environment": "Environnement indisponible dans cet espace."})
    if not isinstance(inputs, dict):
        raise serializers.ValidationError({"inputs": "Les entrées doivent être un objet."})
    inputs = with_defaults(version.input_schema, inputs)
    problems = validate_inputs_against(version.input_schema, inputs)
    if problems:
        raise serializers.ValidationError({"inputs": problems})
    if len(str(inputs)) > MAX_INPUT_BYTES:
        raise serializers.ValidationError({"inputs": "Entrées trop volumineuses."})
    expire_stale(workspace)
    if (
        RunRequest.objects.filter(workspace=workspace, requested_by=user, status=RunRequest.Status.PENDING).count()
        >= MAX_PENDING
    ):
        raise serializers.ValidationError(
            {
                "detail": f"{MAX_PENDING} demandes sont déjà en attente de confirmation : attendez qu'une personne les traite."
            }
        )
    return RunRequest.objects.create(
        workspace=workspace,
        workflow_version=version,
        environment=env,
        inputs=inputs,
        reason=str(reason or "")[:300],
        client=str(client or "")[:120],
        requested_by=user,
        expires_at=timezone.now() + ttl(),
    )


def _locked(workspace, pk) -> RunRequest:
    req = (
        RunRequest.objects.select_for_update()
        .select_related("workflow_version__workflow", "environment")
        .filter(workspace=workspace, pk=pk)
        .first()
    )
    if req is None:
        from django.http import Http404

        raise Http404()
    return req


def confirm(workspace, pk, user, comment="") -> RunRequest:
    """La personne connectée confirme : le run est créé en son nom. Rejoue sans effet si la demande a déjà été confirmée."""
    expire_stale(workspace)  # hors transaction : l'expiration est enregistrée même si la confirmation est refusée
    return _confirm(workspace, pk, user, comment)


@transaction.atomic
def _confirm(workspace, pk, user, comment) -> RunRequest:
    req = _locked(workspace, pk)
    wf = req.workflow_version.workflow
    access.require(user, workspace, wf.folder, RUN)  # 404 si le dossier est caché, 403 si le niveau est insuffisant
    if req.status == RunRequest.Status.LAUNCHED:
        return req
    if req.status != RunRequest.Status.PENDING:
        raise serializers.ValidationError(
            {"detail": f"Cette demande n'est plus en attente ({req.get_status_display().lower()})."}
        )
    if (
        req.environment.kind not in ASSISTANT_ENVIRONMENTS
    ):  # défense en profondeur : une ligne modifiée à la main ne mène pas à la production
        raise PermissionDenied(
            "Un lancement demandé par un assistant n'est possible qu'en développement ou en recette."
        )
    if not has_perm(user, workspace, RUN):
        raise PermissionDenied("Vous n'avez pas le droit de lancer des workflows dans cet espace.")
    run, _ = engine.start_run(
        workspace=workspace,
        environment=req.environment,
        workflow_version=req.workflow_version,
        user=user,
        inputs=req.inputs,
        idempotency_key=f"run-request-{req.pk}",
        trigger="assistant-confirmed",
    )
    req.status = RunRequest.Status.LAUNCHED
    req.decided_by, req.decided_at, req.run = user, timezone.now(), run
    req.comment = str(comment or "")[:300]
    req.save(update_fields=["status", "decided_by", "decided_at", "run", "comment"])
    record(
        "run_request.confirm",
        actor=user,
        workspace=workspace,
        target=req,
        run=str(run.pk),
        environment=req.environment.kind,
        workflow=wf.slug,
        requested_by=getattr(req.requested_by, "username", ""),
        external_assistant=True,
    )
    return req


@transaction.atomic
def refuse(workspace, pk, user, comment="") -> RunRequest:
    expire_stale(workspace)
    req = _locked(workspace, pk)
    access.require(user, workspace, req.workflow_version.workflow.folder, RUN)
    if req.status != RunRequest.Status.PENDING:
        raise serializers.ValidationError(
            {"detail": f"Cette demande n'est plus en attente ({req.get_status_display().lower()})."}
        )
    req.status = RunRequest.Status.REFUSED
    req.decided_by, req.decided_at, req.comment = user, timezone.now(), str(comment or "")[:300]
    req.save(update_fields=["status", "decided_by", "decided_at", "comment"])
    record(
        "run_request.refuse",
        actor=user,
        workspace=workspace,
        target=req,
        workflow=req.workflow_version.workflow.slug,
        external_assistant=True,
    )
    return req
