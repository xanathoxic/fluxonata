"""The « Requête SQL » step: READ-ONLY queries on a PostgreSQL database, through a connection.

Safety, all server-side: the query runs in a READ ONLY transaction with a statement timeout (a write, `nextval`,
temporary objects… are refused by the database itself), one statement only, values go through bound parameters (never
pasted into the text), rows are capped, the server address is checked like every outgoing call (`core.netsec`) and the
connection is pinned to the validated address, TLS is required by default, the password is read from the vault by the
worker at send time. Use a database account that can only read what the workflow needs. `mode: test` sends nothing."""

import json
import logging
import time

from django.db.models import Q

from audit.models import record
from core import netsec
from vault.external import ExternalVaultError

log = logging.getLogger("fluxonata.sql")
MAX_ROWS = 1000
MAX_QUERY = 8000
SIMULATED = {"rows": [], "count": 0, "columns": [], "truncated": False, "simulated": True}


class SqlStepError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez la configuration de l'étape."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def find_connection(workspace_id, environment_id, name):
    from vault.models import Connection

    return (
        Connection.objects.select_related("secret")
        .filter(workspace_id=workspace_id, name=name, kind__in=(Connection.Kind.POSTGRES, Connection.Kind.MYSQL))
        .filter(Q(environment_id=environment_id) | Q(environment__isnull=True))
        .order_by("-environment_id")
        .first()
    )


def clean_query(text) -> str:
    q = str(text or "").strip().rstrip(";").strip()
    if not q or len(q) > MAX_QUERY:
        raise SqlStepError("SQL_CONFIG", f"Requête vide ou trop longue ({MAX_QUERY} caractères au plus).")
    if ";" in q:
        raise SqlStepError("SQL_CONFIG", "Une seule instruction par étape (pas de « ; » au milieu).")
    return q


def prepare(node: dict, resolve, ctx: dict, workspace_id, environment_id) -> dict:
    if find_connection(workspace_id, environment_id, node.get("connection", "")) is None:
        raise SqlStepError(
            "CONNECTION_MISSING",
            f"Connexion « {node.get('connection', '')} » introuvable pour cet environnement (type « Base PostgreSQL »).",
            "Créez la connexion dans Connexions.",
        )
    params = resolve(node.get("params") or {}, ctx)
    if not isinstance(params, dict) or len(params) > 50:
        raise SqlStepError("SQL_CONFIG", "Les paramètres doivent former un objet de 50 entrées au plus.")
    for k, v in params.items():
        if not str(k).replace("_", "").isalnum() or isinstance(v, (dict, set)):
            raise SqlStepError("SQL_CONFIG", f"Paramètre invalide : « {k} » (nom simple, valeur simple ou liste).")
    max_rows = node.get("max_rows")
    return {
        "mode": "reel" if node.get("mode") == "reel" else "test",
        "connection": node["connection"],
        "query": clean_query(node.get("statement")),
        "params": params,
        "max_rows": max_rows if isinstance(max_rows, int) and 1 <= max_rows <= MAX_ROWS else 100,
        "timeout": node.get("timeout_seconds")
        if isinstance(node.get("timeout_seconds"), int) and 1 <= node["timeout_seconds"] <= 60
        else 15,
    }


def simulated_output(spec: dict) -> dict:
    return dict(SIMULATED)


def _execute_mysql(sr, spec, conn, target) -> tuple[dict | None, dict | None]:
    import pymysql
    import pymysql.cursors

    cfg, started = conn.config, time.monotonic()
    ssl_args: dict = {}
    if (cfg.get("tls") or "require") != "disable":
        import ssl

        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # encrypted; the address was validated and is pinned
        ssl_args = {"ssl": ctx}
    try:
        login = conn.login()
    except ExternalVaultError as exc:
        return None, {
            "code": exc.code,
            "message": exc.message,
            "next_step": "Vérifiez le coffre externe (Plate-forme → Coffre externe) et le secret de la connexion.",
        }
    try:
        db = pymysql.connect(
            host=target.ip,
            port=target.port,
            user=login[0],
            password=login[1],
            database=cfg.get("database", ""),
            connect_timeout=10,
            read_timeout=int(spec["timeout"]),
            write_timeout=int(spec["timeout"]),
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            **ssl_args,
        )
    except pymysql.err.OperationalError as exc:
        why = exc.args[0] if exc.args else "erreur"
        return None, {
            "code": "SQL_ERROR",
            "message": f"Connexion à la base refusée ({why}).",
            "next_step": "Vérifiez la connexion, les droits et l'accès réseau.",
        }
    except OSError:
        return None, {
            "code": "SQL_ERROR",
            "message": "La base n'a pas répondu (réseau, délai ou TLS).",
            "next_step": "Vérifiez que le serveur est joignable.",
        }
    try:
        with db.cursor() as cur:
            for stmt in (
                f"SET SESSION max_execution_time={int(spec['timeout']) * 1000}",
                f"SET SESSION max_statement_time={int(spec['timeout'])}",
            ):
                try:
                    cur.execute(stmt)  # MySQL knows the first, MariaDB the second: whichever exists applies
                except pymysql.err.Error:
                    continue
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("START TRANSACTION READ ONLY")
            cur.execute(spec["query"], spec["params"] or None)
            rows = list(cur.fetchmany(spec["max_rows"] + 1)) if cur.description else []
            columns = [c[0] for c in cur.description] if cur.description else []
    except pymysql.err.ProgrammingError as exc:
        return None, {
            "code": "SQL_SYNTAX",
            "message": f"Erreur de syntaxe SQL : {str(exc.args[-1])[:150]}",
            "next_step": "Corrigez la requête.",
        }
    except pymysql.err.OperationalError as exc:
        n = exc.args[0] if exc.args else 0
        if n in (3024, 1969, 2013, 2006):
            return None, {
                "code": "SQL_TIMEOUT",
                "message": "La requête a dépassé le délai maximum.",
                "next_step": "Simplifiez-la ou augmentez le délai.",
            }
        if n == 1792:
            return None, {
                "code": "SQL_READ_ONLY",
                "message": "Cette étape est en lecture seule : la requête tente de modifier des données.",
                "next_step": "Utilisez une requête SELECT.",
            }
        return None, {
            "code": "SQL_ERROR",
            "message": "La base a refusé la requête.",
            "next_step": "Vérifiez la connexion, les droits et la requête.",
        }
    except (pymysql.err.Error, OSError):
        return None, {
            "code": "SQL_ERROR",
            "message": "La base a refusé la requête.",
            "next_step": "Vérifiez la connexion, les droits et la requête.",
        }
    finally:
        db.close()
    truncated = len(rows) > spec["max_rows"]
    rows = json.loads(json.dumps(rows[: spec["max_rows"]], default=str))
    record(
        "sql.query",
        workspace=sr.run.workspace,
        target=sr.run,
        host=target.host,
        rows=len(rows),
        ms=int((time.monotonic() - started) * 1000),
    )
    return {"rows": rows, "count": len(rows), "columns": columns, "truncated": truncated, "simulated": False}, None


