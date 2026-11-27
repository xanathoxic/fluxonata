from django.conf import settings
from django.db import models


class Organization(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class Workspace(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="workspaces")
    name = models.CharField(max_length=120)
    slug = models.SlugField()
    # Archived by a platform administrator: invisible and unreachable (404) for everyone, schedules/webhooks stop; data is kept.
    archived_at = models.DateTimeField(null=True, blank=True)
    # Shared runners (the platform's built-in workers and shared runner agents) may run this space's actions. Turn off to
    # require dedicated runners (isolation, compliance, special hardware).
    use_shared_runners = models.BooleanField(default=True)
    # Finished runs older than this many days are deleted by the worker (null = keep). Audit events are never deleted here.
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    # Validation automatique des lancements demandés par un assistant externe (dev et recette seulement) : activée par un administrateur de l'espace,
    # elle remplace la confirmation d'une personne ; qui l'a activée et quand reste affiché et audité.
    assistant_auto_run = models.BooleanField(default=False)
    assistant_auto_run_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    assistant_auto_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # URLs address a workspace by slug alone, so the slug must be unique across organizations
        constraints = [models.UniqueConstraint(fields=["slug"], name="uniq_workspace_slug_global")]

    def __str__(self):
        return self.name


class Environment(models.Model):
    class Kind(models.TextChoices):
        DEV = "dev", "Développement"
        TEST = "test", "Recette"
        PROD = "prod", "Production"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="environments")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=8, choices=Kind.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "kind"], name="uniq_env_kind")]


class Role(models.TextChoices):
    ADMIN = "admin", "Administrateur"
    SPACE_MANAGER = "space_manager", "Responsable d'espace"
    DESIGNER = "designer", "Concepteur"
    OPERATOR = "operator", "Opérateur"
    APPROVER = "approver", "Approbateur"
    VIEWER = "viewer", "Lecteur"
    CATALOG = "catalog", "Catalogue"  # launches only the catalog items its groups allow, sees only its own runs


class UserProfile(models.Model):
    """Account state that Django's User does not carry. Created on demand: a missing row means « nothing special »."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    must_change_password = models.BooleanField(
        default=False
    )  # temporary password: only the change-password route works
    sso_platform_admin = models.BooleanField(
        default=False
    )  # administrateur de plate-forme parce qu'un groupe SSO le dit (et non à la main)


class Membership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    # d'où vient ce droit : vide = donné à la main (jamais touché par un fournisseur d'identité) ; « sso:<fournisseur> » = donné par les groupes
    # de ce fournisseur, qui peut le modifier ou le retirer à la connexion suivante
    source = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "workspace"], name="uniq_membership")]


class UserGroup(models.Model):
    """A named set of people of a workspace. For the « Catalogue » role, the groups decide which catalog items a person may launch."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="user_groups")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="workspace_groups")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="uniq_group_name")]
        ordering = ["name"]


DEFAULT_POLICY = {
    "prod_requires_promotion": True,  # a workflow version must be promoted before running in production
    "promotion_requires_review": True,  # promotion to production needs a reviewer
    "forbid_self_review": True,  # reviewer must differ from the requester
    "forbid_self_approval_in_prod": True,  # human approvals in production can't be self-approved
    "require_signed_packages": False,  # only import packages signed by a trusted publisher key
}


class PolicyVersion(models.Model):
    """Versioned workspace policy. Every run records the version that governed it."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="policy_versions")
    version = models.PositiveIntegerField()
    rules = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "version"], name="uniq_policy_version")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("PolicyVersion is immutable")
        super().save(*args, **kwargs)


class Promotion(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "En attente de revue"
        APPROVED = "approved", "Approuvée"
        REJECTED = "rejected", "Refusée"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="promotions")
    workflow_version = models.ForeignKey(
        "workflows.WorkflowVersion", on_delete=models.CASCADE, related_name="promotions"
    )
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    comment = models.CharField(max_length=500, blank=True)
    policy_version = models.ForeignKey(PolicyVersion, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)


class ServiceAccount(models.Model):
    """Non-human identity (a User without a usable password) with a role in one workspace."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="service_account")
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="service_accounts")
    name = models.CharField(max_length=80)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="uniq_service_account")]


