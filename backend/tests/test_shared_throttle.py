"""AR1.1 — limites de débit partagées entre processus.

Un « processus » est simulé par une instance de cache distincte pointant vers le même stockage : c'est exactement la situation de deux
workers gunicorn. Avec `LocMemCache` (un stockage par processus) le même scénario laisse passer le double.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import ScopedRateThrottle

from fluxonata.cacheconf import build_caches, shared_cache

TABLE = "fluxonata_cache"


def db_cache() -> DatabaseCache:
    """Une instance neuve = un processus de plus (aucun état Python partagé avec les autres)."""
    return DatabaseCache(TABLE, {"OPTIONS": {"MAX_ENTRIES": 1000, "CULL_FREQUENCY": 3}})


def throttle_with(cache, rate="3/min"):
    class Scoped(ScopedRateThrottle):
        THROTTLE_RATES = {"login": rate}

    t = Scoped()
    t.cache = cache
    return t


@pytest.fixture
def make_table(settings, db):
    """Crée la table du cache partagé comme le fait le service `migrate` (createcachetable sur l'alias configuré)."""
    settings.CACHES = {**settings.CACHES, "shared_db": shared_cache("db")}

    def make():
        call_command("createcachetable", verbosity=0)

    return make


class View:
    throttle_scope = "login"


def anon_request(addr="10.1.2.3"):
    req = APIRequestFactory().post("/x/", REMOTE_ADDR=addr)
    req.user = AnonymousUser()  # comme après l'authentification DRF : la limite est alors par adresse
    return req


def allowed(throttle, addr="10.1.2.3") -> bool:
    return throttle.allow_request(anon_request(addr), View())


# --- configuration -------------------------------------------------------------------------------------------------


def test_default_is_per_process_and_documented():
    assert shared_cache("locmem")["BACKEND"].endswith("LocMemCache")
    assert shared_cache("")["BACKEND"].endswith("LocMemCache")


def test_db_and_redis_specs():
    assert shared_cache("db")["BACKEND"].endswith("DatabaseCache") and shared_cache("db")["LOCATION"] == TABLE
    r = shared_cache("redis://cache:6379/1")
    assert r["BACKEND"].endswith("RedisCache") and r["LOCATION"] == "redis://cache:6379/1"
    assert shared_cache("rediss://cache:6380/0")["BACKEND"].endswith("RedisCache")


@pytest.mark.parametrize("bad", ["memcached", "redis", "http://x", "DB"])
def test_unknown_spec_fails_at_startup_not_silently(bad):
    with pytest.raises(ImproperlyConfigured):
        shared_cache(bad)


def test_local_cache_always_present_and_separate():
    c = build_caches("db")
    assert set(c) == {"default", "local"} and c["local"]["BACKEND"].endswith("LocMemCache")
    assert c["local"]["LOCATION"] != build_caches("locmem")["default"]["LOCATION"]


def test_settings_wire_the_general_user_limit_to_the_local_cache(settings):
    from rest_framework.settings import api_settings

    assert "core.throttles.LocalUserRateThrottle" in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
    assert "rest_framework.throttling.UserRateThrottle" not in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
    assert api_settings.DEFAULT_THROTTLE_RATES["login"]  # les portées existantes sont inchangées


def test_user_throttle_cache_can_be_made_exact(settings):
    from core.throttles import LocalUserRateThrottle

    assert LocalUserRateThrottle().cache is caches["local"]
    settings.ORCH_USER_THROTTLE_CACHE = "default"
    assert LocalUserRateThrottle().cache is caches["default"]


# --- le comportement qui motive la tâche --------------------------------------------------------------------------------


def test_per_process_memory_lets_the_limit_be_exceeded():
    """Le défaut que corrige AR1.1 : deux « processus » à mémoire séparée comptent chacun de leur côté."""
    from django.core.cache.backends.locmem import LocMemCache

    a, b = LocMemCache("p1", {}), LocMemCache("p2", {})
    ta, tb = throttle_with(a), throttle_with(b)
    passed = sum(allowed(t) for t in (ta, tb) for _ in range(3))
    assert passed == 6  # plafond configuré : 3


def test_shared_cache_enforces_the_configured_limit_across_processes(make_table):
    make_table()
    a, b = db_cache(), db_cache()
    a.clear()
    ta, tb = throttle_with(a), throttle_with(b)
    results = [allowed(ta), allowed(tb), allowed(ta), allowed(tb), allowed(ta), allowed(tb)]
    assert results == [True, True, True, False, False, False]  # 3 passent au total, pas 3 par processus


def test_shared_cache_is_per_address_not_global(make_table):
    make_table()
    a, b = db_cache(), db_cache()
    a.clear()
    ta, tb = throttle_with(a, "1/min"), throttle_with(b, "1/min")
    assert allowed(ta, "10.0.0.1")
    assert not allowed(tb, "10.0.0.1")
    assert allowed(throttle_with(b, "1/min"), "10.0.0.2")  # une autre adresse n'est pas touchée


def test_createcache_is_idempotent_for_a_restart_of_the_migrate_service(make_table):
    make_table()
    make_table()  # ne doit pas échouer : le service `migrate` le relance à chaque démarrage
