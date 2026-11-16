"""Étape SQL : chaque défaut de la base devient une erreur explicite et sans fuite (pilotes remplacés par des doublures)."""

from types import SimpleNamespace

import psycopg
import pymysql
import pytest

from core import netsec
from runs import sql_node
from vault.external import ExternalVaultError

TARGET = SimpleNamespace(host="db.test", ip="127.0.0.1", port=3306)
SPEC = {"query": "select 1", "params": {}, "max_rows": 5, "timeout": 5, "connection": "db"}


def mysql_conn(tls=None, login=lambda: ("u", "p")):
    cfg = {"database": "d", **({"tls": tls} if tls else {})}
    return SimpleNamespace(config=cfg, login=login, kind="mysql", secret=object())


class FakeCursor:
    def __init__(self, fail=None):
        self.fail, self.description = fail, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        if self.fail and stmt == "select 1":
            raise self.fail

    def fetchmany(self, n):
        return []


class FakeDb:
    def __init__(self, fail=None):
        self.fail, self.closed = fail, False

    def cursor(self):
        return FakeCursor(self.fail)

    def close(self):
        self.closed = True


def run_mysql(monkeypatch, conn, connect=None, fail=None):
    db = FakeDb(fail)
    seen = {}

    def fake_connect(**kw):
        seen.update(kw)
        if connect:
            raise connect
        return db

    monkeypatch.setattr(pymysql, "connect", fake_connect)
    out = sql_node._execute_mysql(None, SPEC, conn, TARGET)
    return out, seen, db


def test_mysql_tls_is_on_unless_disabled(monkeypatch):
    _, seen, _ = run_mysql(monkeypatch, mysql_conn(), connect=OSError())
    assert "ssl" in seen
    _, seen, _ = run_mysql(monkeypatch, mysql_conn(tls="disable"), connect=OSError())
    assert "ssl" not in seen


def test_mysql_connection_failures(monkeypatch):
    (_, err), _, _ = run_mysql(monkeypatch, mysql_conn(), connect=pymysql.err.OperationalError(1045, "Access denied"))
    assert err["code"] == "SQL_ERROR" and "1045" in err["message"] and "Access denied" not in err["message"]
    (_, err), _, _ = run_mysql(monkeypatch, mysql_conn(), connect=pymysql.err.OperationalError())
    assert "erreur" in err["message"]
    (_, err), _, _ = run_mysql(monkeypatch, mysql_conn(), connect=OSError())
    assert "n'a pas répondu" in err["message"]


def test_mysql_vault_failure_keeps_its_code(monkeypatch):
    def down():
        raise ExternalVaultError("VAULT_DOWN", "coffre indisponible")

    (_, err), _, _ = run_mysql(monkeypatch, mysql_conn(login=down))
    assert err["code"] == "VAULT_DOWN" and "coffre externe" in err["next_step"]


@pytest.mark.parametrize(
    ("fail", "code"),
    [
        (pymysql.err.ProgrammingError(1064, "syntax error near x"), "SQL_SYNTAX"),
        (pymysql.err.OperationalError(3024, "timeout"), "SQL_TIMEOUT"),
        (pymysql.err.OperationalError(2013, "lost"), "SQL_TIMEOUT"),
        (pymysql.err.OperationalError(1792, "read only"), "SQL_READ_ONLY"),
        (pymysql.err.OperationalError(1142, "denied"), "SQL_ERROR"),
        (pymysql.err.OperationalError(), "SQL_ERROR"),
        (pymysql.err.InternalError(1, "x"), "SQL_ERROR"),
        (OSError(), "SQL_ERROR"),
    ],
)
def test_mysql_query_failures_and_the_connection_is_always_closed(monkeypatch, fail, code):
    (result, err), _, db = run_mysql(monkeypatch, mysql_conn(), fail=fail)
    assert result is None and err["code"] == code and db.closed is True
    assert "secret" not in err["message"].lower()


# ---- PostgreSQL


def pg_run(monkeypatch, fail=None, login=lambda: ("u", "p"), blocked=None):
    conn = SimpleNamespace(
        config={"host": "db.test", "database": "d"}, login=login, kind="postgres", secret=object(), name="db"
    )
    monkeypatch.setattr(sql_node, "find_connection", lambda *a: conn)
    sr = SimpleNamespace(
        http_job={"spec": SPEC}, run=SimpleNamespace(workspace_id=1, environment_id=None, workspace=None)
    )

    def validate(url):
        if blocked:
            raise netsec.UnsafeURL(blocked)
        return SimpleNamespace(host="db.test", ip="127.0.0.1", port=5432)

    monkeypatch.setattr(netsec, "validate_url", validate)

    def connect(**kw):
        raise fail

    monkeypatch.setattr(psycopg, "connect", connect)
    return sql_node.execute(sr)


@pytest.mark.parametrize(
    ("fail", "code"),
    [
        (psycopg.errors.ReadOnlySqlTransaction("ro"), "SQL_READ_ONLY"),
        (psycopg.errors.QueryCanceled("annulée"), "SQL_TIMEOUT"),
        (psycopg.errors.SyntaxError("erreur\ndétail"), "SQL_SYNTAX"),
        (psycopg.OperationalError("connexion refusée avec le mot de passe hunter2"), "SQL_ERROR"),
    ],
)
def test_postgres_failures(monkeypatch, fail, code):
    result, err = pg_run(monkeypatch, fail=fail)
    assert result is None and err["code"] == code and "hunter2" not in err["message"]


def test_postgres_setup_failures(monkeypatch):
    assert pg_run(monkeypatch, blocked="privée")[1]["code"] == "SQL_BLOCKED"

    def down():
        raise ExternalVaultError("VAULT_DOWN", "indisponible")

    assert pg_run(monkeypatch, login=down)[1]["code"] == "VAULT_DOWN"
    monkeypatch.setattr(sql_node, "find_connection", lambda *a: None)
    sr = SimpleNamespace(
        http_job={"spec": SPEC}, run=SimpleNamespace(workspace_id=1, environment_id=None, workspace=None)
    )
    assert sql_node.execute(sr)[1]["code"] == "CONNECTION_MISSING"
