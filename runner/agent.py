#!/usr/bin/env python3
"""Fluxonata runner agent: executes actions for a Fluxonata platform, like a GitLab runner.

It only ever makes OUTGOING HTTPS calls to the platform (long-poll for a job, heartbeat, hand back the outcome). It has no
access to the database, the vault key or any job it was not given, and runs every action in the same hardened,
throw-away Docker container as the platform's own workers (no network, non-root, read-only filesystem, limits).

    ORCH_URL=https://fluxonata.example.org ORCH_RUNNER_TOKEN=orr_… python agent.py

Configuration (environment or flags): ORCH_URL, ORCH_RUNNER_TOKEN | ORCH_RUNNER_TOKEN_FILE, ORCH_RUNNER_CONCURRENCY,
ORCH_RUNNER_EXECUTOR (docker | subprocess), ORCH_CA_FILE. The sandbox is tuned with the same ORCH_* variables as the
platform's workers (ORCH_HARDENED, ORCH_PYTHON_IMAGE, ORCH_WORK_DIR, …). The token is never logged.
"""

import argparse
import json
import logging
import os
import platform
import random
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

VERSION = "0.1.0"
log = logging.getLogger("fluxonata.runner")


class Revoked(Exception):
    """The platform no longer accepts this token (runner deleted or token rotated)."""


class NetworkError(Exception):
    pass


def import_sandbox():
    """The sandbox code lives in the platform's `backend/runs/runners.py` (Django-free by design)."""
    here = Path(__file__).resolve().parent
    for candidate in (
        os.environ.get("ORCH_BACKEND_DIR", ""),
        here.parent / "backend",
        here / "lib",
    ):
        if candidate and (Path(candidate) / "runs" / "runners.py").exists():
            sys.path.insert(0, str(candidate))
            break
    from runs.runners import DockerRunner, SubprocessRunner

    return DockerRunner, SubprocessRunner


