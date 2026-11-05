import uuid

from django.conf import settings
from django.db import models


class AiPolicy(models.Model):
    """Per-workspace AI governance. Default is OFF: nothing leaves the platform unless an admin opts in."""

    class Mode(models.TextChoices):
        OFF = "off", "IA désactivée"
        LOCAL_STRICT = "local_strict", "Local strict (aucun envoi cloud, aucun repli externe)"
        CLOUD_ALLOWED = "cloud_allowed", "Cloud autorisé (fournisseur choisi explicitement)"

    workspace = models.OneToOneField("tenancy.Workspace", on_delete=models.CASCADE, related_name="ai_policy")
    mode = models.CharField(max_length=14, choices=Mode.choices, default=Mode.OFF)
    daily_token_budget = models.PositiveIntegerField(default=1_000_000)
    redact_cloud = models.BooleanField(default=True)  # mask emails/IPs/tokens before any cloud call
    log_content = models.BooleanField(default=False)  # prompts/outputs are NOT stored unless explicitly enabled
    dry_run = models.BooleanField(default=True)  # the assistant may run a draft's scripts/decisions in the sandbox
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)


class AiProvider(models.Model):
    class Kind(models.TextChoices):
        LOCAL_OPENAI = "local_openai", "Serveur local compatible OpenAI (llama.cpp, Ollama, vLLM)"
        CLOUD_ANTHROPIC = "cloud_anthropic", "Cloud : API Messages d'Anthropic"
        FAKE = "fake", "Faux fournisseur déterministe (tests)"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="ai_providers")
    name = models.SlugField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    base_url = models.CharField(max_length=300, blank=True)
    model = models.CharField(max_length=120, blank=True)
    secret = models.ForeignKey("vault.Secret", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    timeout_seconds = models.PositiveIntegerField(default=30)
    max_tokens = models.PositiveIntegerField(default=1500)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="uniq_ai_provider")]

    @property
    def is_cloud(self) -> bool:
        return self.kind == self.Kind.CLOUD_ANTHROPIC


class AiCall(models.Model):
    """Metadata only (who, what, which model/prompt version, cost, outcome). Content is not kept by default."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="ai_calls")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    provider = models.CharField(max_length=60)
    kind = models.CharField(max_length=20)
    model = models.CharField(max_length=120, blank=True)
    use_case = models.CharField(max_length=30)
    prompt_version = models.CharField(max_length=20)
    params = models.JSONField(default=dict)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12)  # ok | refused | error | invalid
    error_code = models.CharField(max_length=40, blank=True)
    content = models.JSONField(null=True, blank=True)  # only when policy.log_content
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class AiJob(models.Model):
    """A long assistant task run by the worker (not by the web process) so that the person can follow it live: the draft
    generation. `events` is the live log shown in the editor; the request text is dropped once the job is over."""

    class Status(models.TextChoices):
        QUEUED = "queued", "En attente"
        RUNNING = "running", "En cours"
        SUCCEEDED = "succeeded", "Terminé"
        FAILED = "failed", "Échec"
        CANCELLED = "cancelled", "Annulé"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="ai_jobs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=20, default="draft")
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress = models.PositiveSmallIntegerField(default=0)
    events = models.JSONField(default=list)
    result = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    claimed_by = models.CharField(max_length=80, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class AiSkill(models.Model):
    """A reusable AI capability of the library: instructions + typed inputs + expected output, versioned like actions.
    An "AI step" of a workflow can use it instead of a free-form task."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEPRECATED = "deprecated", "Dépréciée"
        # ADR 0025 : proposée par un assistant externe, jamais exécutable ni publiable avant relecture humaine (activation)
        PROPOSED = "proposed", "Proposée"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="ai_skills")
    slug = models.SlugField()
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    folder = models.CharField(max_length=200, blank=True)
    tags = models.JSONField(default=list, blank=True)  # see core.tags
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    deprecation_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="uniq_ai_skill_slug")]
        ordering = ["name", "id"]

    def latest_version(self):
        """Highest semantic version (not the most recently created)."""
        return max(self.versions.all(), key=lambda v: tuple(int(x) for x in v.version.split(".")), default=None)


class AiSkillVersion(models.Model):
    """Immutable once published: a workflow version pins the exact skill version it was published with."""

    skill = models.ForeignKey(AiSkill, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=20)
    instructions = models.TextField()  # may reference its inputs as {{ inputs.<name> }}
    input_schema = models.JSONField(default=dict)
    output_schema = models.JSONField(default=dict)
    default_provider = models.SlugField(blank=True)
    changelog = models.CharField(max_length=300, blank=True)
    digest = models.CharField(max_length=80, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["skill", "version"], name="uniq_ai_skill_version")]

    def save(self, *args, **kwargs):
        import hashlib
        import json

        if self.pk:
            raise PermissionError("AiSkillVersion is immutable once published")
        body = json.dumps(
            [self.instructions, self.input_schema, self.output_schema, self.default_provider], sort_keys=True
        )
        self.digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AiSkillVersion is immutable once published")


class AiEvalRun(models.Model):
    """One evaluation of a provider on a fixed case set. The comparison of scores between prompt versions or models is
    the point: a lower score than a previous run of the same suite is shown as a regression."""

    class Status(models.TextChoices):
        QUEUED = "queued", "En attente"
        RUNNING = "running", "En cours"
        DONE = "done", "Terminée"
        FAILED = "failed", "Échouée"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="ai_eval_runs")
    provider = models.CharField(max_length=60)
    model = models.CharField(max_length=120, blank=True)
    suite = models.CharField(max_length=10)  # search | workflows | edits | vague
    options = models.JSONField(default=dict, blank=True)
    prompts = models.JSONField(default=dict, blank=True)  # use case -> prompt version at the time of the run
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED, db_index=True)
    passed = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    tokens = models.PositiveIntegerField(default=0)
    log = models.TextField(blank=True)
    error = models.CharField(max_length=300, blank=True)
    claimed_by = models.CharField(max_length=80, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
