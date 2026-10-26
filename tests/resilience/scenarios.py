#!/usr/bin/env python3
"""Real-process resilience scenarios against the dev stack (stdlib only).

Kills services with SIGKILL / restarts PostgreSQL, then checks that no confirmed step is repeated and that an
uncertain external effect is surfaced for a human decision. Run via scripts/resilience.sh.
Never runs `wsl --shutdown` (that must be done by the user).
"""
import http.cookiejar
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = f"http://127.0.0.1:{os.environ.get('ORCH_WEB_PORT', '5173')}/api/v1"
WS = "demo"
RUN = f"{ROOT}/.run"
results = []


def sh(cmd, **env):
    return subprocess.run(cmd, shell=True, cwd=ROOT, env={**os.environ, **env}, capture_output=True, text=True)


class Api:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "csrftoken"), "")

    def call(self, method, path, body=None):
        if not self.csrf():
            self.op.open(f"{BASE}/auth/csrf/").read()
        req = urllib.request.Request(f"{BASE}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-CSRFToken": self.csrf(), "Origin": BASE.split("/api")[0]})
        try:
            with self.op.open(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code} {e.read()[:300]}") from None

    def login(self, user="alice"):
        self.call("POST", "/auth/login/", {"username": user, "password": "demo-fluxonata"})


def kill(name, sig=signal.SIGKILL):
    path = f"{RUN}/{name}.pid"
    if not os.path.exists(path):
        return
    pid = int(open(path).read())
    sh(f"pkill -{int(sig)} -P {pid}")
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    time.sleep(0.5)
    os.remove(path)


def stack_up(lease=None):
    env = {"ORCH_LEASE_SECONDS": str(lease)} if lease else {}
    out = sh("scripts/dev-up.sh", **env)
    assert out.returncode == 0, out.stderr[-500:]
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{BASE}/auth/csrf/", timeout=2).read()
            return
        except Exception:  # noqa: BLE001
            time.sleep(1)
    raise RuntimeError("API did not come back")


def publish(api, slug, nodes, edges, inputs=None):
    api.call("POST", f"/workspaces/{WS}/workflows/", {"slug": slug, "name": slug})
    graph = {"inputs": inputs or {"type": "object", "properties": {}, "required": []}, "nodes": nodes, "edges": edges, "outputs": {}}
    api.call("PUT", f"/workspaces/{WS}/workflows/{slug}/draft/", {"graph": graph, "revision": 1})
    api.call("POST", f"/workspaces/{WS}/workflows/{slug}/publish/", {})


def start(api, slug):
    return api.call("POST", f"/workspaces/{WS}/workflows/{slug}/runs/", {"environment": "test", "inputs": {}})["id"]


def get(api, rid):
    return api.call("GET", f"/workspaces/{WS}/workflow-runs/{rid}/")


def wait_for(api, rid, pred, timeout=90, label=""):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = get(api, rid)
            if pred(last):
                return last
        except Exception:  # noqa: BLE001 - API/DB may be mid-restart
            pass
        time.sleep(1)
    raise AssertionError(f"timeout waiting for {label}; last status={last and last['status']}")


def step(run, nid):
    return next(s for s in run["steps"] if s["node_id"] == nid)


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'OK' if ok else 'KO'}] {name} {detail}", flush=True)


def scenario_a(api, ts):
    """Spec §13: finished action + durable wait + external-effect step; hard-kill everything during the wait."""
    slug = f"resil-a-{ts}"
    publish(api, slug, [
        {"id": "analyse", "type": "action", "name": "Analyse", "action": "text-stats", "inputs": {"text": "un deux trois"}},
        {"id": "pause", "type": "wait", "name": "Pause", "seconds": 15},
        {"id": "notifier", "type": "action", "name": "Notifier", "action": "send-notification", "inputs": {"recipient": "x@example.org", "seconds": 1}},
    ], [{"from": "analyse", "to": "pause"}, {"from": "pause", "to": "notifier"}])
    rid = start(api, slug)
    wait_for(api, rid, lambda r: any(s["node_id"] == "pause" and s["status"] == "waiting" for s in r["steps"]), label="wait started")
    for n in ("web", "worker", "orchestrator", "api"):
        kill(n)
    time.sleep(18)  # the wait expires while everything is down
    stack_up()
    run = wait_for(api, rid, lambda r: r["status"] in ("succeeded", "failed"), timeout=120, label="completion after restart")
    started = [e for e in run["events"] if e["type"] == "step_started" and e["node_id"] == "analyse"]
    ok = run["status"] == "succeeded" and len(started) == 1 and step(run, "analyse")["attempt"] == 1 and step(run, "notifier")["attempt"] == 1
    record("A: arrêt brutal de tous les services pendant une attente", ok, f"status={run['status']} analyse_started={len(started)}")


