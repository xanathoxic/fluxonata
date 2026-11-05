import os
from pathlib import Path

from .cacheconf import build_caches

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BASE_DIR.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(REPO_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DEBUG = env("ORCH_DEBUG", "0") == "1"
SECRET_KEY = env("ORCH_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-dev-only-" + "x" * 40  # DEBUG only; dev-up.sh generates a real one
    else:
        raise RuntimeError("ORCH_SECRET_KEY is required when ORCH_DEBUG is not 1")
ALLOWED_HOSTS = [h for h in env("ORCH_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "core",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "rest_framework",
    "drf_spectacular",
    "tenancy",
    "catalog",
    "workflows",
    "runs",
    "audit",
    "vault",
    "formbuilder",
    "triggers",
    "bundles",
    "ai",
]
MIDDLEWARE = [
    "core.logging.CorrelationMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "fluxonata.urls"
WSGI_APPLICATION = "fluxonata.wsgi.application"
TEMPLATES = []

# 1 = ORCH_DB_HOST/PORT designate a transaction-mode pooler (PgBouncer), see docs/INSTALL.md and ADR 0022. Never for `migrate`.
ORCH_DB_POOLED = env("ORCH_DB_POOLED", "0") == "1"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("ORCH_DB_HOST", "127.0.0.1"),
        "PORT": env("ORCH_DB_PORT", "5433"),
        "NAME": env("ORCH_DB_NAME", "fluxonata"),
        "USER": env("ORCH_DB_USER", "fluxonata"),
        "PASSWORD": env("ORCH_DB_PASSWORD"),
        # Direct to PostgreSQL: one connection per request (0, historical default). Through a transaction-mode pooler (PgBouncer):
        # connections to the pooler are cheap, so they are kept 60 s (AR1.2). ORCH_DB_CONN_MAX_AGE overrides either default.
        "CONN_MAX_AGE": int(env("ORCH_DB_CONN_MAX_AGE", "60" if ORCH_DB_POOLED else "0")),
        "CONN_HEALTH_CHECKS": ORCH_DB_POOLED,  # a pooler restart must not surface as a 500 on the next request
        # Transaction pooling hands the server connection back after each transaction: no session state may be relied upon.
        # Server-side cursors (QuerySet.iterator) and automatic prepared statements both live in the session.
        "DISABLE_SERVER_SIDE_CURSORS": ORCH_DB_POOLED,
        "OPTIONS": {"prepare_threshold": None} if ORCH_DB_POOLED else {},
    }
}
AUTH_PASSWORD_VALIDATORS = [  # applied when a person chooses or changes a password (never to existing hashes)
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"  # API only (no static files served); also required by Django's live test server
LANGUAGE_CODE = "fr"
TIME_ZONE = "UTC"
USE_TZ = True

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
INSECURE_COOKIES = env("ORCH_INSECURE_COOKIES", "0") == "1"  # plain-HTTP evaluation only
SESSION_COOKIE_SECURE = not DEBUG and not INSECURE_COOKIES
CSRF_COOKIE_SECURE = not DEBUG and not INSECURE_COOKIES
if env("ORCH_BEHIND_TLS_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = [o for o in env("ORCH_TRUSTED_ORIGINS", "").split(",") if o]
if DEBUG:  # dev: the browser talks to the Vite server, which proxies /api to Django
    _web = env("ORCH_WEB_PORT", "5173")
    CSRF_TRUSTED_ORIGINS += [f"http://localhost:{_web}", f"http://127.0.0.1:{_web}"]
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "tenancy.auth.BearerTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["tenancy.permissions.IsAuthenticatedAndReady"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "fluxonata.schema.FluxonataSchema",
    "DEFAULT_PAGINATION_CLASS": "core.pagination.BoundedLimitOffsetPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "core.throttles.LocalUserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "login": env("ORCH_LOGIN_RATE", "10/min"),
        "user": env("ORCH_API_RATE", "600/min"),
        "webhook": env("ORCH_WEBHOOK_RATE", "120/min"),
        "form": env("ORCH_FORM_RATE", "30/min"),
        "form_lookup": env("ORCH_FORM_LOOKUP_RATE", "60/min"),
        "ai": env("ORCH_AI_RATE", "30/min"),
        "runner": env("ORCH_RUNNER_RATE", "1500/min"),
    },
    "EXCEPTION_HANDLER": "fluxonata.errors.exception_handler",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Fluxonata API",
    "DESCRIPTION": "Plateforme d'orchestration open source (Apache-2.0).",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
# Cache partagé (limites de débit strictes) : locmem | db | redis://… — voir fluxonata/cacheconf.py (AR1.1).
ORCH_CACHE = env("ORCH_CACHE", "locmem")
ORCH_USER_THROTTLE_CACHE = env(
    "ORCH_USER_THROTTLE_CACHE", "local"
)  # « default » = limite générale exacte, plus coûteuse
CACHES = build_caches(ORCH_CACHE)

# OpenID Connect (optional). The client secret comes from the environment only.
ORCH_OIDC_ISSUER = env("ORCH_OIDC_ISSUER", "")
ORCH_OIDC_CLIENT_ID = env("ORCH_OIDC_CLIENT_ID", "")
ORCH_OIDC_CLIENT_SECRET = env("ORCH_OIDC_CLIENT_SECRET", "")
ORCH_OIDC_REDIRECT_URI = env("ORCH_OIDC_REDIRECT_URI", "http://localhost:5173/api/v1/auth/oidc/callback/")
ORCH_OIDC_SCOPES = env("ORCH_OIDC_SCOPES", "openid profile email")
ORCH_OIDC_AUTO_CREATE = env("ORCH_OIDC_AUTO_CREATE", "1") == "1"
ORCH_OIDC_GROUPS_CLAIM = env("ORCH_OIDC_GROUPS_CLAIM", "groups")
ORCH_OIDC_GROUP_ROLES = env("ORCH_OIDC_GROUP_ROLES", "")  # JSON: {"group": {"workspace": "slug", "role": "designer"}}
ORCH_SCIM_TOKEN = env("ORCH_SCIM_TOKEN", "")  # enables SCIM provisioning (/api/v1/scim/v2/); empty = disabled
ORCH_PUBLIC_URL = env("ORCH_PUBLIC_URL", "")  # external address of the platform (OAuth2 return address)
ORCH_OIDC_POST_LOGIN_URL = env("ORCH_OIDC_POST_LOGIN_URL", "/")
# Hosts that outbound features may reach although they resolve to internal addresses (explicit opt-in).
ORCH_EGRESS_ALLOWLIST = env("ORCH_EGRESS_ALLOWLIST", "")
ORCH_ARTIFACTS_DIR = env("ORCH_ARTIFACTS_DIR", str(REPO_DIR / "var" / "artifacts"))
# Stockage des artefacts (et des archives de paquets) : « fs » = volume local (défaut, un seul hôte) ; « s3 » = service compatible S3, partageable entre
# hôtes (AR1.4, voir runs/s3store.py). Les clés d'accès ne viennent que de l'environnement. L'hôte du service doit figurer dans ORCH_EGRESS_ALLOWLIST.
ORCH_ARTIFACTS_STORE = env("ORCH_ARTIFACTS_STORE", "fs")
ORCH_S3_ENDPOINT = env("ORCH_S3_ENDPOINT", "")
ORCH_S3_BUCKET = env("ORCH_S3_BUCKET", "")
ORCH_S3_PREFIX = env("ORCH_S3_PREFIX", "")
ORCH_S3_REGION = env("ORCH_S3_REGION", "us-east-1")
ORCH_S3_ACCESS_KEY = env("ORCH_S3_ACCESS_KEY", "")
ORCH_S3_SECRET_KEY = env("ORCH_S3_SECRET_KEY", "")
ORCH_S3_TIMEOUT = float(env("ORCH_S3_TIMEOUT", "30"))
ORCH_S3_MAX_OBJECT_MB = int(env("ORCH_S3_MAX_OBJECT_MB", "64"))
ORCH_KEY_DIR = env("ORCH_KEY_DIR", "")
# 1 (défaut, dev/tests) : la première clé du coffre se crée au premier usage. 0 (pile de production) : jamais — `manage.py vault_init` la crée explicitement (AR2.5).
ORCH_KEY_AUTOCREATE = env("ORCH_KEY_AUTOCREATE", "1") == "1"  # empty -> ~/.config/fluxonata/keys
# Worker hardening (see « Durcissement et limites » in docs/INSTALL.md)
ORCH_START_CAP_SECONDS = int(
    env("ORCH_START_CAP_SECONDS", "120")
)  # extra allowance for container start-up (the action limit starts once it runs)
ORCH_EGRESS_TEST_NETWORK = env("ORCH_EGRESS_TEST_NETWORK", "fluxonata-egress-test")
ORCH_HARDENED = env("ORCH_HARDENED", "0") == "1"
ORCH_SECCOMP_PROFILE = env("ORCH_SECCOMP_PROFILE", "")  # path to a JSON seccomp profile (optional)
ORCH_DOCKER_RUNTIME = env("ORCH_DOCKER_RUNTIME", "")  # e.g. "runsc" (gVisor) when installed
ORCH_WORK_DIR = env("ORCH_WORK_DIR", "")  # per-run temp dirs; must be a path shared with the Docker daemon host
# Rôles portés par CE process (ADR 0022, AR2). Vide = tous (comportement historique). `run_worker` : exec, aux, ai (ou worker-exec, worker-aux, worker-ai).
ORCH_VERSION = env(
    "ORCH_VERSION", "0.1.0-dev"
)  # posés à la construction de l'image (ARG du Dockerfile) : voir health.live, /metrics (fluxonata_build_info)
ORCH_REVISION = env("ORCH_REVISION", "")
ORCH_ROLES = env("ORCH_ROLES", "")
ORCH_RUNNER = env("ORCH_RUNNER", "docker")
# MCP de conception (ADR 0025) : tester le CODE d'une action proposée exige un runner ISOLÉ ; 1 = l'autoriser sur le runner « subprocess » (développement seulement, jamais en production).
ORCH_MCP_ACTION_TEST_UNISOLATED = env("ORCH_MCP_ACTION_TEST_UNISOLATED", "0") == "1"
ORCH_PYTHON_IMAGE = env("ORCH_PYTHON_IMAGE", "python:3.13-slim")
ORCH_NODE_IMAGE = env("ORCH_NODE_IMAGE", "node:22-slim")
ORCH_POWERSHELL_IMAGE = env("ORCH_POWERSHELL_IMAGE", "mcr.microsoft.com/powershell:lts-debian-12")
ORCH_LEASE_SECONDS = int(env("ORCH_LEASE_SECONDS", "30"))
ORCH_MAX_ATTEMPTS = int(env("ORCH_MAX_ATTEMPTS", "3"))
# Actions exécutées EN PARALLÈLE par un process worker (rôle exec). 1 = historique. Le démarrage d'un conteneur domine (≈ 5-8 s ici) : sur un hôte qui le permet,
# plusieurs créneaux multiplient le débit ; sur un hôte saturé ils n'apportent rien (voir docs/LOAD_TEST.md, AR4.6).
ORCH_WORKER_CONCURRENCY = int(env("ORCH_WORKER_CONCURRENCY", "1"))
ORCH_MAX_CONCURRENT_PER_WORKSPACE = int(env("ORCH_MAX_CONCURRENT_PER_WORKSPACE", "4"))
# Tâches en file examinées PAR ESPACE à chaque prise (les plus anciennes, après priorité) : borne le coût d'une prise sans laisser un espace au long carnet en affamer un autre (AR5.4).
ORCH_CLAIM_WINDOW_PER_WORKSPACE = int(env("ORCH_CLAIM_WINDOW_PER_WORKSPACE", "50"))
ORCH_WORKER_TAGS = [t for t in env("ORCH_WORKER_TAGS", "").split(",") if t]  # tags carried by the built-in workers
ORCH_RUNNER_LONG_POLL_SECONDS = int(
    env("ORCH_RUNNER_LONG_POLL_SECONDS", "20")
)  # how long a runner request may wait for a job
# Attentes longues simultanées PAR PROCESSUS de l'API (une attente occupe un thread). Pile de production : 2 processus × 8 threads, donc 4 créneaux
# par processus = au plus la moitié des threads. Au-delà, un runner reçoit 429 + « réessayez dans N s » au lieu de bloquer l'interface (AR1.3).
ORCH_MIN_AGENT_VERSION = env(
    "ORCH_MIN_AGENT_VERSION", ""
)  # vide = aucune exigence ; sinon un agent plus ancien reçoit 426 et s'arrête (AR3.7)
ORCH_RUNNER_LONG_POLL_SLOTS = int(env("ORCH_RUNNER_LONG_POLL_SLOTS", "4"))
ORCH_RUNNER_BUSY_RETRY_SECONDS = int(env("ORCH_RUNNER_BUSY_RETRY_SECONDS", "5"))
ORCH_RUNNER_ONLINE_SECONDS = int(
    env("ORCH_RUNNER_ONLINE_SECONDS", "60")
)  # a runner is "online" if it called within this delay
ORCH_MAX_LOG_BYTES = 64 * 1024
ORCH_MAX_RESULT_BYTES = 1024 * 1024

ORCH_LOG_FORMAT = env("ORCH_LOG_FORMAT", "json")  # json | text
ORCH_METRICS_TOKEN = env("ORCH_METRICS_TOKEN", "")  # empty -> /metrics disabled
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"ctx": {"()": "core.logging.ContextFilter"}},
    "formatters": {
        "json": {"()": "core.logging.JsonFormatter"},
        "text": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["ctx"], "formatter": ORCH_LOG_FORMAT}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