class Client:
    """Minimal JSON-over-HTTP client (standard library only)."""

    def __init__(self, base_url: str, token: str, ca_file: str = ""):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("ORCH_URL doit commencer par https:// (ou http:// en développement).")
        self.base, self.token = base_url.rstrip("/"), token
        self.ctx = ssl.create_default_context(cafile=ca_file or None) if self.base.startswith("https") else None

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30.0):
        """Returns (status, parsed JSON or None). 401 raises Revoked; connection problems raise NetworkError."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(  # noqa: S310 - the scheme is checked in __init__
            self.base + "/api/v1" + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"fluxonata-runner/{VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as resp:  # noqa: S310 - http(s) URL chosen by the operator
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code == 401:
                raise Revoked() from None
            try:
                return exc.code, json.loads(raw) if raw else None
            except ValueError:
                return exc.code, None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise NetworkError(str(exc)) from None


class Agent:
    def __init__(
        self,
        client: Client,
        executor,
        concurrency: int = 2,
        once: bool = False,
        idle_exit: float = 0.0,
    ):
        self.client, self.executor, self.once, self.idle_exit = (
            client,
            executor,
            once,
            idle_exit,
        )
        self.concurrency = concurrency
        self.stop = threading.Event()
        self.config: dict = {}
        self.done = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ configuration
    def configure(self) -> dict:
        status, cfg = self.client.call(
            "POST",
            "/runner/me/",
            {
                "version": VERSION,
                "platform": f"{platform.system().lower()}/{platform.machine()}",
            },
        )
        if status != 200 or not cfg:
            raise NetworkError(f"réponse inattendue de la plate-forme ({status})")
        self.config = cfg
        self.concurrency = max(1, min(self.concurrency, int(cfg.get("max_concurrent", self.concurrency))))
        log.info(
            "runner « %s » (%s) : %d tâche(s) en parallèle, runtimes %s, étiquettes %s",
            cfg["name"],
            cfg["scope"],
            self.concurrency,
            ",".join(cfg["runtimes"]),
            ",".join(cfg["tags"]) or "—",
        )
        return cfg

    # ------------------------------------------------------------ loop
    def run(self) -> int:
        self.configure()
        slots = [threading.Thread(target=self._slot, name=f"slot-{i}", daemon=True) for i in range(self.concurrency)]
        for t in slots:
            t.start()
        try:
            while any(t.is_alive() for t in slots):
                for t in slots:
                    t.join(0.2)
        except KeyboardInterrupt:
            self.stop.set()
        return 2 if self.revoked else 3 if self.too_old else 0

    revoked = False
    too_old = False

    def _slot(self):
        backoff, idle_since = 1.0, time.monotonic()
        while not self.stop.is_set():
            try:
                status, job = self.client.call(
                    "POST",
                    "/runner/jobs/request/",
                    {"wait": int(self.config.get("long_poll_seconds", 20))},
                    timeout=float(self.config.get("long_poll_seconds", 20)) + 15,
                )
                backoff = 1.0
                if status == 200 and job:
                    self._execute(job)
                    idle_since = time.monotonic()
                    if self.once:
                        self.stop.set()
                elif status == 204:
                    if self.idle_exit and time.monotonic() - idle_since > self.idle_exit:
                        self.stop.set()
                elif status == 426:  # la plateforme exige une version d'agent plus récente (AR3.7)
                    detail = ((job or {}).get("error") or {}).get("details") or {} if isinstance(job, dict) else {}
                    log.error(
                        "agent trop ancien : la plateforme exige la version %s ou plus récente (installée : %s). Arrêt.",
                        detail.get("min_agent_version", "?"),
                        VERSION,
                    )
                    self.too_old = True
                    self.stop.set()
                elif (
                    status == 429 and isinstance(job, dict) and (job.get("error") or {}).get("code") == "long_poll_busy"
                ):
                    # La plateforme limite ses attentes longues (AR1.3) : ce n'est pas une panne, on revient plus tard.
                    retry = ((job["error"].get("details") or {}).get("retry_after")) or 5
                    log.debug(
                        "plate-forme occupée par d'autres attentes longues ; nouvelle demande dans %s s",
                        retry,
                    )
                    self._sleep(min(max(float(retry), 1.0), 60.0))
                else:
                    log.warning("demande de tâche : réponse %s", status)
                    self._sleep(backoff)
            except Revoked:
                log.error("jeton refusé : runner supprimé ou jeton changé. Arrêt.")
                self.revoked = True
                self.stop.set()
            except NetworkError as exc:
                log.warning(
                    "plate-forme injoignable (%s) ; nouvelle tentative dans %.0f s",
                    exc,
                    backoff,
                )
                self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _sleep(self, seconds: float):
        self.stop.wait(seconds * (0.7 + random.random() * 0.6))  # noqa: S311 - jitter, not a secret ; runners must not retry in lockstep

    # ------------------------------------------------------------ one job
    def _execute(self, job: dict):
        jid, attempt = job["job_id"], job["attempt"]
        log.info(
            "tâche %s (tentative %d, %s, espace %s) démarrée",
            jid[:8],
            attempt,
            job["runtime"],
            job.get("workspace", "?"),
        )
        cancel, fenced, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        def beat():
            interval = float(self.config.get("heartbeat_seconds", 10))
            while not finished.wait(interval):
                try:
                    status, body = self.client.call(
                        "POST",
                        f"/runner/jobs/{jid}/heartbeat/",
                        {"attempt": attempt},
                        timeout=15,
                    )
                except (NetworkError, Revoked):
                    continue  # the lease absorbs short outages; the platform requeues the job if we stay silent
                if status == 409 or status == 404:
                    log.warning("tâche %s : tentative abandonnée par la plate-forme", jid[:8])
                    fenced.set()
                    cancel.set()
                    return
                if status == 200 and body and body.get("cancel"):
                    cancel.set()

        threading.Thread(target=beat, daemon=True).start()
        version = SimpleNamespace(
            runtime=job["runtime"],
            source=job["source"],
            entrypoint=job["entrypoint"],
            limits=job["limits"],
            network_policy=job["network_policy"],
        )
        try:
            outcome = self.executor.run(
                version,
                job["inputs"],
                int(job["timeout_seconds"]),
                cancel,
                job.get("env") or {},
            )
            payload = {
                "attempt": attempt,
                "ok": outcome.ok,
                "result": outcome.result,
                "error": outcome.error,
                "logs": outcome.logs,
                "exit_code": outcome.exit_code,
                "cancelled": outcome.cancelled,
            }
        except Exception as exc:
            log.exception("tâche %s : erreur du bac à sable", jid[:8])
            payload = {
                "attempt": attempt,
                "ok": False,
                "error": {
                    "code": "RUNNER_ERROR",
                    "message": f"Erreur du runner : {exc}"[:300],
                },
            }
        finally:
            finished.set()
        if fenced.is_set():
            return  # someone else owns this job now; our answer would be ignored anyway
        self._report(jid, payload)
        with self._lock:
            self.done += 1

    def _report(self, jid: str, payload: dict):
        for pause in (0, 1, 3, 8, 15):
            if pause:
                time.sleep(pause)
            try:
                status, _ = self.client.call("POST", f"/runner/jobs/{jid}/complete/", payload, timeout=60)
            except NetworkError as exc:
                log.warning("tâche %s : résultat non remis (%s)", jid[:8], exc)
                continue
            except Revoked:
                return
            if status in (200, 409):
                log.info(
                    "tâche %s : %s",
                    jid[:8],
                    "résultat enregistré" if status == 200 else "résultat tardif ignoré",
                )
                return
            log.warning("tâche %s : réponse %s au dépôt du résultat", jid[:8], status)
        log.error(
            "tâche %s : résultat perdu (la plate-forme relancera la tâche à l'expiration du bail)",
            jid[:8],
        )


def read_token(args) -> str:
    if args.token_file:
        return Path(args.token_file).read_text().strip()
    return (args.token or os.environ.get("ORCH_RUNNER_TOKEN", "")).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent runner Fluxonata")
    ap.add_argument(
        "--url",
        default=os.environ.get("ORCH_URL", ""),
        help="adresse de la plate-forme (ORCH_URL)",
    )
    ap.add_argument(
        "--token",
        default="",
        help="jeton du runner (préférez ORCH_RUNNER_TOKEN ou --token-file)",
    )
    ap.add_argument("--token-file", default=os.environ.get("ORCH_RUNNER_TOKEN_FILE", ""))
    ap.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("ORCH_RUNNER_CONCURRENCY", "2")),
    )
    ap.add_argument(
        "--executor",
        choices=["docker", "subprocess"],
        default=os.environ.get("ORCH_RUNNER_EXECUTOR", "docker"),
    )
    ap.add_argument(
        "--ca-file",
        default=os.environ.get("ORCH_CA_FILE", ""),
        help="autorité de certification (HTTPS interne)",
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="exécute une seule tâche puis s'arrête (tests)",
    )
    ap.add_argument(
        "--idle-exit",
        type=float,
        default=0.0,
        help="s'arrête après N secondes sans tâche (tests)",
    )
    ap.add_argument("--version", action="version", version=f"fluxonata-runner {VERSION}")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = read_token(args)
    if not args.url or not token.startswith("orr_"):
        ap.error("ORCH_URL et un jeton de runner (« orr_… ») sont requis.")
    if args.url.startswith("http://") and not any(h in args.url for h in ("://localhost", "://127.", "://[::1]")):
        log.warning(
            "⚠ HTTP sans chiffrement vers %s : le jeton et les secrets des tâches circuleraient en clair. Utilisez HTTPS.",
            args.url,
        )
    docker_runner, subprocess_runner = import_sandbox()
    if args.executor == "subprocess":
        log.warning("⚠ exécuteur « subprocess » : les actions ne sont PAS isolées (développement et tests uniquement).")
        executor = subprocess_runner()
    else:
        try:
            ver = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            ver = None
        if ver is None or ver.returncode != 0:
            log.error(
                "Docker est introuvable ou inaccessible pour cet utilisateur : le runner exécute chaque action dans un conteneur."
            )
            return 1
        log.info("Docker %s", ver.stdout.strip())
        executor = docker_runner()
    agent = Agent(
        Client(args.url, token, args.ca_file),
        executor,
        args.concurrency,
        args.once,
        args.idle_exit,
    )

    def on_signal(signum, _frame):
        if agent.stop.is_set():
            os._exit(130)
        log.info("arrêt demandé : les tâches en cours se terminent (Ctrl+C à nouveau pour forcer)")
        agent.stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        return agent.run()
    except Revoked:
        log.error("jeton refusé : runner supprimé ou jeton changé.")
        return 2
    except NetworkError as exc:
        log.error("plate-forme injoignable au démarrage : %s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