def scenario_b(api, ts):
    """Worker killed during a non-idempotent external action -> uncertain, human decision, never blind re-run."""
    slug = f"resil-b-{ts}"
    publish(api, slug, [{"id": "notifier", "type": "action", "name": "Notifier", "action": "send-notification",
                         "inputs": {"recipient": "y@example.org", "seconds": 40}}], [])
    rid = start(api, slug)
    wait_for(api, rid, lambda r: any(s["node_id"] == "notifier" and s["status"] == "running" for s in r["steps"]), label="external action running")
    time.sleep(3)
    kill("worker")
    run = wait_for(api, rid, lambda r: r["needs_decision"], timeout=60, label="uncertain outcome")  # lease expires (6 s)
    stack_up(lease=6)  # worker back
    time.sleep(6)
    still = get(api, rid)
    no_rerun = step(still, "notifier")["attempt"] == 1 and step(still, "notifier")["status"] == "uncertain"
    api.call("POST", f"/workspaces/{WS}/workflow-runs/{rid}/steps/notifier/decision/", {"decision": "succeed", "output": {"delivered": True, "recipient": "y@example.org"}})
    final = wait_for(api, rid, lambda r: r["status"] in ("succeeded", "failed"), label="decision applied")
    record("B: worker tué pendant un effet externe non idempotent", run["needs_decision"] and no_rerun and final["status"] == "succeeded",
           f"uncertain={run['needs_decision']} not_rerun={no_rerun} final={final['status']}")


def scenario_c(api, ts):
    """Worker killed during an idempotent action -> lease expires, safe automatic retry."""
    slug = f"resil-c-{ts}"
    publish(api, slug, [{"id": "attente", "type": "action", "name": "Attente", "action": "slow-wait", "inputs": {"seconds": 8}}], [])
    rid = start(api, slug)
    wait_for(api, rid, lambda r: any(s["node_id"] == "attente" and s["status"] == "running" for s in r["steps"]), label="running")
    time.sleep(2)
    kill("worker")
    time.sleep(8)  # lease (6 s) expires; orchestrator reconciles and re-queues
    stack_up(lease=6)
    run = wait_for(api, rid, lambda r: r["status"] in ("succeeded", "failed"), timeout=90, label="completion")
    record("C: worker tué pendant une action idempotente (reprise automatique)", run["status"] == "succeeded", f"status={run['status']}")


def scenario_d(api, ts):
    """PostgreSQL restarted while a run is waiting: services reconnect, run completes."""
    slug = f"resil-d-{ts}"
    publish(api, slug, [{"id": "pause", "type": "wait", "name": "Pause", "seconds": 12},
                        {"id": "fin", "type": "action", "name": "Fin", "action": "text-stats", "inputs": {"text": "base de données"}}],
            [{"from": "pause", "to": "fin"}])
    rid = start(api, slug)
    wait_for(api, rid, lambda r: any(s["node_id"] == "pause" and s["status"] == "waiting" for s in r["steps"]), label="waiting")
    out = sh("docker restart fluxonata-db-1")
    assert out.returncode == 0, out.stderr
    run = wait_for(api, rid, lambda r: r["status"] in ("succeeded", "failed"), timeout=120, label="completion after db restart")
    record("D: redémarrage de PostgreSQL pendant une exécution", run["status"] == "succeeded", f"status={run['status']}")


def main():
    ts = int(time.time())
    sh("scripts/dev-down.sh")
    stack_up(lease=6)
    api = Api()
    api.login()
    try:
        for fn in (scenario_a, scenario_b, scenario_c, scenario_d):
            try:
                fn(api, ts)
            except Exception as exc:  # noqa: BLE001
                record(fn.__name__, False, repr(exc)[:300])
            if fn is scenario_a:
                api = Api()
                api.login()
    finally:
        sh("scripts/dev-down.sh")
        stack_up()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} scénarios réussis")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
