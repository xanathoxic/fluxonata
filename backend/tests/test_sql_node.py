import os

import pytest

from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from vault.models import Connection, Secret


@pytest.fixture
def ws(world, settings):
    db = dict(settings.DATABASES["default"])
    # Cette base sert de « base du client » que l'étape SQL interroge. Derrière un pooler en mode transaction (scripts/pooler-test.sh) on la
    # joint en DIRECT : PgBouncer refuse le paramètre de démarrage `options` (délai, lecture seule) que l'étape envoie à une base cible,
    # ce qui échoue de façon sûre (SQL_ERROR) mais ne teste plus la garde de lecture seule. Le chemin de la plateforme reste, lui, via le pooler.
    if os.environ.get("ORCH_TEST_TARGET_DB_HOST"):
        db["HOST"], db["PORT"] = os.environ["ORCH_TEST_TARGET_DB_HOST"], os.environ["ORCH_TEST_TARGET_DB_PORT"]
    host = db.get("HOST") or "127.0.0.1"
    settings.ORCH_EGRESS_ALLOWLIST = f"{host},127.0.0.1,localhost"
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="dbpw")
    s.set_value(db.get("PASSWORD") or "x")
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="base",
        label="Base",
        kind="postgres",
        secret=s,
        config={
            "host": host,
            "port": str(db.get("PORT") or 5432),
            "database": db["NAME"],
            "username": db["USER"],
            "sslmode": "prefer",
        },
    )
    return ws


def _run(ws, world, query, slug, **extra):
    user = world["designer"]
    v = publish(ws, slug, [node("q", "sql", connection="base", mode="reel", statement=query, **extra)], [], user=user)
    run = drain(launch(ws, v, user))
    return run, run.steps.get(node_id="q")


def test_select_with_bound_parameters_returns_rows(ws, world):
    run, sr = _run(ws, world, "SELECT %(u)s::text AS username", "a", params={"u": "dana"})
    assert (
        run.status == "succeeded"
        and sr.output["rows"] == [{"username": "dana"}]
        and sr.output["columns"] == ["username"]
    )
    assert sr.output["count"] == 1 and sr.output["truncated"] is False and sr.output["simulated"] is False
    # the value is a bound parameter: an injection attempt is just an unknown name
    run, sr = _run(
        ws, world, "SELECT username FROM auth_user WHERE username = %(u)s", "b", params={"u": "x' OR '1'='1"}
    )
    assert sr.output["rows"] == []


def test_rows_are_capped_and_flagged(ws, world):
    run, sr = _run(ws, world, "SELECT generate_series(1, 50) AS n", "c", max_rows=10)
    assert sr.output["count"] == 10 and sr.output["truncated"] is True and sr.output["rows"][0] == {"n": 1}


def test_writes_are_refused_by_the_database_itself(ws, world):
    run, _ = _run(ws, world, "DELETE FROM auth_user", "d")
    assert run.status == "failed" and run.error["code"] == "SQL_READ_ONLY"
    from django.contrib.auth import get_user_model

    assert get_user_model().objects.filter(username="dana").exists()
    run, _ = _run(ws, world, "SELECT nextval('django_migrations_id_seq')", "e")
    assert run.status == "failed"


def test_single_statement_syntax_timeout_and_missing_connection(ws, world):
    user = world["designer"]
    from workflows.validation import validate_graph

    g = {
        "inputs": {"type": "object"},
        "nodes": [node("q", "sql", connection="base", statement="SELECT 1; DROP TABLE x")],
        "edges": [],
    }
    assert "bad_sql" in {i["code"] for i in validate_graph(g, ws, "t")}
    g["nodes"][0]["connection"] = "inconnue"
    assert "unknown_connection" in {i["code"] for i in validate_graph(g, ws, "t")}
    run, _ = _run(ws, world, "SELEC 1", "f")
    assert run.error["code"] == "SQL_SYNTAX"
    run, _ = _run(ws, world, "SELECT pg_sleep(5)", "g", timeout_seconds=1)
    assert run.error["code"] == "SQL_TIMEOUT"
    assert user  # keep fixture user referenced


def test_test_mode_and_dry_run_send_nothing(ws, world):
    user = world["designer"]
    v = publish(ws, "t", [node("q", "sql", connection="base", mode="test", statement="SELECT 1")], [], user=user)
    run = drain(launch(ws, v, user))
    assert run.steps.get(node_id="q").output["simulated"] is True and run.steps.get(node_id="q").http_job is None


def test_private_servers_are_refused(ws, world, settings):
    settings.ORCH_EGRESS_ALLOWLIST = ""
    run, _ = _run(ws, world, "SELECT 1", "h")
    assert run.error["code"] == "SQL_BLOCKED"
