#!/usr/bin/env python3
"""Real-process resilience scenarios against the dev stack (stdlib only).

Kills services with SIGKILL / restarts PostgreSQL, then checks that no confirmed step is repeated and that an
uncertain external effect is surfaced for a human decision. Run via scripts/resilience.sh.
Never runs `wsl --shutdown` (that must be done by the user).
"""
import concurrent.futures
import hashlib
import hmac
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


# ----------------------------------------------------------------------------------------------------------------------
# AR0.4 — scénarios ajoutés avant de toucher aux rôles de déploiement (docs/ROADMAP_ARCHITECTURE.md). Seuils fixés AVANT la mesure.
# E, G et H n'utilisent QUE des étapes exécutées par l'orchestrateur (jonction, attente) : le worker Docker traite ≈ 1 action / 8 s sur cette machine
# (O3b), ce qui noierait ce qu'on veut mesurer. Une première version avec des actions conteneurisées avait manqué le budget de H pour cette raison.
#   E  orchestrateur tué seul     : run terminé <= 30 s après son retour, aucune étape rejouée
#   F  API tuée seule             : run terminé API arrêtée ; 1re réponse 200 <= 15 s après son retour
#   G  PostgreSQL arrêté en rafale de webhooks : 30 runs exactement (0 perdu, 0 doublon) ; 1er webhook accepté <= 20 s après le retour de la base
#   H  deux orchestrateurs        : 12 runs de 3 étapes terminés <= 60 s, chaque étape démarrée exactement 1 fois (même si l'un est tué)
E_RECOVERY, F_RECOVERY, G_RECOVERY, H_BUDGET = 30, 15, 20, 60


def started_counts(run):
    out = {}
    for e in run["events"]:
        if e["type"] == "step_started":
            out[e["node_id"]] = out.get(e["node_id"], 0) + 1
    return out


def scenario_e(api, ts):
    """Orchestrator killed ALONE while a run waits: nothing advances until it is back, then the run completes without repeating a step."""
    slug = f"resil-e-{ts}"
    publish(api, slug, [
        {"id": "debut", "type": "join", "name": "Début"},
        {"id": "pause", "type": "wait", "name": "Pause", "seconds": 6},
        {"id": "fin", "type": "join", "name": "Fin"},
    ], [{"from": "debut", "to": "pause"}, {"from": "pause", "to": "fin"}])
    rid = start(api, slug)
    wait_for(api, rid, lambda r: any(s["node_id"] == "pause" and s["status"] == "waiting" for s in r["steps"]), label="attente")
    kill("orchestrator")
    time.sleep(9)  # l'attente expire pendant que l'orchestrateur est absent
    frozen = get(api, rid)["status"] == "running"  # personne n'a avancé le run
    t0 = time.time()
    stack_up(lease=6)
    run = wait_for(api, rid, lambda r: r["status"] in ("succeeded", "failed"), timeout=90, label="fin après retour de l'orchestrateur")
    took = time.time() - t0
    counts = started_counts(run)
    ok = run["status"] == "succeeded" and frozen and took <= E_RECOVERY and all(v == 1 for v in counts.values()) and set(counts) == {"debut", "pause", "fin"}
    record("E: orchestrateur tué seul pendant une attente", ok, f"gelé_pendant_l'absence={frozen} terminé_en={took:.0f}s (<= {E_RECOVERY}) départs={counts}")


def scenario_f(api, ts):
    """API killed ALONE: execution does not need it (orchestrator + worker + database only); it comes back fast."""
    slug = f"resil-f-{ts}"
    publish(api, slug, [
        {"id": "pause", "type": "wait", "name": "Pause", "seconds": 5},
        {"id": "fin", "type": "action", "name": "Fin", "action": "text-stats", "inputs": {"text": "sans api"}},
    ], [{"from": "pause", "to": "fin"}])
    rid = start(api, slug)
    wait_for(api, rid, lambda r: any(s["node_id"] == "pause" and s["status"] == "waiting" for s in r["steps"]), label="attente")
    kill("api")
    time.sleep(14)  # l'exécution continue sans API
    t0 = time.time()
    stack_up()
    first_200 = time.time() - t0
    run = wait_for(api, rid, lambda r: r["status"] in ("succeeded", "failed"), timeout=30, label="état après retour de l'API")
    finished = run.get("finished_at") or ""
    # le run s'est terminé pendant l'absence de l'API : sa date de fin précède le moment où l'API est revenue
    back_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0))
    done_while_down = bool(finished) and finished[:19] < back_at
    ok = run["status"] == "succeeded" and done_while_down and first_200 <= F_RECOVERY
    record("F: API tuée seule (l'exécution continue)", ok, f"terminé_API_arrêtée={done_while_down} 1re_réponse_en={first_200:.0f}s (<= {F_RECOVERY}) fin={finished[:19]} retour={back_at}")


def _post_webhook(url, secret, payload, delivery):
    raw = json.dumps(payload).encode()
    t = str(int(time.time()))
    sig = hmac.new(secret.encode(), t.encode() + b"." + raw, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=raw, method="POST", headers={
        "Content-Type": "application/json", "X-Fluxonata-Timestamp": t, "X-Fluxonata-Signature": f"v1={sig}", "X-Fluxonata-Delivery": delivery})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:  # noqa: BLE001 - connexion refusée, délai dépassé… : l'expéditeur réessaiera
        return 0, {}


