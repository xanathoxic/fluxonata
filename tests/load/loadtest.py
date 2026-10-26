#!/usr/bin/env python3
"""Load test (stdlib only). Uses the dev stack's PostgreSQL + demo data."""

import http.cookiejar
import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PORT = 8200
BASE = f"http://127.0.0.1:{PORT}/api/v1"
WS = "demo"
PY = f"{ROOT}/.venv/bin/python"
procs: list[subprocess.Popen] = []


def env():
    e = {
        **os.environ,
        "ORCH_API_RATE": "1000000/min",
        "ORCH_LOGIN_RATE": "1000/min",
        "ORCH_LOG_FORMAT": "text",
    }
    for line in open(f"{ROOT}/.env"):
        if "=" in line and not line.startswith("#"):
            k, _, v = line.strip().partition("=")
            e.setdefault(k, v)
    return e


def spawn(cmd, **kw):
    p = subprocess.Popen(
        cmd,
        cwd=f"{ROOT}/backend",
        env=env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        **kw,
    )
    procs.append(p)
    return p


def stop(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(10)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.op.open(f"{BASE}/auth/csrf/").read()
        self.call(
            "POST", "/auth/login/", {"username": "alice", "password": "demo-fluxonata"}
        )

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "csrftoken"), "")

    def call(self, method, path, body=None):
        req = urllib.request.Request(
            f"{BASE}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-CSRFToken": self.csrf(),
                "Origin": f"http://127.0.0.1:{PORT}",
            },
        )
        t0 = time.perf_counter()
        try:
            with self.op.open(req, timeout=60) as r:
                raw = r.read()
                return (
                    r.status,
                    (json.loads(raw) if raw else None),
                    time.perf_counter() - t0,
                )
        except urllib.error.HTTPError as e:
            return e.code, None, time.perf_counter() - t0


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


def run_load(n, workers, fn):
    clients = [Client() for _ in range(workers)]
    lat, errors, lock = [], [], threading.Lock()
    idx = iter(range(n))

    def worker(c):
        while True:
            with lock:
                i = next(idx, None)
            if i is None:
                return
            status, _, dt = fn(c, i)
            with lock:
                lat.append(dt)
                if status not in (200, 202):
                    errors.append(status)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(worker, clients))
    return lat, errors, time.perf_counter() - t0


def report(name, lat, errors, dur, ok):
    print(
        f"[{'OK' if ok else 'KO'}] {name}: n={len(lat)} erreurs={len(errors)} durée={dur:.1f}s débit={len(lat) / dur:.0f}/s "
        f"p50={statistics.median(lat) * 1000:.0f}ms p95={pct(lat, 0.95) * 1000:.0f}ms max={max(lat) * 1000:.0f}ms",
        flush=True,
    )
    return ok


