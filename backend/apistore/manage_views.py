"""API de gestion de l'API store (session ou jeton d'API ; droits vérifiés côté serveur par `get_workspace`).

- ADMINISTER : produits, plans, identité d'exécution, publication, suspension/révocation, usage de tout l'espace.
- APPROVE : décider d'une demande en attente.  RUN : demander une souscription, émettre/révoquer les clés des siennes.
- VIEW : voir les produits publiés et ses propres souscriptions.
"""

from django.db.models import Count
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from tenancy.models import ServiceAccount
from tenancy.permissions import ADMINISTER, APPROVE, RUN, VIEW, get_workspace, has_perm, require_human_session
from workflows.models import Workflow

from . import docs, service
from .models import ApiKey, Plan, Product, Subscription, UsageEvent


def problem(status: int, code: str, message: str) -> Response:
    """Réponse d'erreur au format de la plate-forme (`error.message`, lu par l'interface) ; `detail` et `code` restent pour les clients existants."""
    return Response(
        {"error": {"code": code, "message": message, "details": None}, "detail": message, "code": code}, status=status
    )


def refuse(exc: service.Refused) -> Response:
    return problem(exc.status, exc.outcome, exc.message)


class ProductIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=60)
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=500, allow_blank=True, default="")


class ProductPatch(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    description = serializers.CharField(max_length=500, allow_blank=True, required=False)
    status = serializers.ChoiceField(choices=[c for c, _ in Product.Status.choices], required=False)
    run_as = serializers.CharField(max_length=80, required=False)  # nom du compte de service
    workflows = serializers.ListField(child=serializers.SlugField(), required=False, max_length=50)


class PlanIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=60)
    name = serializers.CharField(max_length=120)
    per_minute = serializers.IntegerField(min_value=1, allow_null=True, default=None)
    per_day = serializers.IntegerField(min_value=1, allow_null=True, default=None)
    approval = serializers.ChoiceField(choices=[c for c, _ in Plan.Approval.choices], default="manual")
    environments = serializers.ListField(child=serializers.CharField(), min_length=1)

    def validate_environments(self, value):
        try:
            return service.validate_plan_environments(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None


def product_dict(p: Product, *, admin: bool) -> dict:
    out = {
        "slug": p.slug,
        "name": p.name,
        "description": p.description,
        "status": p.status,
        "workflows": sorted(link.workflow.slug for link in p.links.all()),
        "workflow_names": {link.workflow.slug: link.workflow.name for link in p.links.all()},
        "plans": [
            {
                "slug": pl.slug,
                "name": pl.name,
                "per_minute": pl.per_minute,
                "per_day": pl.per_day,
                "approval": pl.approval,
                "environments": pl.environments,
            }
            for pl in p.plans.all()
        ],
    }
    if admin:
        out["run_as"] = p.run_as.name if p.run_as_id else None
    return out


def sub_dict(s: Subscription) -> dict:
    return {
        "id": s.pk,
        "product": s.product.slug,
        "plan": s.plan.slug,
        "consumer": s.consumer,
        "status": s.status,
        "requested_by": s.requested_by.username if s.requested_by_id else None,
        "decided_at": s.decided_at,
        "created_at": s.created_at,
        "keys": [
            {"id": k.pk, "prefix": k.prefix, "expires_at": k.expires_at, "revoked": k.revoked_at is not None}
            for k in s.keys.all()
        ],
    }


def _product_or_404(ws, slug) -> Product:
    p = Product.objects.filter(workspace=ws, slug=slug).select_related("run_as").first()
    if p is None:
        raise Http404()
    return p


@extend_schema(exclude=True)
class MeView(APIView):
    """Ce que la personne peut faire dans l'espace : l'interface s'en sert pour n'afficher que les sections utiles (le serveur décide quand même)."""

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        return Response(
            {
                "administer": has_perm(request.user, ws, ADMINISTER),
                "approve": has_perm(request.user, ws, APPROVE),
                "run": has_perm(request.user, ws, RUN),
            }
        )


@extend_schema(exclude=True)
class OptionsView(APIView):
    """Choix proposés à l'administrateur : workflows de l'espace (publiés ou non) et comptes de service."""

    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        workflows = (
            Workflow.objects.filter(workspace=ws, deprecated=False).prefetch_related("versions").order_by("slug")
        )
        return Response(
            {
                "workflows": [
                    {"slug": w.slug, "name": w.name, "published": w.latest_version() is not None} for w in workflows
                ],
                "accounts": list(
                    ServiceAccount.objects.filter(workspace=ws).order_by("name").values_list("name", flat=True)
                ),
            }
        )


@extend_schema(exclude=True)
class ProductsView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        admin = has_perm(request.user, ws, ADMINISTER)
        qs = Product.objects.filter(workspace=ws).prefetch_related("links__workflow", "plans")
        if not admin:
            qs = qs.filter(status=Product.Status.PUBLISHED)
        return Response([product_dict(p, admin=admin) for p in qs.select_related("run_as")])

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        body = ProductIn(data=request.data)
        body.is_valid(raise_exception=True)
        if Product.objects.filter(workspace=ws, slug=body.validated_data["slug"]).exists():
            return problem(409, "invalid", "Ce produit existe déjà.")
        p = Product.objects.create(workspace=ws, created_by=request.user, **body.validated_data)
        service.audit("apistore.product_created", request.user, ws, p)
        return Response(product_dict(p, admin=True), status=201)


@extend_schema(exclude=True)
class ProductDetailView(APIView):
    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        admin = has_perm(request.user, ws, ADMINISTER)
        p = _product_or_404(ws, slug)
        if p.status != Product.Status.PUBLISHED and not admin:
            raise Http404()
        return Response(product_dict(p, admin=admin))

    def patch(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        p = _product_or_404(ws, slug)
        body = ProductPatch(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        try:
            service.update_product(p, request.user, body.validated_data)
        except service.Refused as exc:
            return refuse(exc)
        return Response(product_dict(_product_or_404(ws, slug), admin=True))


def _documented_product(request, workspace, slug) -> Product:
    """Produit à documenter : publié pour tout membre, brouillon seulement pour qui administre (aperçu)."""
    ws = get_workspace(request.user, workspace, VIEW)
    p = (
        Product.objects.filter(workspace=ws, slug=slug)
        .select_related("workspace")
        .prefetch_related("links__workflow__versions", "plans")
        .first()
    )
    if p is None or (p.status != Product.Status.PUBLISHED and not has_perm(request.user, ws, ADMINISTER)):
        raise Http404()
    return p


@extend_schema(exclude=True)
class ProductDocsView(APIView):
    """Documentation générée du produit (endpoints, schémas d'entrée, exemples) : calculée depuis l'état réel, jamais stockée."""

    def get(self, request, workspace, slug):
        return Response(docs.build(_documented_product(request, workspace, slug)))


@extend_schema(exclude=True)
class ProductOpenApiView(APIView):
    """Même documentation au format OpenAPI 3.1, pour l'importer dans un outil client."""

    def get(self, request, workspace, slug):
        return Response(docs.openapi(docs.build(_documented_product(request, workspace, slug))))


@extend_schema(exclude=True)
class PlansView(APIView):
    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, ADMINISTER)
        p = _product_or_404(ws, slug)
        body = PlanIn(data=request.data)
        body.is_valid(raise_exception=True)
        if p.plans.filter(slug=body.validated_data["slug"]).exists():
            return problem(409, "invalid", "Ce plan existe déjà.")
        Plan.objects.create(product=p, **body.validated_data)
        service.audit("apistore.plan_created", request.user, ws, p, plan=body.validated_data["slug"])
        return Response(product_dict(_product_or_404(ws, slug), admin=True), status=201)


class SubscriptionIn(serializers.Serializer):
    product = serializers.SlugField()
    plan = serializers.SlugField()
    consumer = serializers.CharField(max_length=120)


@extend_schema(exclude=True)
class SubscriptionsView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        qs = Subscription.objects.filter(product__workspace=ws).select_related("product", "plan", "requested_by")
        if not has_perm(request.user, ws, ADMINISTER) and not has_perm(request.user, ws, APPROVE):
            qs = qs.filter(requested_by=request.user)
        return Response([sub_dict(s) for s in qs.prefetch_related("keys").order_by("-created_at")[:200]])

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, RUN)
        body = SubscriptionIn(data=request.data)
        body.is_valid(raise_exception=True)
        d = body.validated_data
        product = Product.objects.filter(workspace=ws, slug=d["product"], status=Product.Status.PUBLISHED).first()
        plan = product.plans.filter(slug=d["plan"]).first() if product else None
        if plan is None:
            raise Http404()
        try:
            sub = service.subscribe(product, plan, consumer=d["consumer"], user=request.user)
        except service.Refused as exc:
            return refuse(exc)
        return Response(sub_dict(sub), status=201)


def _sub_or_404(request, workspace, pk, perm) -> tuple:
    ws = get_workspace(request.user, workspace, perm)
    sub = Subscription.objects.filter(pk=pk, product__workspace=ws).select_related("product__workspace", "plan").first()
    if sub is None:
        raise Http404()
    return ws, sub


class DecisionIn(serializers.Serializer):
    approve = serializers.BooleanField()


class StatusIn(serializers.Serializer):
    status = serializers.ChoiceField(choices=["active", "suspended", "revoked"])


class KeyIn(serializers.Serializer):
    days = serializers.IntegerField(min_value=1, max_value=service.MAX_KEY_DAYS, default=90)
    unlimited = serializers.BooleanField(default=False)  # sans expiration : réservé aux administrateurs


@extend_schema(exclude=True)
class DecisionView(APIView):
    def post(self, request, workspace, pk):
        require_human_session(request)
        _, sub = _sub_or_404(request, workspace, pk, APPROVE)
        body = DecisionIn(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            service.decide(sub, request.user, approve=body.validated_data["approve"])
        except service.Refused as exc:
            return refuse(exc)
        return Response(sub_dict(sub))


@extend_schema(exclude=True)
class SubscriptionDetailView(APIView):
    def delete(self, request, workspace, pk):
        require_human_session(request)
        ws, sub = _sub_or_404(request, workspace, pk, ADMINISTER)
        try:
            service.delete_subscription(sub, request.user)
        except service.Refused as exc:
            return refuse(exc)
        return Response(status=204)


@extend_schema(exclude=True)
class StatusView(APIView):
    def post(self, request, workspace, pk):
        require_human_session(request)
        _, sub = _sub_or_404(request, workspace, pk, ADMINISTER)
        body = StatusIn(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            service.set_status(sub, request.user, body.validated_data["status"])
        except service.Refused as exc:
            return refuse(exc)
        return Response(sub_dict(sub))


def _own_or_admin(request, ws, sub) -> None:
    """Les clés d'une souscription sont gérées par son demandeur ou par un administrateur ; un autre membre voit un 404."""
    if sub.requested_by_id != request.user.pk and not has_perm(request.user, ws, ADMINISTER):
        raise Http404()


@extend_schema(exclude=True)
class KeysView(APIView):
    def post(self, request, workspace, pk):
        require_human_session(
            request
        )  # une clé ne s'obtient pas avec un jeton : sinon un jeton pourrait en fabriquer d'autres
        ws, sub = _sub_or_404(request, workspace, pk, RUN)
        _own_or_admin(request, ws, sub)
        body = KeyIn(data=request.data)
        body.is_valid(raise_exception=True)
        unlimited = body.validated_data["unlimited"]
        if unlimited and not has_perm(request.user, ws, ADMINISTER):
            return problem(403, "forbidden", "Une clé sans expiration ne peut être émise que par un administrateur.")
        try:
            key, raw = service.issue_key(sub, request.user, days=None if unlimited else body.validated_data["days"])
        except service.Refused as exc:
            return refuse(exc)
        return Response({"id": key.pk, "prefix": key.prefix, "expires_at": key.expires_at, "key": raw}, status=201)


@extend_schema(exclude=True)
class KeyRevokeView(APIView):
    def post(self, request, workspace, pk, key_id):
        require_human_session(request)
        ws, sub = _sub_or_404(request, workspace, pk, RUN)
        _own_or_admin(request, ws, sub)
        key = ApiKey.objects.filter(pk=key_id, subscription=sub).first()
        if key is None:
            raise Http404()
        service.revoke_key(key, request.user)
        return Response(sub_dict(sub))


@extend_schema(exclude=True)
class UsageView(APIView):
    """Résumé d'usage : par issue sur 24 h, et les derniers appels. Aucune valeur d'entrée n'existe à montrer."""

    def get(self, request, workspace, pk):
        ws, sub = _sub_or_404(request, workspace, pk, VIEW)
        _own_or_admin(request, ws, sub)
        from datetime import timedelta

        from django.utils import timezone

        events = UsageEvent.objects.filter(subscription=sub)
        since = events.filter(created_at__gt=timezone.now() - timedelta(days=1))
        return Response(
            {
                "last_24h": {r["outcome"]: r["n"] for r in since.values("outcome").annotate(n=Count("id"))},
                "recent": [
                    {
                        "at": e.created_at,
                        "workflow": e.workflow_slug,
                        "environment": e.environment,
                        "outcome": e.outcome,
                        "run_id": str(e.run_id) if e.run_id else None,
                    }
                    for e in events.order_by("-created_at")[:50]
                ],
            }
        )
