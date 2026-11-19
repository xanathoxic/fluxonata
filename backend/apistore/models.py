"""Modèle de l'API store : produits (groupes de workflows), plans (quotas), souscriptions, clés hachées, usage.

Aucune valeur d'entrée ni de sortie d'un workflow n'est conservée ici : l'usage ne garde que des métadonnées.
"""

from django.conf import settings
from django.db import models


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        PUBLISHED = "published", "Publié"
        RETIRED = "retired", "Retiré"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="+")
    slug = models.SlugField(max_length=60)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    workflows = models.ManyToManyField("workflows.Workflow", through="ProductWorkflow", related_name="+")
    # Identité d'exécution, choisie par un administrateur : les souscripteurs ne la choisissent jamais (sinon, élévation de droits).
    run_as = models.ForeignKey(
        "tenancy.ServiceAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_apistore_product")]
        ordering = ["name"]


class ProductWorkflow(models.Model):
    """Un workflow exposé par un produit ; seul ce lien l'ouvre aux souscripteurs."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="links")
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "workflow"], name="uniq_apistore_product_workflow")]


class Plan(models.Model):
    class Approval(models.TextChoices):
        AUTO = "auto", "Automatique"
        MANUAL = "manual", "Par une personne"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="plans")
    slug = models.SlugField(max_length=60)
    name = models.CharField(max_length=120)
    per_minute = models.PositiveIntegerField(null=True, blank=True)  # null : illimité
    per_day = models.PositiveIntegerField(null=True, blank=True)
    approval = models.CharField(max_length=6, choices=Approval.choices, default=Approval.MANUAL)
    environments = models.JSONField(
        default=list
    )  # genres d'environnement autorisés ; jamais vide (contrôlé à l'écriture)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "slug"], name="uniq_apistore_plan")]
        ordering = ["name"]


class Subscription(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspendue"
        REVOKED = "revoked", "Révoquée"
        REJECTED = "rejected", "Refusée"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    consumer = models.CharField(max_length=120)  # équipe ou application qui consomme
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    # Identité sous laquelle les workflows s'exécutent : ses droits et la gouvernance s'appliquent comme pour tout appel.
    run_as = models.ForeignKey("tenancy.ServiceAccount", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ApiKey(models.Model):
    """Clé de souscription. Seul son hachage SHA-256 est conservé (la valeur n'est montrée qu'une fois)."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="keys")
    prefix = models.CharField(max_length=12)
    key_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(
        null=True, blank=True
    )  # vide = clé sans expiration (réservée aux administrateurs)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class UsageEvent(models.Model):
    """Un appel, accepté ou refusé. Métadonnées seulement : jamais d'entrées, de sorties ni de clé."""

    subscription = models.ForeignKey(Subscription, null=True, on_delete=models.SET_NULL, related_name="usage")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL, related_name="+")
    workflow_slug = models.CharField(max_length=80, blank=True)
    environment = models.CharField(max_length=10, blank=True)
    outcome = models.CharField(max_length=20)  # accepted | rate_limited | forbidden | invalid | ...
    run_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
