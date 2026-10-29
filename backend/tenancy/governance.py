"""Governance: versioned policies, promotion to production with review, separation of duties.

`authorize_run` is the single choke point used by the API, schedules and webhooks (via engine.start_run).
The decision applied is stored in the run definition so it can be audited later.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import record

from .models import DEFAULT_POLICY, Environment, PolicyVersion, Promotion
from .permissions import APPROVE, PUBLISH, has_perm


def current_policy(workspace) -> tuple[PolicyVersion | None, dict]:
    pv = workspace.policy_versions.order_by("-version").first()
    return pv, {**DEFAULT_POLICY, **(pv.rules if pv else {})}


@transaction.atomic
def publish_policy(workspace, rules: dict, user) -> PolicyVersion:
    unknown = set(rules) - set(DEFAULT_POLICY)
    if unknown:
        raise ValidationError({"rules": f"Règles inconnues : {', '.join(sorted(unknown))}"})
    if any(not isinstance(v, bool) for v in rules.values()):
        raise ValidationError({"rules": "Chaque règle est un booléen."})
    last = workspace.policy_versions.select_for_update().order_by("-version").first()
    merged = {**DEFAULT_POLICY, **(last.rules if last else {}), **rules}
    pv = PolicyVersion.objects.create(
        workspace=workspace, version=(last.version + 1) if last else 1, rules=merged, created_by=user
    )
    record("policy.publish", actor=user, workspace=workspace, target=pv, version=pv.version, rules=merged)
    return pv


def approved_promotion(workflow_version, environment) -> Promotion | None:
    return Promotion.objects.filter(
        workflow_version=workflow_version, environment=environment, status=Promotion.Status.APPROVED
    ).first()


def authorize_run(*, workspace, environment, workflow_version, user, trigger="manual") -> dict:
    """Raise PermissionDenied when the run is not allowed; return the decision to store with the run."""
    pv, rules = current_policy(workspace)
    token = getattr(user, "_token", None)
    if token is not None and token.environments and environment.kind not in token.environments:
        raise PermissionDenied("Ce jeton n'est pas autorisé pour cet environnement.")
    promotion = None
    if environment.kind == Environment.Kind.PROD and rules["prod_requires_promotion"]:
        promotion = approved_promotion(workflow_version, environment)
        if promotion is None:
            raise PermissionDenied(
                "Cette version n'a pas été promue en production. Demandez une promotion, elle sera revue avant usage."
            )
    return {
        "policy_version": pv.version if pv else 0,
        "rules": rules,
        "promotion_id": promotion.pk if promotion else None,
        "trigger": trigger,
        "decision": "allowed",
        "decided_at": timezone.now().isoformat(),
    }


@transaction.atomic
def request_promotion(workflow_version, environment, user, comment="") -> Promotion:
    workspace = workflow_version.workflow.workspace
    if not has_perm(user, workspace, PUBLISH):
        raise PermissionDenied("Vous n'avez pas le droit de demander une promotion.")
    if environment.workspace_id != workspace.id:
        raise ValidationError({"environment": "Environnement inconnu."})
    if environment.kind == Environment.Kind.DEV:
        raise ValidationError({"environment": "Aucune promotion n'est nécessaire en développement."})
    if approved_promotion(workflow_version, environment):
        raise ValidationError({"environment": "Cette version est déjà promue dans cet environnement."})
    if Promotion.objects.filter(
        workflow_version=workflow_version, environment=environment, status=Promotion.Status.REQUESTED
    ).exists():
        raise ValidationError({"environment": "Une demande est déjà en attente de revue."})
    pv, rules = current_policy(workspace)
    review_needed = environment.kind == Environment.Kind.PROD and rules["promotion_requires_review"]
    promo = Promotion.objects.create(
        workspace=workspace,
        workflow_version=workflow_version,
        environment=environment,
        requested_by=user,
        comment=comment[:500],
        policy_version=pv,
        status=Promotion.Status.REQUESTED if review_needed else Promotion.Status.APPROVED,
        decided_at=None if review_needed else timezone.now(),
    )
    record(
        "promotion.request",
        actor=user,
        workspace=workspace,
        target=promo,
        version=workflow_version.version,
        environment=environment.kind,
        auto_approved=not review_needed,
    )
    return promo


@transaction.atomic
def decide_promotion(promotion_id, user, approve: bool, comment="") -> Promotion:
    promo = Promotion.objects.select_for_update().select_related("workflow_version__workflow").get(pk=promotion_id)
    workspace = promo.workspace
    if not has_perm(user, workspace, APPROVE):
        raise PermissionDenied("Vous n'avez pas le droit de réviser les promotions.")
    if promo.status != Promotion.Status.REQUESTED:
        raise ValidationError({"status": "Cette demande a déjà été traitée."})
    _, rules = current_policy(workspace)
    if rules["forbid_self_review"] and promo.requested_by_id == user.id:
        raise PermissionDenied("Séparation des rôles : vous ne pouvez pas réviser votre propre demande.")
    promo.status = Promotion.Status.APPROVED if approve else Promotion.Status.REJECTED
    promo.reviewed_by, promo.comment, promo.decided_at = user, comment[:500] or promo.comment, timezone.now()
    promo.save()
    record("promotion.decide", actor=user, workspace=workspace, target=promo, approved=approve)
    return promo
