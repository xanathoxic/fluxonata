#!/usr/bin/env python3
"""AR4.6 / O3b — débit d'un worker en fonction de ORCH_WORKER_CONCURRENCY, avec le VRAI exécuteur Docker.

    .venv/bin/python scripts/loadtest_worker_concurrency.py [--actions 8] [--concurrency 1 2 4]

Base jetable `fluxonata_conc`, données de démonstration, N actions « slow-wait » (1 s de calcul) mises en file, puis un process `manage.py run_worker --roles exec`
avec la concurrence testée ; on chronomètre jusqu'à ce que tout soit terminé. Le coût dominant est le démarrage d'un conteneur : ce test dit si la concurrence le
masque SUR CETTE MACHINE (la mesure n'est pas transposable à un hôte dédié).
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = "fluxonata_conc"


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", type=int, default=8)
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4])
    a = ap.parse_args()
    load_env()
    os.environ.update(
        ORCH_DB_NAME=DB, ORCH_DEBUG="1", DJANGO_SETTINGS_MODULE="fluxonata.settings"
    )
    sys.path.insert(0, str(ROOT / "backend"))
    results = []
    for conc in a.concurrency:
        sql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        sql("postgres", f"CREATE DATABASE {DB}")
        subprocess.run(
            [sys.executable, "manage.py", "migrate", "--noinput", "-v", "0"],
            cwd=ROOT / "backend",
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [sys.executable, "manage.py", "seed_demo"],
            cwd=ROOT / "backend",
            check=True,
            capture_output=True,
        )
        code = f"""
import time
from django.contrib.auth import get_user_model
from catalog.models import ActionVersion
from runs import services
from tenancy.models import Environment, Workspace
ws = Workspace.objects.get(slug="demo"); user = get_user_model().objects.get(username="alice")
v = ActionVersion.objects.filter(action__workspace=ws, action__slug="slow-wait").first()
env = Environment.objects.get(workspace=ws, kind="test")
for _ in range({a.actions}):
    services.create_execution(workspace=ws, environment=env, action_version=v, user=user, inputs={{"seconds": 1}})
print("en file :", {a.actions})
"""
        subprocess.run(
            [sys.executable, "manage.py", "shell", "-c", code],
            cwd=ROOT / "backend",
            check=True,
            capture_output=True,
        )
        env = {
            **os.environ,
            "ORCH_WORKER_CONCURRENCY": str(conc),
            "ORCH_RUNNER": "docker",
            "ORCH_MAX_CONCURRENT_PER_WORKSPACE": "64",
        }
        t0 = time.time()
        worker = subprocess.Popen(
            [sys.executable, "manage.py", "run_worker", "--roles", "exec"],
            cwd=ROOT / "backend",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        done = failed = 0
        try:
            while time.time() - t0 < 600:
                out = subprocess.run(
                    [
                        sys.executable,
                        "manage.py",
                        "shell",
                        "-c",
                        "from runs.models import Execution as E; print(E.objects.filter(status='succeeded').count(), E.objects.filter(status='failed').count())",
                    ],
                    cwd=ROOT / "backend",
                    capture_output=True,
                    text=True,
                ).stdout.split()[-2:]
                done, failed = int(out[0]), int(out[1])
                if done + failed >= a.actions:
                    break
                time.sleep(2)
        finally:
            took = time.time() - t0
            worker.send_signal(signal.SIGTERM)
            try:
                worker.wait(60)
            except subprocess.TimeoutExpired:
                worker.kill()
        results.append(
            {
                "concurrence": conc,
                "actions": a.actions,
                "réussies": done,
                "échouées": failed,
                "durée_s": round(took, 1),
                "actions_par_min": round(done / took * 60, 1),
            }
        )
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    sql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    if len(results) > 1 and results[0]["durée_s"]:
        print(
            f"accélération {results[-1]['concurrence']} créneaux / 1 créneau : x{results[0]['durée_s'] / results[-1]['durée_s']:.2f}"
        )


if __name__ == "__main__":
    main()
