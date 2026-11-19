"""Règles de l'API store : souscription, clés, point d'application unique des appels (clé, souscription, plan, quota, environnement).

Toute décision d'accès passe par `invoke` : l'interface et l'API d'administration n'en refont aucune.
Les workflows s'exécutent sous l'identité `run_as` de la souscription, via `runs.engine.start_run` : la gouvernance habituelle s'applique.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from audit.models import record
from runs import engine
from runs.models import WorkflowRun
from runs.serializers import validate_inputs_against, with_defaults
from tenancy.auth import hash_token
from tenancy.models import Environment, ServiceAccount
from workflows.models import Workflow

from .models import ApiKey, Plan, Product, ProductWorkflow, Subscription, UsageEvent

KEY_PREFIX = "fxs_"
MAX_KEY_DAYS = 365


class Refused(Exception):
    """Appel ou action refusés ; `status` est le code HTTP à renvoyer, `outcome` l'étiquette conservée dans l'usage."""

    def __init__(self, status: int, outcome: str, message: str):
        super().__init__(message)
        self.status, self.outcome, self.message = status, outcome, message


def new_key() -> tuple[str, str, str]:
    import secrets

    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:12], hash_token(raw)


def audit(event: str, user, ws, target, **data) -> None:
    record(event, actor=user, workspace=ws, target=target, **data)


@transaction.atomic
def update_product(product: Product, user, data: dict) -> Product:
    """Applique une modification de produit validée par le sérialiseur ; la publication re-contrôle les prérequis."""
    ws = product.workspace
    if "run_as" in data:
        account = ServiceAccount.objects.filter(workspace=ws, name=data["run_as"]).first()
        if account is None:
            raise Refused(400, "invalid", "Compte de service inconnu dans cet espace.")
        set_product_run_as(product, account)
    if "workflows" in data:
        found = list(Workflow.objects.filter(workspace=ws, slug__in=data["workflows"]))
        if len(found) != len(set(data["workflows"])):
            raise Refused(400, "invalid", "Un workflow indiqué n'existe pas dans cet espace.")
        product.links.exclude(workflow__in=found).delete()
        for wf in found:
            ProductWorkflow.objects.get_or_create(product=product, workflow=wf)
    for field in ("name", "description"):
        if field in data:
            setattr(product, field, data[field])
    if data.get("status") == Product.Status.PUBLISHED and product.status != Product.Status.PUBLISHED:
        check_publishable(product)
    if "status" in data:
        product.status = data["status"]
    product.save()
    audit("apistore.product_updated", user, ws, product, fields=sorted(data))
    return product


def set_product_run_as(product: Product, account: ServiceAccount) -> None:
    if account.workspace_id != product.workspace_id:
        raise Refused(409, "invalid", "Le compte de service doit appartenir au même espace.")
    product.run_as = account


def check_publishable(product: Product) -> None:
    """Un produit ne se publie qu'avec une identité d'exécution, au moins un workflow publié et au moins un plan."""
    if product.run_as_id is None:
        raise Refused(409, "invalid", "Choisissez le compte de service sous lequel les workflows s'exécutent.")
    if not product.plans.exists():
        raise Refused(409, "invalid", "Ajoutez au moins un plan.")
    unpublished = [
        link.workflow.slug
        for link in product.links.select_related("workflow")
        if link.workflow.latest_version() is None
    ]
    if not product.links.exists() or unpublished:
        raise Refused(
            409, "invalid", "Chaque workflow du produit doit avoir une version publiée (au moins un workflow)."
        )


def validate_plan_environments(environments) -> list[str]:
    allowed = {k for k, _ in Environment.Kind.choices}
    if not isinstance(environments, list) or not environments or not set(environments) <= allowed:
        raise ValueError("Au moins un environnement connu est requis.")
    return sorted(set(environments))


@transaction.atomic
def subscribe(product: Product, plan: Plan, *, consumer: str, user) -> Subscription:
    if product.status != Product.Status.PUBLISHED or plan.product_id != product.pk:
        raise Refused(409, "unavailable", "Ce produit n'accepte pas de souscription.")
    auto = plan.approval == Plan.Approval.AUTO
    sub = Subscription.objects.create(
        product=product,
        plan=plan,
        consumer=consumer.strip()[:120],
        requested_by=user,
        run_as=product.run_as,
        status=Subscription.Status.ACTIVE if auto else Subscription.Status.PENDING,
        decided_at=timezone.now() if auto else None,
    )
    record("apistore.subscribe", actor=user, workspace=product.workspace, target=sub, plan=plan.slug, auto=auto)
    return sub


@transaction.atomic
def decide(sub: Subscription, user, *, approve: bool) -> Subscription:
    """Approuver ou refuser une demande en attente. Le demandeur ne peut pas décider de sa propre demande."""
    if sub.status != Subscription.Status.PENDING:
        raise Refused(409, "invalid", "Cette demande a déjà été traitée.")
    if sub.requested_by_id is not None and sub.requested_by_id == user.pk:
        raise Refused(403, "forbidden", "Vous ne pouvez pas décider de votre propre demande.")
    sub.status = Subscription.Status.ACTIVE if approve else Subscription.Status.REJECTED
    sub.decided_by, sub.decided_at = user, timezone.now()
    sub.save(update_fields=["status", "decided_by", "decided_at"])
    record(
        "apistore.approve" if approve else "apistore.reject",
        actor=user,
        workspace=sub.product.workspace,
        target=sub,
    )
    return sub


@transaction.atomic
def set_status(sub: Subscription, user, status: str) -> Subscription:
    """Suspendre, réactiver ou révoquer. Révoquer révoque ses clés pour de bon : réactiver une souscription révoquée (archivée)
    ne rend aucune ancienne clé, il faut en émettre une nouvelle."""
    allowed = {
        Subscription.Status.ACTIVE: {Subscription.Status.SUSPENDED, Subscription.Status.REVOKED},
        Subscription.Status.SUSPENDED: {Subscription.Status.ACTIVE, Subscription.Status.REVOKED},
        Subscription.Status.REVOKED: {Subscription.Status.ACTIVE},
    }
    if status not in allowed.get(sub.status, set()):
        raise Refused(409, "invalid", "Changement d'état impossible depuis l'état actuel.")
    sub.status = status
    sub.save(update_fields=["status"])
    if status == Subscription.Status.REVOKED:
        sub.keys.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    record(f"apistore.{status}", actor=user, workspace=sub.product.workspace, target=sub)
    return sub


@transaction.atomic
def delete_subscription(sub: Subscription, user) -> None:
    """Supprime une souscription archivée (révoquée ou refusée) avec ses clés. L'historique d'usage reste (lien détaché) et la suppression est auditée."""
    if sub.status not in (Subscription.Status.REVOKED, Subscription.Status.REJECTED):
        raise Refused(409, "invalid", "Seule une souscription archivée (révoquée ou refusée) peut être supprimée.")
    ws, info = sub.product.workspace, {"product": sub.product.slug, "plan": sub.plan.slug, "consumer": sub.consumer}
    record("apistore.deleted", actor=user, workspace=ws, target=sub, **info)
    sub.delete()


@transaction.atomic
def issue_key(sub: Subscription, user, *, days: int | None = 90) -> tuple[ApiKey, str]:
    """Crée une clé ; la valeur en clair n'est renvoyée qu'ici. `days=None` : sans expiration (l'appelant doit avoir vérifié le droit)."""
    if sub.status != Subscription.Status.ACTIVE:
        raise Refused(409, "invalid", "Seule une souscription active reçoit une clé.")
    if days is not None and not 1 <= days <= MAX_KEY_DAYS:
        raise Refused(400, "invalid", f"La durée de vie d'une clé est de 1 à {MAX_KEY_DAYS} jours.")
    raw, prefix, digest = new_key()
    key = ApiKey.objects.create(
        subscription=sub,
        prefix=prefix,
        key_hash=digest,
        expires_at=None if days is None else timezone.now() + timedelta(days=days),
    )
    record(
        "apistore.key_issued",
        actor=user,
        workspace=sub.product.workspace,
        target=key,
        expires_in_days=days if days is not None else "illimitée",
    )
    return key, raw


def revoke_key(key: ApiKey, user) -> None:
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])
        record("apistore.key_revoked", actor=user, workspace=key.subscription.product.workspace, target=key)


