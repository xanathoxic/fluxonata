"""Configuration des caches (AR1.1).

Le cache `default` porte les compteurs de limitation de débit des portées strictes (connexion, webhooks, formulaires publics, IA, runners).
Avec `LocMemCache`, chaque processus a son propre compteur : le plafond réel devient « plafond × nombre de processus » (déjà ×2 avec
les 2 workers gunicorn de la pile de production). Les déploiements à plusieurs processus ou réplicas doivent donc utiliser un cache partagé.

    ORCH_CACHE=locmem            (défaut ; développement, tests, un seul processus)
    ORCH_CACHE=db                table `fluxonata_cache` dans PostgreSQL (créée par `manage.py createcachetable`, lancé par le service `migrate`)
    ORCH_CACHE=redis://hôte:6379/1   Redis (paquet Python `redis` requis, non installé par défaut)

Le cache `local` reste en mémoire du processus : il sert à la limite générale par utilisateur (grossière, voir core/throttles.py), pour ne
pas ajouter deux requêtes SQL à chaque appel d'API.
"""

from django.core.exceptions import ImproperlyConfigured

LOCMEM = "django.core.cache.backends.locmem.LocMemCache"
DB_TABLE = "fluxonata_cache"


def shared_cache(spec: str) -> dict:
    spec = (spec or "locmem").strip()
    if spec == "locmem":
        return {"BACKEND": LOCMEM, "LOCATION": "fluxonata-shared"}
    if spec == "db":
        return {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": DB_TABLE,
            "OPTIONS": {"MAX_ENTRIES": 100_000, "CULL_FREQUENCY": 3},
        }
    if spec.startswith(("redis://", "rediss://")):
        return {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": spec}
    raise ImproperlyConfigured(f"ORCH_CACHE={spec!r} inconnu : attendu locmem, db ou une URL redis://")


def build_caches(spec: str) -> dict:
    return {"default": shared_cache(spec), "local": {"BACKEND": LOCMEM, "LOCATION": "fluxonata-local"}}