def execute(sr) -> tuple[dict | None, dict | None]:
    import psycopg
    from psycopg.rows import dict_row

    spec = sr.http_job["spec"]
    conn = find_connection(sr.run.workspace_id, sr.run.environment_id, spec["connection"])
    if conn is None or conn.secret is None:
        return None, {
            "code": "CONNECTION_MISSING",
            "message": "Connexion introuvable ou sans mot de passe.",
            "next_step": "Vérifiez la connexion.",
        }
    cfg = conn.config
    try:
        target = netsec.validate_url(
            f"https://{cfg.get('host', '')}:{int(cfg.get('port') or (3306 if conn.kind == 'mysql' else 5432))}/"
        )
    except netsec.UnsafeURL as exc:
        return None, {
            "code": "SQL_BLOCKED",
            "message": f"Serveur refusé : {exc}",
            "next_step": "Vérifiez la connexion.",
        }
    if conn.kind == "mysql":
        return _execute_mysql(sr, spec, conn, target)
    started = time.monotonic()
    try:
        login = conn.login()
    except ExternalVaultError as exc:
        return None, {
            "code": exc.code,
            "message": exc.message,
            "next_step": "Vérifiez le coffre externe (Plate-forme → Coffre externe) et le secret de la connexion.",
        }
    try:
        with psycopg.connect(
            host=target.host,
            hostaddr=target.ip,
            port=target.port,
            dbname=cfg.get("database", ""),
            user=login[0],
            password=login[1],
            sslmode=cfg.get("sslmode") or "require",
            connect_timeout=10,
            options=f"-c statement_timeout={int(spec['timeout']) * 1000} -c default_transaction_read_only=on",
            row_factory=dict_row,
        ) as db:
            db.read_only = True
            with db.cursor() as cur:
                cur.execute(spec["query"], spec["params"] or None)
                rows = cur.fetchmany(spec["max_rows"] + 1) if cur.description else []
                columns = [c.name for c in cur.description] if cur.description else []
    except psycopg.errors.ReadOnlySqlTransaction:
        return None, {
            "code": "SQL_READ_ONLY",
            "message": "Cette étape est en lecture seule : la requête tente de modifier des données.",
            "next_step": "Utilisez une requête SELECT.",
        }
    except psycopg.errors.QueryCanceled:
        return None, {
            "code": "SQL_TIMEOUT",
            "message": "La requête a dépassé le délai maximum.",
            "next_step": "Simplifiez-la ou augmentez le délai.",
        }
    except psycopg.errors.SyntaxError as exc:
        return None, {
            "code": "SQL_SYNTAX",
            "message": f"Erreur de syntaxe SQL : {str(exc).splitlines()[0][:150]}",
            "next_step": "Corrigez la requête.",
        }
    except psycopg.Error as exc:
        return None, {
            "code": "SQL_ERROR",
            "message": f"La base a refusé la requête ({type(exc).__name__}).",
            "next_step": "Vérifiez la connexion, les droits et la requête.",
        }
    truncated = len(rows) > spec["max_rows"]
    rows = json.loads(json.dumps(rows[: spec["max_rows"]], default=str))
    record(
        "sql.query",
        workspace=sr.run.workspace,
        target=sr.run,
        host=target.host,
        rows=len(rows),
        ms=int((time.monotonic() - started) * 1000),
    )
    return {"rows": rows, "count": len(rows), "columns": columns, "truncated": truncated, "simulated": False}, None