def authenticate(raw: str) -> ApiKey:
    """La clé doit exister, ne pas être expirée ni révoquée, et sa souscription doit être active."""
    key = None
    if raw.startswith(KEY_PREFIX):
        key = (
            ApiKey.objects.select_related(
                "subscription__product__workspace", "subscription__plan", "subscription__run_as__user"
            )
            .filter(key_hash=hash_token(raw))
            .first()
        )
    now = timezone.now()
    if key is None or key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
        raise Refused(401, "invalid_key", "Clé invalide, expirée ou révoquée.")
    if key.subscription.status != Subscription.Status.ACTIVE or not key.subscription.run_as.user.is_active:
        raise Refused(403, "inactive", "Souscription inactive.")
    return key


def _check_quota(sub: Subscription) -> None:
    """Compte les appels acceptés ; verrouille la souscription pour que deux appels simultanés ne dépassent pas le plan."""
    now = timezone.now()
    accepted = UsageEvent.objects.filter(subscription=sub, outcome="accepted")
    plan = sub.plan
    if (
        plan.per_minute is not None
        and accepted.filter(created_at__gt=now - timedelta(minutes=1)).count() >= plan.per_minute
    ):
        raise Refused(429, "rate_limited", "Quota par minute atteint.")
    if plan.per_day is not None and accepted.filter(created_at__gt=now - timedelta(days=1)).count() >= plan.per_day:
        raise Refused(429, "rate_limited", "Quota journalier atteint.")


