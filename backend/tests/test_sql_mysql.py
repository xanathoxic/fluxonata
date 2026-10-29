import shutil
import socket
import subprocess
import time

import pytest

from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from vault.models import Connection, Secret

pymysql = pytest.importorskip("pymysql")
PASSWORD = "test-pw-12345"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mariadb():
    if shutil.which("docker") is None:
        pytest.skip("docker indisponible")
    port = _free_port()
    cid = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            f"127.0.0.1:{port}:3306",
            "-e",
            f"MARIADB_ROOT_PASSWORD={PASSWORD}",
            "-e",
            "MARIADB_DATABASE=essai",
            "mariadb:11",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not cid:
        pytest.skip("image mariadb:11 indisponible")
    try:
        deadline = time.time() + 240
        while True:
            try:
                db = pymysql.connect(
                    host="127.0.0.1", port=port, user="root", password=PASSWORD, database="essai", connect_timeout=3
                )
                break
            except pymysql.err.Error:
                if time.time() > deadline:
                    pytest.skip("mariadb ne démarre pas")
                time.sleep(1)
        with db.cursor() as cur:
            cur.execute("CREATE TABLE gens (id INT PRIMARY KEY, nom VARCHAR(40))")
            cur.execute("INSERT INTO gens VALUES (1, 'Ada'), (2, 'Bob'), (3, 'Chloé')")
        db.commit()
        db.close()
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


@pytest.fixture
def ws(world, mariadb, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    s = Secret(workspace=ws, name="mypw")
    s.set_value(PASSWORD)
    s.save()
    Connection.objects.create(
        workspace=ws,
        name="my",
        label="MariaDB",
        kind="mysql",
        secret=s,
        config={"host": "127.0.0.1", "port": str(mariadb), "database": "essai", "username": "root", "tls": "disable"},
    )
    return ws


def _run(ws, world, query, slug, **extra):
    user = world["designer"]
    v = publish(ws, slug, [node("q", "sql", connection="my", mode="reel", statement=query, **extra)], [], user=user)
    run = drain(launch(ws, v, user), max_rounds=1500)
    return run, run.steps.get(node_id="q")


def test_select_with_parameters_and_row_cap(ws, world):
    run, sr = _run(ws, world, "SELECT id, nom FROM gens WHERE nom = %(n)s", "a", params={"n": "Bob"})
    assert (
        run.status == "succeeded"
        and sr.output["rows"] == [{"id": 2, "nom": "Bob"}]
        and sr.output["columns"] == ["id", "nom"]
    )
    run, sr = _run(ws, world, "SELECT id FROM gens ORDER BY id", "b", max_rows=2)
    assert sr.output["count"] == 2 and sr.output["truncated"] is True
    run, sr = _run(ws, world, "SELECT %(u)s AS valeur", "c", params={"u": "x' OR '1'='1"})
    assert sr.output["rows"] == [{"valeur": "x' OR '1'='1"}]


def test_writes_syntax_errors_and_blocked_hosts(ws, world, settings):
    run, _ = _run(ws, world, "DELETE FROM gens", "d")
    assert run.status == "failed" and run.error["code"] == "SQL_READ_ONLY"
    run, sr = _run(ws, world, "SELECT COUNT(*) AS n FROM gens", "e")
    assert sr.output["rows"] == [{"n": 3}]  # nothing was deleted
    run, _ = _run(ws, world, "SELEC 1", "f")
    assert run.error["code"] == "SQL_SYNTAX"
    settings.ORCH_EGRESS_ALLOWLIST = ""
    run, _ = _run(ws, world, "SELECT 1", "g")
    assert run.error["code"] == "SQL_BLOCKED"
