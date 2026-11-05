#!/usr/bin/env python3
"""O6 (AR0.3 / AR1.3) — N runners en attente longue : l'interface reste-t-elle réactive ?

    .venv/bin/python scripts/loadtest_runners.py [--runners 60] [--seconds 30] [--slots 4 100]

Pour chaque valeur de `ORCH_RUNNER_LONG_POLL_SLOTS` (100 ≈ l'ancien comportement : aucun plafond effectif ; 4 = le défaut), le script :
base jetable `fluxonata_o6` (jamais celle de développement) → migrate → un runner → gunicorn avec les réglages de la pile de production
(2 processus × 8 threads) → N clients qui imitent l'agent (attente de 20 s, retour au bout de `retry_after` sur 429) → pendant ce temps, une requête
`GET /api/v1/health/live/` toutes les 100 ms, dont on mesure la latence. `health/live` est la requête la moins chère de l'API : si elle attend, c'est qu'il
n'y a plus de thread libre. Stdlib + psycopg ; machine de mesure = la vôtre (le client de charge consomme du CPU : chiffres pessimistes et non transposables).
"""

import argparse
import http.client
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
GUNICORN = str(ROOT / ".venv" / "bin" / "gunicorn")
DB = "fluxonata_o6"
PORT = 8251


def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def sql(dbname, stmt):
    import psycopg

    with psycopg.connect(
        host=os.environ["ORCH_DB_HOST"],
        port=os.environ["ORCH_DB_PORT"],
        user=os.environ["ORCH_DB_USER"],
        password=os.environ["ORCH_DB_PASSWORD"],
        dbname=dbname,
        autocommit=True,
    ) as c:
        c.execute(stmt)


def manage(env, *args, stdin=None):
    return subprocess.run(
        [PY, "manage.py", *args],
        cwd=ROOT / "backend",
        env=env,
        input=stdin,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def call(method, path, token=None, body=None, timeout=60):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    c.request(method, path, json.dumps(body) if body is not None else None, headers)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, (json.loads(raw) if raw else None)
    except (
        ValueError
    ):  # corps non JSON (page d'erreur du serveur saturé) : on garde le code HTTP
        return r.status, None


def measure(slots, runners, seconds):
    env = {
        **os.environ,
        "ORCH_DB_NAME": DB,
        "ORCH_DEBUG": "1",
        "ORCH_RUNNER_LONG_POLL_SLOTS": str(slots),
        "ORCH_RUNNER_RATE": "100000/min",
    }
    sql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    sql("postgres", f"CREATE DATABASE {DB}")
    manage(env, "migrate", "--noinput")
    token = (
        manage(
            env,
            "shell",
            "-c",
            "import hashlib;from runs.runner_auth import new_token;from runs.models import Runner;"
            "t,h,p=new_token();Runner.objects.create(name='o6',scope='shared',token_hash=h,token_prefix=p);print(t)",
        )
        .strip()
        .splitlines()[-1]
    )
    g = subprocess.Popen(
        [
            GUNICORN,
            "fluxonata.wsgi",
            "-b",
            f"127.0.0.1:{PORT}",
            "-w",
            "2",
            "--worker-class",
            "gthread",
            "--threads",
            "8",
            "--timeout",
            "60",
        ],
        cwd=ROOT / "backend",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                if call("GET", "/api/v1/health/live/")[0] == 200:
                    break
            except OSError:
                time.sleep(0.5)
        stop = threading.Event()
        counts = {"200": 0, "204": 0, "429": 0, "other": 0}
        lock = threading.Lock()

        def runner_loop():
            while not stop.is_set():
                try:
                    status, body = call(
                        "POST",
                        "/api/v1/runner/jobs/request/",
                        token,
                        {"wait": 20},
                        timeout=40,
                    )
                except OSError:
                    time.sleep(1)
                    continue
                with lock:
                    counts[str(status) if str(status) in counts else "other"] += 1
                    if status not in (200, 204, 429):
                        counts.setdefault("other_codes", set()).add(status)
                if status == 429:
                    stop.wait(
                        float(
                            ((body or {}).get("error") or {})
                            .get("details", {})
                            .get("retry_after", 5)
                        )
                    )

        threads = [
            threading.Thread(target=runner_loop, daemon=True) for _ in range(runners)
        ]
        for t in threads:
            t.start()
        time.sleep(3)  # laisse les attentes s'installer
        lat, errors = [], 0
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            t0 = time.monotonic()
            try:
                ok = call("GET", "/api/v1/health/live/", timeout=30)[0] == 200
            except OSError:
                ok = False
            lat.append((time.monotonic() - t0) * 1000)
            errors += 0 if ok else 1
            time.sleep(0.1)
        stop.set()
        lat.sort()
        return {
            "slots": slots,
            "runners": runners,
            "n": len(lat),
            "errors": errors,
            "p50_ms": round(statistics.median(lat)),
            "p95_ms": round(lat[int(len(lat) * 0.95) - 1]),
            "max_ms": round(lat[-1]),
            "responses": {
                k: (sorted(v) if isinstance(v, set) else v) for k, v in counts.items()
            },
        }
    finally:
        g.terminate()
        try:
            g.wait(10)
        except subprocess.TimeoutExpired:
            g.kill()
        sql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runners", type=int, default=60)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--slots", type=int, nargs="+", default=[100, 4])
    a = ap.parse_args()
    load_env()
    for s in a.slots:
        print(
            json.dumps(measure(s, a.runners, a.seconds), ensure_ascii=False), flush=True
        )


if __name__ == "__main__":
    sys.exit(main())