def scenario_g(api, ts):
    """PostgreSQL STOPPED (not restarted) in the middle of a webhook burst; the sender retries each failed delivery with the same id.
    Exactly one run per delivery: none lost, none duplicated."""
    slug, n_events = f"resil-g-{ts}", 30
    publish(api, slug, [{"id": "a", "type": "join", "name": "A"}], [],
            inputs={"type": "object", "properties": {"n": {"type": "string"}}, "required": ["n"]})
    hook = api.call("POST", f"/workspaces/{WS}/webhooks/", {"name": f"resil-g-{ts}", "workflow": slug, "environment": "test"})
    url = BASE.split("/api")[0] + hook["url"] if hook["url"].startswith("/") else hook["url"]
    acked_at, attempts = {}, {}

    def sender(i):  # un expéditeur réaliste : réessaie la MÊME livraison jusqu'à un 2xx
        delivery = f"g-{ts}-{i}"
        end = time.time() + 120
        while time.time() < end:
            attempts[i] = attempts.get(i, 0) + 1
            status, _ = _post_webhook(url, hook["secret"], {"n": str(i)}, delivery)
            if 200 <= status < 300:
                acked_at[i] = time.time()
                return
            time.sleep(1.5)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
    futures = []
    for i in range(n_events):
        futures.append(pool.submit(sender, i))
        time.sleep(0.3)
        if i == 8:
            sh("docker stop fluxonata-db-1")  # arrêt réel de la base, au milieu de la rafale
            t_stop = time.time()
        if i == 20:
            t_up = time.time()
            sh("docker start fluxonata-db-1")
    for f in futures:
        f.result()
    pool.shutdown()
    after_up = [t for t in acked_at.values() if t >= t_up]
    first_ok = (min(after_up) - t_up) if after_up else 999
    time.sleep(4)
    stack_up()
    end = time.time() + 60
    while True:  # les 30 runs doivent aussi TERMINER (pas seulement exister)
        runs = [r for r in api.call("GET", f"/workspaces/{WS}/workflow-runs/?limit=200")["results"] if r["workflow"]["slug"] == slug]
        if (len(runs) >= n_events and all(r["status"] in ("succeeded", "failed") for r in runs)) or time.time() > end:
            break
        time.sleep(2)
    seen = sorted(int(r["inputs"]["n"]) for r in runs)
    retried = sum(1 for v in attempts.values() if v > 1)
    done = sum(r["status"] == "succeeded" for r in runs)
    ok = seen == list(range(n_events)) and len(acked_at) == n_events and done == n_events and first_ok <= G_RECOVERY
    record("G: PostgreSQL arrêté pendant une rafale de webhooks", ok,
           f"acquittés={len(acked_at)}/{n_events} runs={len(runs)} distincts={len(set(seen))} réussis={done} réessayés={retried} 1er_accepté_après_retour={first_ok:.0f}s (<= {G_RECOVERY}) base_arrêtée={t_up - t_stop:.0f}s")


def scenario_h(api, ts):
    """Two orchestrators at once, one of them SIGKILLed on the way: every step is started exactly once and every run completes."""
    slug, n_runs = f"resil-h-{ts}", 12
    publish(api, slug, [
        {"id": "un", "type": "join", "name": "Un"},
        {"id": "pause", "type": "wait", "name": "Pause", "seconds": 8},
        {"id": "deux", "type": "join", "name": "Deux"},
    ], [{"from": "un", "to": "pause"}, {"from": "pause", "to": "deux"}])
    n_extra = int(os.environ.get("H_ORCHESTRATORS", "3"))  # 3 en plus de celui de la pile = 4 orchestrateurs simultanés (AR4.1)
    extras = [subprocess.Popen([f"{ROOT}/.venv/bin/python", "manage.py", "run_orchestrator"], cwd=f"{ROOT}/backend",
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True) for _ in range(n_extra)]
    second = extras[0]
    try:
        time.sleep(3)
        t0 = time.time()
        ids = [start(api, slug) for _ in range(n_runs)]
        time.sleep(2)  # les 12 runs sont en attente (8 s) : il reste du travail à faire pour celui qui survit
        mid_flight = sum(get(api, rid)["status"] == "running" for rid in ids)
        for victim in extras[: max(1, n_extra // 2)]:  # la moitié des orchestrateurs supplémentaires meurent en pleine exécution
            os.killpg(victim.pid, signal.SIGKILL)
            victim.wait()
        runs = [wait_for(api, rid, lambda r: r["status"] in ("succeeded", "failed"), timeout=H_BUDGET + 30, label=f"run {rid[:8]}") for rid in ids]
        took = time.time() - t0
    finally:
        for p in extras:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
    dup = [(r["id"][:8], started_counts(r)) for r in runs if any(v != 1 for v in started_counts(r).values()) or set(started_counts(r)) != {"un", "pause", "deux"}]
    ok = all(r["status"] == "succeeded" for r in runs) and not dup and took <= H_BUDGET and mid_flight == n_runs
    record("H: quatre orchestrateurs, un tué en route", ok, f"runs={n_runs} en_cours_au_moment_du_kill={mid_flight} réussis={sum(r['status'] == 'succeeded' for r in runs)} durée={took:.0f}s (<= {H_BUDGET}) anomalies_de_départ={dup[:2]}")


def main():
    ts = int(time.time())
    sh("scripts/dev-down.sh")
    stack_up(lease=6)
    api = Api()
    api.login()
    try:
        every = (scenario_a, scenario_b, scenario_c, scenario_d, scenario_e, scenario_f, scenario_g, scenario_h)
        wanted = {a.lower() for a in sys.argv[1:]}  # ex. : scenarios.py E G H (aucun argument = tous)
        for fn in [f for f in every if not wanted or f.__name__[-1] in wanted]:
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
