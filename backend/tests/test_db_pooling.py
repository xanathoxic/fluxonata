"""AR1.2 — connexions à PostgreSQL via un pooler en mode transaction (PgBouncer).

Ces tests vérifient la configuration (réglages Django et pile de production). Le comportement réel à travers un PgBouncer est
éprouvé par `scripts/pooler-test.sh` (suite complète derrière le pooler). Pas de base de données ici.
"""

import runpy
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.prod.yml"


def load_settings(monkeypatch, **env):
    for k in ("ORCH_DB_POOLED", "ORCH_DB_CONN_MAX_AGE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ORCH_DEBUG", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return runpy.run_module("fluxonata.settings")["DATABASES"]["default"]


def test_direct_connection_keeps_the_historical_behaviour(monkeypatch):
    db = load_settings(monkeypatch)
    assert db["CONN_MAX_AGE"] == 0
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is False and db["CONN_HEALTH_CHECKS"] is False
    assert db["OPTIONS"] == {}


def test_pooled_connection_is_safe_for_transaction_pooling(monkeypatch):
    db = load_settings(monkeypatch, ORCH_DB_POOLED="1")
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is True  # QuerySet.iterator() : un curseur serveur vit dans la session
    assert db["OPTIONS"] == {"prepare_threshold": None}  # pas d'instruction préparée automatique (liée à la session)
    assert db["CONN_HEALTH_CHECKS"] is True and db["CONN_MAX_AGE"] == 60


def test_max_age_can_be_overridden_either_way(monkeypatch):
    assert load_settings(monkeypatch, ORCH_DB_POOLED="1", ORCH_DB_CONN_MAX_AGE="0")["CONN_MAX_AGE"] == 0
    assert load_settings(monkeypatch, ORCH_DB_CONN_MAX_AGE="30")["CONN_MAX_AGE"] == 30


# --- pile de production --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stack():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def env_of(service: dict) -> dict:
    e = service.get("environment") or {}
    return e if isinstance(e, dict) else dict(item.split("=", 1) for item in e)


@pytest.mark.parametrize("name", ["api", "orchestrator", "worker"])
def test_runtime_roles_reach_the_database_through_the_pooler(stack, name):
    e = env_of(stack[name])
    assert e["ORCH_DB_HOST"] == "pgbouncer" and e["ORCH_DB_POOLED"] == "1"
    assert "pgbouncer" in stack[name]["depends_on"] and "migrate" in stack[name]["depends_on"]


def test_migrations_stay_on_a_direct_connection(stack):
    """DDL et `createcachetable` n'ont rien à faire derrière un pooler en mode transaction."""
    e = env_of(stack["migrate"])
    assert e.get("ORCH_DB_POOLED") in (None, "0") and "ORCH_DB_HOST" not in e  # l'hôte vient de .env.prod : `db`


def test_only_the_pooler_talks_to_postgres_directly(stack):
    holders = {
        n
        for n, s in stack.items()
        if "pgbouncer" not in env_of(s).get("ORCH_DB_HOST", "") and "ORCH_DB_HOST" in env_of(s)
    }
    assert holders == set()
    assert "db" in stack["pgbouncer"]["depends_on"]
    assert "@db:5432/" in env_of(stack["pgbouncer"])["DATABASE_URL"]


def test_pooler_is_pinned_and_in_transaction_mode(stack):
    p = stack["pgbouncer"]
    assert "@sha256:" in p["image"]  # empreinte : on n'exécute pas ce qu'un tag désignera demain
    e = env_of(p)
    assert e["POOL_MODE"] == "transaction" and e["AUTH_TYPE"] == "scram-sha-256"
    assert "ports" not in p  # jamais publié sur l'hôte
    assert int(e["DEFAULT_POOL_SIZE"]) < 100  # très en dessous du max_connections de PostgreSQL (100 par défaut)
