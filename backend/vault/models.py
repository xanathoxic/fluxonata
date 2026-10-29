from django.conf import settings
from django.db import models

from . import crypto


class Secret(models.Model):
    """Encrypted value. The plaintext is never returned by the API nor written to logs/audit."""

    class Provider(models.TextChoices):
        INTERNAL = "internal", "Coffre de Fluxonata"
        VAULT = "vault", "Coffre externe (HashiCorp Vault)"

    class VaultMode(models.TextChoices):
        MANAGED = "managed", "Écrit par Fluxonata dans Vault"
        REFERENCE = "reference", "Secret existant dans Vault"
        DYNAMIC = "dynamic", "Identifiants dynamiques (Vault)"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="secrets")
    name = models.SlugField()
    ciphertext = models.TextField(
        blank=True, default=""
    )  # empty for an external secret: the value never reaches our database
    key_id = models.CharField(max_length=16, blank=True, default="")
    provider = models.CharField(max_length=10, choices=Provider.choices, default=Provider.INTERNAL)
    vault_mode = models.CharField(max_length=10, choices=VaultMode.choices, blank=True, default="")
    vault_mount = models.CharField(max_length=80, blank=True, default="")
    vault_path = models.CharField(max_length=200, blank=True, default="")
    vault_key = models.CharField(max_length=80, blank=True, default="")
    vault_version = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="uniq_secret_name")]

    PURPOSE = "secret"

    @property
    def is_external(self) -> bool:
        return self.provider == self.Provider.VAULT

    def set_value(self, plaintext: str) -> None:
        if self.is_external:
            from . import external

            external.write_secret(self, plaintext)  # the value goes to Vault, never into our database
            return
        self.key_id, self.ciphertext = crypto.encrypt(plaintext, f"{self.PURPOSE}:{self.workspace_id}")

    def reveal(self) -> str:
        """Server-side only (injection into an authorized action)."""
        if self.is_external:
            from . import external

            return external.reveal(self)
        return crypto.decrypt(self.ciphertext, f"{self.PURPOSE}:{self.workspace_id}")

    def reveal_fields(self) -> dict[str, str]:
        """Every field the secret carries: `value` for a plain secret; a Vault secret may add more (a dynamic database
        credential has `username` and `password`)."""
        if self.is_external:
            from . import external

            return external.fields(self)
        return {"value": self.reveal()}


class Connection(models.Model):
    """A configured link to an external system: public settings + a reference to a secret."""

    class Kind(models.TextChoices):
        HTTP_BEARER = "http_bearer", "Service HTTP (jeton)"
        API_KEY = "api_key", "Service HTTP (clé d'API)"
        BASIC = "basic", "Service HTTP (identifiant + mot de passe)"
        OAUTH2_CLIENT = "oauth2_client", "Service HTTP (OAuth2, identifiants client)"
        OAUTH2_CODE = "oauth2_code", "Service HTTP (OAuth2, autorisation par une personne)"
        AWS_SIGV4 = "aws_sigv4", "Stockage S3 / services AWS (signature SigV4)"
        POSTGRES = "postgres", "Base PostgreSQL (lecture seule)"
        MYSQL = "mysql", "Base MySQL / MariaDB (lecture seule)"
        LDAP = "ldap", "Annuaire LDAP / Active Directory (lecture seule)"
        SFTP = "sftp", "Serveur SFTP (fichiers texte)"
        IMAP = "imap", "Boîte e-mail (IMAP)"
        GENERIC = "generic", "Générique"

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="connections")
    name = models.SlugField()
    label = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.GENERIC)
    config = models.JSONField(default=dict, blank=True)  # non-secret settings (e.g. base_url)
    secret = models.ForeignKey(Secret, null=True, blank=True, on_delete=models.SET_NULL, related_name="connections")
    environment = models.ForeignKey(
        "tenancy.Environment", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_test_ok = models.BooleanField(null=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_message = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "name", "environment"], name="uniq_connection")]

    def login(self, user_key: str = "username") -> tuple[str, str]:
        """(user name, secret) to log in with. The user name is a setting of the connection, except for dynamic Vault
        credentials, where Vault supplies both the user name and the password (the settings need not name a user)."""
        f = self.secret.reveal_fields()
        if self.secret.vault_mode == Secret.VaultMode.DYNAMIC and f.get("username"):
            return f["username"], f["value"]
        return (self.config or {}).get(user_key, ""), f["value"]