class ApiToken(models.Model):
    """Bearer token limited in scope, lifetime and environments. Only its SHA-256 hash is stored."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="api_tokens")
    service_account = models.ForeignKey(
        ServiceAccount, null=True, blank=True, on_delete=models.CASCADE, related_name="tokens"
    )
    # Jeton PERSONNEL (ADR 0025 amendement) : agit au nom de cette personne, avec ses droits ∩ les portées du jeton. Exactement un des deux.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="personal_tokens"
    )
    name = models.CharField(max_length=80)
    prefix = models.CharField(max_length=12)
    token_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    environments = models.JSONField(default=list)  # [] = every environment of the workspace
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(service_account__isnull=False, user__isnull=True)
                | models.Q(service_account__isnull=True, user__isnull=False),
                name="api_token_one_owner",
            )
        ]

    @property
    def owner_user(self):
        return self.user if self.user_id else self.service_account.user


class OidcIdentity(models.Model):
    """Link between an IdP subject and a local user. Never matched by e-mail (prevents account takeover)."""

    issuer = models.CharField(max_length=300)
    subject = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="oidc_identities")
    provider = models.ForeignKey(
        "IdentityProvider", null=True, blank=True, on_delete=models.SET_NULL, related_name="identities"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["issuer", "subject"], name="uniq_oidc_identity")]


class IdentityProvider(models.Model):
    """Un fournisseur d'identité réglé dans l'interface (OpenID Connect ou SAML 2.0). Le secret du client n'est jamais stocké en clair
    (chiffré par le coffre) ni renvoyé par l'API ; les réglages qui ne sont pas secrets sont dans `config` (voir `tenancy/sso_config.py`)."""

    class Kind(models.TextChoices):
        OIDC = "oidc", "OpenID Connect"
        SAML = "saml", "SAML 2.0"

    class Unmapped(models.TextChoices):
        ALLOW = "allow", "Connexion autorisée, sans accès"
        DENY = "deny", "Connexion refusée"

    slug = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    secret_key_id = models.CharField(max_length=64, blank=True)
    secret_token = models.TextField(blank=True)
    auto_create = models.BooleanField(default=True)  # créer le compte à la première connexion
    unmapped = models.CharField(
        max_length=8, choices=Unmapped.choices, default=Unmapped.ALLOW
    )  # aucune règle de groupe ne correspond
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class GroupRule(models.Model):
    """Si l'utilisateur est dans ce groupe du fournisseur, alors il reçoit ce droit : un rôle dans un espace (et, au choix, l'appartenance à
    un groupe d'utilisateurs de l'espace) ou l'administration de la plate-forme. Le droit le plus élevé gagne quand plusieurs règles s'appliquent."""

    class Match(models.TextChoices):
        EXACT = "exact", "Nom exact"
        GLOB = "glob", "Motif (* et ?)"

    class Grant(models.TextChoices):
        WORKSPACE = "workspace", "Rôle dans un espace"
        PLATFORM_ADMIN = "platform_admin", "Administrateur de la plate-forme"

    provider = models.ForeignKey(IdentityProvider, on_delete=models.CASCADE, related_name="rules")
    position = models.PositiveIntegerField(default=0)
    group = models.CharField(max_length=255)
    match = models.CharField(max_length=6, choices=Match.choices, default=Match.EXACT)
    grant = models.CharField(max_length=16, choices=Grant.choices, default=Grant.WORKSPACE)
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.CASCADE, related_name="sso_rules")
    role = models.CharField(max_length=20, choices=Role.choices, blank=True)
    user_group = models.CharField(
        max_length=80, blank=True
    )  # groupe d'utilisateurs de l'espace à alimenter (créé au besoin)

    class Meta:
        ordering = ["position", "id"]


class Folder(models.Model):
    """An explicitly created folder of a tree (workflows, actions, AI skills). A folder also exists implicitly as soon as an
    item uses it; this row makes an *empty* folder persist until someone deletes it."""

    class Kind(models.TextChoices):
        WORKFLOW = "workflow", "Workflows"
        ACTION = "action", "Actions"
        AI_SKILL = "ai_skill", "Skills IA"
        FORM = "form", "Formulaires"
        CONTROL = "control", "Contrôles"
        ANSIBLE_ROLE = "ansible_role", "Rôles Ansible"
        INVENTORY = "inventory", "Inventaires"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="folders")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    path = models.CharField(max_length=200)  # "A/B/C"
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "kind", "path"], name="uniq_folder")]
        ordering = ["path"]


class EgressHost(models.Model):
    """A host (name or IP address) the platform's server may call although it is internal, added by a platform administrator in the
    web interface. It adds to `ORCH_EGRESS_ALLOWLIST` (which the operator keeps in the environment and which this table never
    shows as editable). Exact match, no range, no wildcard; see `core.netsec` for what can never be allowed this way."""

    host = models.CharField(max_length=253, unique=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["host"]

    def __str__(self) -> str:
        return self.host


class Branding(models.Model):
    """Image de marque de la plate-forme (ADR 0033) : une seule ligne (pk = 1), réglée par les administrateurs de la plate-forme.
    Une valeur vide = valeur d'origine (nom Fluxonata, logo d'origine, accent d'origine). `version` augmente à chaque changement :
    elle invalide les caches des images et de la page."""

    class LoginStyle(models.TextChoices):
        PLAIN = "plain", "Sobre"
        TINT = "tint", "Fond teinté"
        GRADIENT = "gradient", "Dégradé"

    product_name = models.CharField(max_length=60, blank=True)
    tagline = models.CharField(max_length=140, blank=True)
    login_message = models.CharField(max_length=300, blank=True)
    footer_text = models.CharField(max_length=200, blank=True)
    footer_url = models.CharField(max_length=300, blank=True)
    accent = models.CharField(max_length=7, blank=True)
    topbar_bg = models.CharField(
        max_length=7, blank=True
    )  # fond de la barre du haut ; vide = d'origine (blanc). Le texte se déduit.
    sidebar_bg = models.CharField(max_length=7, blank=True)  # fond du menu de gauche ; vide = d'origine (blanc)
    login_style = models.CharField(max_length=8, choices=LoginStyle.choices, default=LoginStyle.PLAIN)
    logo_has_name = models.BooleanField(
        default=True
    )  # le logo téléversé contient déjà le nom : on n'écrit pas le nom à côté
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls) -> "Branding":
        return cls.objects.get_or_create(pk=1)[0]


class BrandingAsset(models.Model):
    """Une image de la marque, gardée dans la base (source de vérité) : « logo » (en-tête et connexion), « logo_dark » (en-tête quand sa couleur est sombre) ou « icon » (onglet du navigateur)."""

    kind = models.CharField(max_length=12, unique=True)
    content_type = models.CharField(max_length=24)
    data = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True)
    height = models.PositiveIntegerField(null=True)
    updated_at = models.DateTimeField(auto_now=True)
