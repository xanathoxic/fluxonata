#!/usr/bin/env python3
"""M2 (AR4.4) — coût du tour de l'orchestrateur quand beaucoup de runs ATTENDENT (minuterie, approbation, signal externe).

    .venv/bin/python scripts/loadtest_orchestrator.py [--runs 100 300 500]

Base jetable `fluxonata_perf` (jamais celle de développement). Pour chaque N : N runs d'un workflow « jonction → attente de 1 h → jonction », amenés à l'état d'attente,
puis on chronomètre `orchestrator.tick()` (un passage sur TOUS les runs actifs) et on compte les requêtes SQL d'un tour. Rien n'est exécuté : c'est le coût de « ne
rien avoir à faire », répété toutes les 0,5 s. Mesuré sur la machine telle qu'elle est (pas dédiée) : un ordre de grandeur, pas une capacité.
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = "fluxonata_perf"


def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def sql(dbname, stmt):
    import psycopg

    with psycopg.connect(host=os.environ["ORCH_DB_HOST"], port=os.environ["ORCH_DB_PORT"], user=os.environ["ORCH_DB_USER"], password=os.environ["ORCH_DB_PASSWORD"], dbname=dbname, autocommit=True) as c:
        c.execute(stmt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, nargs="+", default=[100, 300, 500])
    a = ap.parse_args()
    load_env()
    sql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    sql("postgres", f"CREATE DATABASE {DB}")
    os.environ.update(ORCH_DB_NAME=DB, ORCH_DEBUG="1", DJANGO_SETTINGS_MODULE="fluxonata.settings")
    sys.path.insert(0, str(ROOT / "backend"))
    import django

    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    call_command("migrate", "--noinput", verbosity=0)
    call_command("bootstrap", "--workspace", "perf", verbosity=0)
    try:
        from django.contrib.auth import get_user_model

        from runs import engine, orchestrator
        from tenancy.models import Environment, Workspace
        from workflows.models import Workflow, WorkflowVersion
        from workflows.validation import validate
        from workflows.pinning import pin_graph  # noqa: F401  (si absent, voir plus bas)
    except ImportError:
        pin_graph = None
    from runs import engine, orchestrator
    from tenancy.models import Environment, Workspace
    from workflows.models import Workflow, WorkflowVersion

    ws = Workspace.objects.get(slug="perf")
    user = get_user_model().objects.get(username="admin") if "get_user_model" in dir() else None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(username="admin")
    graph = {
        "inputs": {"type": "object", "properties": {}, "required": []},
        "nodes": [{"id": "a", "type": "join", "name": "A"}, {"id": "w", "type": "wait", "name": "W", "seconds": 3600}, {"id": "z", "type": "join", "name": "Z"}],
        "edges": [{"from": "a", "to": "w"}, {"from": "w", "to": "z"}],
        "outputs": {},
    }
    wf = Workflow.objects.create(workspace=ws, slug="idle", name="idle")
    version = WorkflowVersion.objects.create(workflow=wf, version="1.0.0", graph=graph, input_schema=graph["inputs"], created_by=user)
    env = Environment.objects.get(workspace=ws, kind="test")
    from runs.models import WorkflowRun

    started = 0
    for n in sorted(a.runs):
        while started < n:
            engine.start_run(workspace=ws, environment=env, workflow_version=version, user=user, inputs={})
            started += 1
        for _ in range(3):  # amène chaque run à l'état d'attente
            orchestrator.tick()
        waiting = WorkflowRun.objects.filter(status="running", steps__status="waiting").distinct().count()
        times, queries = [], []
        for _ in range(7):
            with CaptureQueriesContext(connection) as q:
                t = time.perf_counter()
                orchestrator.tick()
                times.append((time.perf_counter() - t) * 1000)
            queries.append(len(q))
        print(json.dumps({"runs_actifs": n, "en_attente": waiting, "tick_ms_médian": round(statistics.median(times)), "tick_ms_max": round(max(times)), "requêtes_par_tour": queries[-1], "requêtes_par_run": round(queries[-1] / n, 1)}, ensure_ascii=False), flush=True)
    connection.close()
    sql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")


if __name__ == "__main__":
    main()