def main():
    results = []
    api = spawn(
        [
            f"{ROOT}/.venv/bin/gunicorn",
            "fluxonata.wsgi",
            "-w",
            "4",
            "-b",
            f"127.0.0.1:{PORT}",
            "--timeout",
            "60",
        ]
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{BASE}/health/live/", timeout=1).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    try:
        lat, err, dur = run_load(
            2000, 20, lambda c, i: c.call("GET", f"/workspaces/{WS}/actions/")
        )
        results.append(
            report(
                "O1 lecture catalogue (20 clients)",
                lat,
                err,
                dur,
                not err and pct(lat, 0.95) < 0.3,
            )
        )

        main_client = Client()
        slug = f"load-{int(time.time())}"
        main_client.call(
            "POST", f"/workspaces/{WS}/workflows/", {"slug": slug, "name": slug}
        )
        graph = {
            "inputs": {"type": "object", "properties": {}, "required": []},
            "edges": [],
            "outputs": {},
            "nodes": [
                {
                    "id": "a",
                    "type": "action",
                    "name": "A",
                    "action": "text-stats",
                    "inputs": {"text": "un deux trois"},
                }
            ],
        }
        main_client.call(
            "PUT",
            f"/workspaces/{WS}/workflows/{slug}/draft/",
            {"graph": graph, "revision": 1},
        )
        status, _, _ = main_client.call(
            "POST", f"/workspaces/{WS}/workflows/{slug}/publish/", {}
        )
        assert status == 201, "publication du workflow de charge impossible"
        start = lambda c, i: c.call(
            "POST",
            f"/workspaces/{WS}/workflows/{slug}/runs/",
            {"environment": "test", "inputs": {}},
        )  # noqa: E731
        run_load(10, 5, start)  # warm-up: cold gunicorn workers / DB connections are not what O2 measures
        lat, err, dur = run_load(60, 10, start)
        results.append(
            report(
                "O2 soumission de workflows (10 clients)",
                lat,
                err,
                dur,
                not err and pct(lat, 0.95) < 0.5,
            )
        )

        # O3: drain queue first (the 200 runs of O2 are already queued) so that measurements start from an empty system
        orch = spawn([PY, "manage.py", "run_orchestrator"])
        drain = [spawn([PY, "manage.py", "run_worker"]) for _ in range(4)]
        wait_idle(
            main_client
        )  # the 200 runs of O2 are processed here, outside the measurement
        for p in drain:
            stop(p)
        throughputs = {}
        for n_workers in (1, 4):
            ws_procs = [
                spawn([PY, "manage.py", "run_worker"]) for _ in range(n_workers)
            ]
            wait_idle(main_client)
            ids, t0 = submit_batch(main_client, slug, 24), time.perf_counter()
            done = wait_done(main_client, ids)
            dur = time.perf_counter() - t0
            ok = sum(1 for r in done.values() if r == "succeeded")
            execs = {rid: count_execs(main_client, rid) for rid in ids}
            exact = all(v == 1 for v in execs.values())
            throughputs[n_workers] = len(ids) / dur
            print(
                f"    {n_workers} worker(s): {ok}/{len(ids)} réussis en {dur:.1f}s = {len(ids) / dur * 60:.0f} runs/min ; 1 exécution par run: {exact}",
                flush=True,
            )
            results.append(ok == len(ids) and exact)
            for p in ws_procs:
                stop(p)
        ratio = throughputs[4] / throughputs[1]
        print(
            f"[{'OK' if ratio >= 1.8 else 'KO'}] O3 passage à l'échelle 1→4 workers : ×{ratio:.2f} (objectif ≥ 1,8)"
        )
        results.append(ratio >= 1.8)
        stop(orch)
    finally:
        for p in procs:
            stop(p)
    print(
        "\nObjectifs atteints."
        if all(results)
        else "\nCertains objectifs ne sont PAS atteints (voir ci-dessus)."
    )
    sys.exit(0 if all(results) else 1)


def wait_idle(c, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        _, body, _ = c.call("GET", f"/workspaces/{WS}/workflow-runs/?status=running")
        if body is not None and not body["results"]:
            return
        time.sleep(2)
    raise RuntimeError("system never became idle")


def submit_batch(c, slug, n):
    ids = []
    for _ in range(n):
        status, body, _ = c.call(
            "POST",
            f"/workspaces/{WS}/workflows/{slug}/runs/",
            {"environment": "test", "inputs": {}},
        )
        assert status == 202
        ids.append(body["id"])
    return ids


def wait_done(c, ids, timeout=900):
    end, state = time.time() + timeout, {}
    pending = set(ids)
    while pending and time.time() < end:
        for rid in list(pending):
            _, body, _ = c.call("GET", f"/workspaces/{WS}/workflow-runs/{rid}/")
            if body and body["status"] in ("succeeded", "failed", "cancelled"):
                state[rid] = body["status"]
                pending.discard(rid)
        time.sleep(0.5)
    return state


def count_execs(c, rid):
    _, body, _ = c.call("GET", f"/workspaces/{WS}/workflow-runs/{rid}/")
    return sum(1 for s in body["steps"] if s["execution_id"]) if body else -1


if __name__ == "__main__":
    main()
