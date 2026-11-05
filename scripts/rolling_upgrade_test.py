#!/usr/bin/env python3
"""AR6.1 / S12 — mise à jour PROGRESSIVE sous charge, topologie par rôles, ancien code → nouveau code.

    .venv/bin/python scripts/rolling_upgrade_test.py --old-ref HEAD [--replicas 2] [--keep]

Déroulé : (1) construit l'image de l'ANCIEN code (`git archive <réf>`) et celle du code courant ; (2) démarre la pile par rôles avec l'ancien code ; (3) lance une charge
continue — des webhooks signés (l'expéditeur réessaie la MÊME livraison jusqu'à un 2xx) et des lectures authentifiées de l'interface ; (4) applique les migrations du nouveau
code PENDANT que l'ancien sert ; (5) bascule un rôle après l'autre (workers, runner-gw, orchestrateur, api-ingest, api-ui) vers le nouveau code ; (6) laisse la charge
finir et vérifie les comptes dans la base.

SEUILS FIXÉS AVANT LA MESURE (jamais ajustés après) :
  - 0 livraison acquittée perdue (chaque livraison acquittée a EXACTEMENT 1 run) et 0 doublon ;
  - tous les runs créés se terminent « succeeded » ;
  - avec --replicas 2 : lectures de l'interface en erreur ≤ 2 % du total et aucune coupure continue de plus de 8 s ;
  - avec --replicas 1 : on RAPPORTE les coupures (un rôle à un seul réplica est indisponible pendant son redémarrage), sans seuil.
Limites : machine de développement partagée ; l'ancien code (sans gestionnaire SIGTERM) n'obéit pas à l'arrêt gracieux, donc chaque redémarrage de ses conteneurs attend le
`stop_grace_period` ; un seul scénario de charge, une seule exécution.
"""

import argparse
import contextlib
import hashlib
import hmac
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("ORCH_HTTP_PORT", "8080"))
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "prod-smoke-pass-123"
OLD = {
    "ORCH_BACKEND_IMAGE": "fluxonata-backend:old",
    "ORCH_WORKER_IMAGE": "fluxonata-worker:old",
}
NEW = {
    "ORCH_BACKEND_IMAGE": "fluxonata-backend:new",
    "ORCH_WORKER_IMAGE": "fluxonata-worker:new",
}
ORDER = [
    "worker-aux",
    "worker-exec",
    "runner-gw",
    "orchestrator",
    "api-ingest",
    "api-ui",
]


def sh(cmd, env=None, check=True, **kw):
    r = subprocess.run(
        cmd,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        **kw,
    )
    if check and r.returncode:
        raise RuntimeError(
            f"{' '.join(map(str, cmd))[:120]} -> {r.returncode}\n{r.stderr[-600:]}"
        )
    return r


def compose(*args, env=None, check=True):
    files = [
        "-f",
        "infra/docker-compose.prod.yml",
        "-f",
        "infra/docker-compose.roles.yml",
        "--env-file",
        "infra/.env.prod",
    ]
    return sh(["docker", "compose", *files, *args], env=env, check=check)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------------------------------------ images
def build_images(old_ref):
    tmp = Path(tempfile.mkdtemp(prefix="old-src-"))
    sh(
        [
            "bash",
            "-c",
            f"git archive {old_ref} -- . ':(exclude)frontend' | tar -x -C {tmp}",
        ]
    )
    dockerfile = (tmp / "infra" / "Dockerfile.backend").read_text()
    log(f"image de l'ancien code ({old_ref}) : construction")
    if " AS runtime" in dockerfile:
        sh(
            [
                "docker",
                "build",
                "-q",
                "-f",
                "infra/Dockerfile.backend",
                "--target",
                "runtime",
                "-t",
                OLD["ORCH_BACKEND_IMAGE"],
                str(tmp),
            ]
        )
        sh(
            [
                "docker",
                "build",
                "-q",
                "-f",
                "infra/Dockerfile.backend",
                "--target",
                "exec",
                "-t",
                OLD["ORCH_WORKER_IMAGE"],
                str(tmp),
            ]
        )
    else:  # ancienne image monolithique : la même pour tous les rôles (avec le CLI Docker)
        sh(
            [
                "docker",
                "build",
                "-q",
                "-f",
                "infra/Dockerfile.backend",
                "-t",
                OLD["ORCH_BACKEND_IMAGE"],
                str(tmp),
            ]
        )
        sh(["docker", "tag", OLD["ORCH_BACKEND_IMAGE"], OLD["ORCH_WORKER_IMAGE"]])
    log("image du code courant : construction")
    sh(
        [
            "docker",
            "build",
            "-q",
            "-f",
            "infra/Dockerfile.backend",
            "--target",
            "runtime",
            "-t",
            NEW["ORCH_BACKEND_IMAGE"],
            ".",
        ]
    )
    sh(
        [
            "docker",
            "build",
            "-q",
            "-f",
            "infra/Dockerfile.backend",
            "--target",
            "exec",
            "-t",
            NEW["ORCH_WORKER_IMAGE"],
            ".",
        ]
    )
    compose("build", "web", env=OLD)


