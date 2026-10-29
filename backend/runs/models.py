import uuid

from django.conf import settings
from django.db import models


class Runner(models.Model):
    """A remote execution agent (like a GitLab runner). It talks to the platform over HTTPS only, authenticates with a
    token (only its SHA-256 is stored), is shared across spaces or dedicated to some of them, and never sees the database,
    the vault key or any job it was not given."""

    class Scope(models.TextChoices):
        SHARED = "shared", "Partagé"
        DEDICATED = "dedicated", "Dédié à des espaces"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=60, unique=True)
    description = models.CharField(max_length=200, blank=True)
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.SHARED)
    workspaces = models.ManyToManyField("tenancy.Workspace", blank=True, related_name="runners")
    tags = models.JSONField(default=list, blank=True)
    runtimes = models.JSONField(
        default=list
    )  # which runtimes the agent is allowed to run (subset of RUNTIMES, never "ai")
    max_concurrent = models.PositiveSmallIntegerField(default=2)
    paused = models.BooleanField(default=False)
    token_hash = models.CharField(max_length=64, unique=True)
    token_prefix = models.CharField(max_length=16)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_contact_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    agent_version = models.CharField(max_length=40, blank=True)
    platform = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class Execution(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En attente"
        RUNNING = "running", "En cours"
        SUCCEEDED = "succeeded", "Réussie"
        FAILED = "failed", "Échouée"
        CANCELLED = "cancelled", "Annulée"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="executions")
    environment = models.ForeignKey("tenancy.Environment", on_delete=models.PROTECT, related_name="+")
    action_version = models.ForeignKey("catalog.ActionVersion", on_delete=models.PROTECT, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    inputs = models.JSONField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    result = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)  # {"code", "message", "next_step"}
    idempotency_key = models.CharField(max_length=128, null=True, blank=True)
    step_run = models.ForeignKey(
        "runs.StepRun", null=True, blank=True, on_delete=models.CASCADE, related_name="executions"
    )
    priority = models.IntegerField(default=0)
    cancel_requested = models.BooleanField(default=False)
    connections = models.JSONField(default=dict, blank=True)  # slot -> connection name
    limits = models.JSONField(null=True, blank=True)  # per-execution override of the version limits (script tasks)
    # Execution-level lease/fencing (the Postgres rows are the source of truth, no broker in P1)
    attempt_count = models.PositiveIntegerField(default=0)  # also the fencing token of the live attempt
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    worker_id = models.CharField(max_length=80, blank=True)
    runner = models.ForeignKey("Runner", null=True, blank=True, on_delete=models.SET_NULL, related_name="executions")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"],
                name="uniq_exec_idempotency",
                condition=models.Q(idempotency_key__isnull=False),
            )
        ]


class Attempt(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "En cours"
        SUCCEEDED = "succeeded", "Réussie"
        FAILED = "failed", "Échouée"
        LOST = "lost", "Perdue (lease expirée)"

    execution = models.ForeignKey(Execution, on_delete=models.CASCADE, related_name="attempts")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    worker_id = models.CharField(max_length=80)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    logs = models.TextField(blank=True)  # separate from the result

    class Meta:
        constraints = [models.UniqueConstraint(fields=["execution", "number"], name="uniq_attempt_number")]
        ordering = ["number"]


class ExecutionEvent(models.Model):
    """Ordered per-execution event stream; `seq` is the catch-up cursor."""

    execution = models.ForeignKey(Execution, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    type = models.CharField(max_length=40)
    message = models.CharField(max_length=300)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["execution", "seq"], name="uniq_event_seq")]
        ordering = ["seq"]


class WorkflowRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "En cours"
        COMPENSATING = "compensating", "Compensation en cours"
        SUCCEEDED = "succeeded", "Réussie"
        FAILED = "failed", "Échouée"
        CANCELLED = "cancelled", "Annulée"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="workflow_runs")
    environment = models.ForeignKey("tenancy.Environment", on_delete=models.PROTECT, related_name="+")
    workflow_version = models.ForeignKey("workflows.WorkflowVersion", on_delete=models.PROTECT, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    inputs = models.JSONField()
    # Frozen at launch: later catalog/workflow changes never affect a running instance.
    definition = models.JSONField()
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.RUNNING, db_index=True)
    outputs = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    parent_step = models.ForeignKey(
        "runs.StepRun", null=True, blank=True, on_delete=models.CASCADE, related_name="child_runs"
    )
    child_index = models.IntegerField(null=True, blank=True)
    priority = models.IntegerField(default=0)
    cancel_requested = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=128, null=True, blank=True)
    callback_url = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"],
                name="uniq_run_idempotency",
                condition=models.Q(idempotency_key__isnull=False),
            )
        ]


class StepRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "En cours"
        RETRY_WAIT = "retry_wait", "Nouvelle tentative programmée"
        WAITING = "waiting", "En attente"
        UNCERTAIN = "uncertain", "Issue incertaine : décision requise"
        SUCCEEDED = "succeeded", "Réussie"
        FAILED = "failed", "Échouée"
        SKIPPED = "skipped", "Ignorée"
        CANCELLED = "cancelled", "Annulée"

    TERMINAL = ("succeeded", "failed", "skipped", "cancelled")

    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="steps")
    node_id = models.CharField(max_length=64)
    node_type = models.CharField(max_length=20)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    attempt = models.PositiveIntegerField(default=0)
    output = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    available_at = models.DateTimeField(null=True, blank=True)
    resume_payload = models.JSONField(null=True, blank=True)  # body received by a wait that resumes on an HTTP call
    agent_state = models.JSONField(null=True, blank=True)  # state of an « agent » step (phase, iteration, steps taken…)
    http_job = models.JSONField(
        null=True, blank=True
    )  # request of an « http » step: state queued|running|done + outcome
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    seq = models.PositiveIntegerField(default=0)  # completion order (used for reverse compensation)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "node_id"], name="uniq_step_per_run")]


class RunEvent(models.Model):
    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    type = models.CharField(max_length=40)
    node_id = models.CharField(max_length=64, blank=True)
    message = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "seq"], name="uniq_runevent_seq")]
        ordering = ["seq"]


class Approval(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        APPROVED = "approved", "Approuvée"
        REJECTED = "rejected", "Refusée"

    step_run = models.OneToOneField(StepRun, on_delete=models.CASCADE, related_name="approval")
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="approvals")
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    comment = models.CharField(max_length=500, blank=True)
    allow_self_approval = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)


class Artifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="artifacts")
    execution = models.ForeignKey(Execution, null=True, on_delete=models.CASCADE, related_name="artifacts")
    name = models.CharField(max_length=120)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RunFile(models.Model):
    """A file (any bytes) produced or read by a step of a run: kept in PostgreSQL with the run, deleted with it (retention).
    Steps exchange it by reference (`{"file_id": …}`); the content never travels in step outputs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="run_files")
    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="files")
    step_id = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=120)
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class CallbackDelivery(models.Model):
    """Transactional outbox for outbound run-completion callbacks (retries with backoff, SSRF-checked)."""

    class Status(models.TextChoices):
        PENDING = "pending", "À envoyer"
        DELIVERED = "delivered", "Remis"
        FAILED = "failed", "Abandonné"

    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="callbacks")
    url = models.CharField(max_length=500)
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AlertRule(models.Model):
    """Tells someone (a chat webhook, a monitoring endpoint) when runs fail. The destination address is a secret: it is
    stored encrypted and never returned (only its host is shown)."""

    class Event(models.TextChoices):
        RUN_FAILED = "run_failed", "Une exécution échoue"
        FAILURE_RATE = "failure_rate", "Le taux d'échec dépasse un seuil"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="alert_rules")
    name = models.CharField(max_length=120)
    event = models.CharField(max_length=14, choices=Event.choices)
    enabled = models.BooleanField(default=True)
    environment = models.CharField(max_length=4, blank=True)  # dev | test | prod | "" (any)
    workflow = models.ForeignKey(
        "workflows.Workflow", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    threshold_percent = models.PositiveIntegerField(default=30)
    min_runs = models.PositiveIntegerField(default=10)
    window_minutes = models.PositiveIntegerField(default=60)
    cooldown_minutes = models.PositiveIntegerField(default=60)
    format = models.CharField(max_length=5, default="slack")  # slack ({"text": …}) | json (full facts)
    url_ciphertext = models.TextField()
    key_id = models.CharField(max_length=16)
    url_host = models.CharField(max_length=200, blank=True)
    last_fired_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def _purpose(self) -> str:
        return f"alert:{self.workspace_id}"

    def set_url(self, url: str) -> None:
        from vault import crypto

        self.key_id, self.url_ciphertext = crypto.encrypt(url, self._purpose())

    def reveal_url(self) -> str:
        from vault import crypto

        return crypto.decrypt(self.url_ciphertext, self._purpose())


class AlertDelivery(models.Model):
    """Outbox of alerts (retried with backoff, SSRF-checked at send time)."""

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="deliveries")
    payload = models.JSONField()
    status = models.CharField(max_length=10, default="pending", db_index=True)  # pending | delivered | failed
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ServiceInstance(models.Model):
    """Registry of running processes (workers, orchestrator), updated by their own heartbeat."""

    kind = models.CharField(max_length=20)  # worker | orchestrator
    name = models.CharField(max_length=120, unique=True)
    hostname = models.CharField(max_length=120, blank=True)
    pid = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(db_index=True)
    info = models.JSONField(default=dict, blank=True)