class OAuthToken(models.Model):
    """Access (and refresh) token of an OAuth2 connection, encrypted at rest and never returned by the API."""

    connection = models.OneToOneField(Connection, on_delete=models.CASCADE, related_name="oauth_token")
    access_ciphertext = models.TextField(blank=True)
    refresh_ciphertext = models.TextField(blank=True)
    key_id = models.CharField(max_length=16, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    scope = models.CharField(max_length=300, blank=True)
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    def _purpose(self) -> str:
        return f"oauth:{self.connection.workspace_id}"

    def set_tokens(self, access: str, refresh: str | None = None) -> None:
        self.key_id, self.access_ciphertext = crypto.encrypt(access, self._purpose())
        if refresh:
            _, self.refresh_ciphertext = crypto.encrypt(refresh, self._purpose())

    def access(self) -> str:
        return crypto.decrypt(self.access_ciphertext, self._purpose()) if self.access_ciphertext else ""

    def refresh(self) -> str:
        return crypto.decrypt(self.refresh_ciphertext, self._purpose()) if self.refresh_ciphertext else ""


class VaultConfig(models.Model):
    """The external secrets manager of the PLATFORM (one for all workspaces; singleton, pk=1). The AppRole `secret_id` is
    the one credential we must keep: encrypted with the instance key, never returned by the API nor written to a log."""

    address = models.URLField(max_length=300, blank=True)
    namespace = models.CharField(max_length=120, blank=True)
    kv_mount = models.CharField(max_length=80, default="secret")  # KV v2 engine used for the secrets Fluxonata writes
    base_prefix = models.CharField(max_length=120, default="fluxonata")  # ... under <base_prefix>/<workspace>/
    auth_mount = models.CharField(max_length=80, default="approle")
    role_id = models.CharField(max_length=120, blank=True)
    secret_id_ciphertext = models.TextField(blank=True, default="")
    key_id = models.CharField(max_length=16, blank=True, default="")
    ca_pem = models.TextField(blank=True, default="")  # private certificate authority of the Vault server
    verify_tls = models.BooleanField(default=True)
    enabled = models.BooleanField(default=False)
    last_test_ok = models.BooleanField(null=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_message = models.CharField(max_length=300, blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    PURPOSE = "vault-approle"

    def set_secret_id(self, plaintext: str) -> None:
        self.key_id, self.secret_id_ciphertext = crypto.encrypt(plaintext, self.PURPOSE)

    def secret_id(self) -> str:
        return crypto.decrypt(self.secret_id_ciphertext, self.PURPOSE) if self.secret_id_ciphertext else ""


class VaultGrant(models.Model):
    """What else (besides its own `<base_prefix>/<workspace>/` area) a workspace may reach in Vault: a path pattern
    (`*` wildcard) and whether Fluxonata may also write there. Set by a platform administrator only."""

    workspace = models.ForeignKey("tenancy.Workspace", on_delete=models.CASCADE, related_name="vault_grants")
    pattern = models.CharField(max_length=200)  # "mount/path/with/*"
    writable = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "pattern"], name="uniq_vault_grant")]


class VaultLease(models.Model):
    """Dynamic credentials handed out by Vault, kept ENCRYPTED and only until shortly before their lease ends, so that the
    steps (and processes) of one run share the same credentials and the outputs can be masked consistently."""

    secret = models.ForeignKey(Secret, on_delete=models.CASCADE, related_name="leases")
    lease_id = models.CharField(max_length=300, blank=True)
    ciphertext = models.TextField()
    key_id = models.CharField(max_length=16)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    PURPOSE = "vault-lease"