# ------------------------------------------------------------------------------------------------------------------ client
class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "csrftoken"), "")

    def call(self, method, path, body=None, timeout=15):
        if not self.csrf():
            self.op.open(f"{BASE}/api/v1/auth/csrf/", timeout=timeout).read()
        req = urllib.request.Request(
            f"{BASE}/api/v1{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "X-CSRFToken": self.csrf(), "Origin": BASE},
        )  # fmt: skip
        with self.op.open(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None


def post_webhook(url, secret, payload, delivery):
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode(), ts.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    req = urllib.request.Request(url, data=raw, method="POST", headers={"Content-Type": "application/json", "X-Fluxonata-Timestamp": ts, "X-Fluxonata-Signature": f"v1={sig}", "X-Fluxonata-Delivery": delivery})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


# ------------------------------------------------------------------------------------------------------------------ charge
class Load:
    def __init__(self, url, secret):
        self.url, self.secret = url, secret
        self.stop = threading.Event()
        self.acked: dict[int, float] = {}
        self.reads: list[tuple[float, int, float]] = []  # (instant, code, durée)
        self.lock = threading.Lock()
        self.threads = [
            threading.Thread(target=self.webhooks, daemon=True),
            threading.Thread(target=self.readers, daemon=True),
        ]

    def webhooks(self):
        n = 0
        while not self.stop.is_set():
            n += 1
            end = time.time() + 60
            while time.time() < end:
                if (
                    200
                    <= post_webhook(self.url, self.secret, {"n": str(n)}, f"up-{n}")
                    < 300
                ):
                    with self.lock:
                        self.acked[n] = time.time()
                    break
                time.sleep(0.5)  # l'expéditeur réessaie la MÊME livraison
            time.sleep(0.25)

    def readers(self):
        c = Client()
        with contextlib.suppress(Exception):
            c.call("POST", "/auth/login/", {"username": "alice", "password": PASSWORD})
        while not self.stop.is_set():
            t = time.time()
            try:
                c.call("GET", "/workspaces/demo/actions/", timeout=8)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            except Exception:  # noqa: BLE001
                code = 0
            with self.lock:
                self.reads.append((t, code, time.time() - t))
            time.sleep(0.2)

    def start(self):
        [t.start() for t in self.threads]

    def finish(self):
        self.stop.set()
        [t.join(timeout=70) for t in self.threads]


def roll_replicas(svc, replicas, timeout=240):
    """Bascule PAR ÉTAPES d'un service à plusieurs réplicas, avec garde de santé (ce que `docker compose up` seul ne fait pas : il recrée les réplicas l'un après l'autre
    SANS attendre que le nouveau soit prêt). 1) démarrer `replicas` conteneurs du nouveau code À CÔTÉ des anciens (`--no-recreate`) ; 2) attendre qu'ils soient sains ;
    3) arrêter proprement les anciens (arrêt gracieux : ils finissent leurs requêtes). Retourne True si les nouveaux sont sains."""
    old_ids = compose("ps", "-q", svc, check=False).stdout.split()
    compose(
        "up",
        "-d",
        "--no-build",
        "--no-deps",
        "--no-recreate",
        f"--scale={svc}={2 * replicas}",
        svc,
        env=NEW,
    )
    end = time.time() + timeout
    new_ids = []
    while time.time() < end:
        ids = [
            i
            for i in compose("ps", "-q", svc, check=False).stdout.split()
            if i not in old_ids
        ]
        healthy = [
            i
            for i in ids
            if sh(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", i], check=False
            ).stdout.strip()
            == "healthy"
        ]
        if len(healthy) >= replicas:
            new_ids = healthy
            break
        time.sleep(2)
    for oid in old_ids:
        sh(["docker", "stop", "-t", "30", oid], check=False)
        sh(["docker", "rm", oid], check=False)
    return bool(new_ids)


def wait_healthy(service, timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        ids = compose("ps", "-q", service, check=False).stdout.split()
        states = [
            sh(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", i], check=False
            ).stdout.strip()
            for i in ids
        ]
        if ids and all(s == "healthy" for s in states):
            return True
        time.sleep(3)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-ref", default="HEAD")
    ap.add_argument("--replicas", type=int, default=2)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument(
        "--naive",
        action="store_true",
        help="recréer les réplicas avec `docker compose up` seul (sans garde de santé) : la 1re mesure",
    )
    a = ap.parse_args()
    sh([str(ROOT / "scripts" / "prod-init.sh")])
    env_prod = dict(
        line.split("=", 1)
        for line in (ROOT / "infra" / ".env.prod").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    os.environ.update({k: v for k, v in env_prod.items() if k not in os.environ})
    scale = [f"--scale=api-ui={a.replicas}", f"--scale=api-ingest={a.replicas}"]
    compose("down", "-v", "--remove-orphans", env=OLD, check=False)
    build_images(a.old_ref)
    verdict = {}
    try:
        log(
            f"démarrage avec l'ANCIEN code ({a.old_ref}), {a.replicas} réplica(s) pour api-ui et api-ingest"
        )
        compose("up", "-d", "--no-build", *scale, env=OLD)
        for _ in range(90):
            if (
                sh(
                    ["curl", "-fs", f"{BASE}/api/v1/health/ready/"], check=False
                ).returncode
                == 0
            ):
                break
            time.sleep(2)
        compose(
            "exec",
            "-T",
            "-e",
            f"ADMIN_PW={PASSWORD}",
            "api-ui",
            "python",
            "manage.py",
            "bootstrap",
            "--workspace",
            "principal",
            "--password-env",
            "ADMIN_PW",
            env=OLD,
        )
        compose(
            "exec",
            "-T",
            "api-ui",
            "python",
            "manage.py",
            "seed_demo",
            "--password",
            PASSWORD,
            env=OLD,
        )
        c = Client()
        c.call("POST", "/auth/login/", {"username": "alice", "password": PASSWORD})
        slug = f"upg-{int(time.time())}"
        c.call("POST", "/workspaces/demo/workflows/", {"slug": slug, "name": slug})
        graph = {
            "inputs": {
                "type": "object",
                "properties": {"n": {"type": "string"}},
                "required": ["n"],
            },
            "nodes": [{"id": "a", "type": "join", "name": "A"}],
            "edges": [],
            "outputs": {},
        }
        c.call(
            "PUT",
            f"/workspaces/demo/workflows/{slug}/draft/",
            {"graph": graph, "revision": 1},
        )
        c.call("POST", f"/workspaces/demo/workflows/{slug}/publish/", {})
        hook = c.call(
            "POST",
            "/workspaces/demo/webhooks/",
            {"name": slug, "workflow": slug, "environment": "test"},
        )
        url = BASE + hook["url"] if hook["url"].startswith("/") else hook["url"]
        load = Load(url, hook["secret"])
        load.start()
        log("charge démarrée (webhooks + lectures) ; 15 s avec l'ancien code")
        time.sleep(15)
        marks = {"charge démarrée": time.time()}

        log("migrations du NOUVEAU code appliquées PENDANT que l'ancien code sert")
        compose("run", "--rm", "--no-deps", "migrate", env=NEW)
        marks["migrations appliquées"] = time.time()
        time.sleep(5)
        for svc in ORDER:
            t0 = time.time()
            log(f"bascule de {svc} vers le nouveau code")
            marks[f"début de la bascule de {svc}"] = time.time()
            if svc in ("api-ui", "api-ingest") and a.replicas > 1 and not a.naive:
                ok = roll_replicas(svc, a.replicas)
            else:
                compose(
                    "up",
                    "-d",
                    "--no-build",
                    "--no-deps",
                    *(
                        [f"--scale={svc}={a.replicas}"]
                        if svc in ("api-ui", "api-ingest")
                        else []
                    ),
                    svc,
                    env=NEW,
                )
                ok = wait_healthy(svc)
            marks[
                f"{svc} (basculé en {time.time() - t0:.0f} s{'' if ok else ', PAS SAIN'})"
            ] = time.time()
            log(f"  {svc} : {'sain' if ok else 'PAS SAIN'}")
            time.sleep(3)
        log("tout est sur le nouveau code ; 15 s de charge de plus")
        time.sleep(15)
        load.finish()

        # ----- vérification dans la base
        time.sleep(8)
        out = compose(
            "exec",
            "-T",
            "api-ui",
            "python",
            "manage.py",
            "shell",
            "-c",
            f"""
from runs.models import WorkflowRun
from collections import Counter
runs = WorkflowRun.objects.filter(workflow_version__workflow__slug="{slug}")
ns = [r.inputs.get("n") for r in runs]
print("RESULT", json.dumps({{"runs": runs.count(), "statuts": dict(Counter(runs.values_list("status", flat=True))), "n_distincts": len(set(ns)), "ns": sorted(int(x) for x in ns)}}))
""".replace("json.dumps", "__import__('json').dumps"),
            env=NEW,
        )
        res = json.loads(
            next(l for l in out.stdout.splitlines() if l.startswith("RESULT")).split(
                " ", 1
            )[1]
        )
        acked = sorted(load.acked)
        lost = sorted(set(acked) - set(res["ns"]))
        dup = res["runs"] - res["n_distincts"]
        errors = [r for r in load.reads if r[1] != 200]
        # coupures continues de l'interface
        longest, run_start = 0.0, None
        for t, code, dur in load.reads:
            if code != 200:
                run_start = run_start or t
                longest = max(longest, t + dur - run_start)
            else:
                run_start = None
        rate = len(errors) / max(1, len(load.reads))
        # fenêtres d'erreur : début, durée, codes, et PHASE (dernier jalon franchi avant le début)
        windows, cur = [], None
        for t, code, dur in load.reads:
            if code != 200:
                cur = cur or {"t": t, "codes": set(), "n": 0}
                cur["codes"].add(code)
                cur["n"] += 1
                cur["end"] = t + dur
            elif cur:
                windows.append(cur)
                cur = None
        if cur:
            windows.append(cur)
        ordered = sorted(marks.items(), key=lambda kv: kv[1])
        phases = [
            {"début": time.strftime("%H:%M:%S", time.localtime(w["t"])), "durée_s": round(w["end"] - w["t"], 1), "lectures": w["n"], "codes": sorted(w["codes"]),
             "phase": next((k for k, v in reversed(ordered) if v <= w["t"]), "avant")}
            for w in windows
        ]  # fmt: skip
        verdict = {
            "livraisons acquittées": len(acked), "runs créés": res["runs"], "perdues (acquittées sans run)": len(lost), "doublons": dup,
            "statuts des runs": res["statuts"], "lectures UI": len(load.reads), "lectures en erreur": len(errors), "taux d'erreur UI": f"{rate:.2%}",
            "plus longue coupure UI (s)": round(longest, 1), "codes d'erreur UI": sorted({r[1] for r in errors}),
            "fenêtres d'erreur de l'interface": phases, "jalons": {k: time.strftime("%H:%M:%S", time.localtime(v)) for k, v in marks.items()},
        }  # fmt: skip
        print(json.dumps(verdict, ensure_ascii=False, indent=1))
        ok = (
            not lost
            and dup == 0
            and set(res["statuts"]) <= {"succeeded"}
            and len(acked) > 0
        )
        if a.replicas >= 2:
            ok = ok and rate <= 0.02 and longest <= 8
        print(
            "MISE À JOUR PROGRESSIVE : "
            + ("OK (seuils tenus)" if ok else "SEUILS NON TENUS")
        )
        return 0 if ok else 1
    finally:
        if not a.keep:
            compose("down", "-v", "--remove-orphans", env=NEW, check=False)


if __name__ == "__main__":
    sys.exit(main())