@dataclass
class Call:
    key: ApiKey
    workflow_slug: str
    environment: str
    inputs: dict
    idempotency_key: str | None = None


def _log(call_sub, product, slug, env, outcome, run=None) -> None:
    UsageEvent.objects.create(
        subscription=call_sub,
        product=product,
        workflow_slug=slug[:80],
        environment=env[:10],
        outcome=outcome,
        run_id=run.id if run else None,
    )


def invoke(call: Call):
    """Point d'application unique. Renvoie `(run, created)` ou lève `Refused` ; chaque issue est consignée sans valeur d'entrée.

    Le refus est consigné APRÈS l'annulation de la transaction de l'appel, sinon sa trace serait annulée avec elle."""
    sub = call.key.subscription
    try:
        with transaction.atomic():
            locked = (
                Subscription.objects.select_for_update()
                .select_related("product__workspace", "plan", "run_as__user")
                .get(pk=sub.pk)
            )
            return _invoke_locked(call, locked, locked.product, locked.plan)
    except Refused as exc:
        _log(sub, sub.product, call.workflow_slug, call.environment, exc.outcome)
        record(
            "apistore.call_refused",
            workspace=sub.product.workspace,
            target=sub,
            outcome=exc.outcome,
            workflow=call.workflow_slug,
        )
        raise


def _invoke_locked(call: Call, sub: Subscription, product: Product, plan: Plan):
    if sub.status != Subscription.Status.ACTIVE or product.status != Product.Status.PUBLISHED:
        raise Refused(403, "inactive", "Souscription ou produit inactif.")
    link = product.links.select_related("workflow").filter(workflow__slug=call.workflow_slug).first()
    if link is None:
        raise Refused(404, "forbidden", "Ce workflow n'appartient pas à ce produit.")
    if call.environment not in plan.environments:
        raise Refused(403, "forbidden", "Environnement non autorisé par le plan.")
    env = Environment.objects.filter(workspace=product.workspace, kind=call.environment).first()
    version = link.workflow.latest_version()
    if env is None or version is None:
        raise Refused(409, "unavailable", "Environnement ou version publiée indisponible.")
    inputs = with_defaults(version.input_schema, call.inputs)
    problems = validate_inputs_against(version.input_schema, inputs)
    if problems:
        raise Refused(400, "invalid", "Entrées invalides : " + "; ".join(map(str, problems))[:300])
    # Clé d'idempotence propre à la souscription (deux consommateurs ne partagent jamais un rejeu) ; un rejeu ne consomme pas de quota.
    idem = f"apistore:{sub.pk}:{call.idempotency_key[:100]}" if call.idempotency_key else None
    if idem:
        replay = WorkflowRun.objects.filter(workspace=product.workspace, idempotency_key=idem).first()
        if replay is not None:
            return replay, False
    _check_quota(sub)
    from rest_framework.exceptions import PermissionDenied

    try:
        run, created = engine.start_run(
            workspace=product.workspace,
            environment=env,
            workflow_version=version,
            user=sub.run_as.user,
            inputs=inputs,
            idempotency_key=idem,
            trigger="apistore",
        )
    except PermissionDenied as exc:
        raise Refused(403, "governance", str(exc.detail)) from None
    if created:
        _log(sub, product, call.workflow_slug, call.environment, "accepted", run)
    ApiKey.objects.filter(pk=call.key.pk).update(last_used_at=timezone.now())
    return run, created
