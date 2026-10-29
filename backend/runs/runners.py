"""Action runners. The DockerRunner is the default; SubprocessRunner is dev/test only and NOT isolated.

Protocol v1: input JSON in /work/input.json -> exactly one JSON envelope line on stdout
({"protocol":1,"ok":true,"result":...} | {"protocol":1,"ok":false,"error":{code,message}}).
User prints go to stderr (= logs), never into the result. Shell actions print their JSON result as the last
stdout line (their stderr is the log); the runner builds the envelope.

Cancellation is cooperative then forced: the container receives SIGTERM (`docker stop`) and is killed after a
short grace period.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .runtime_config import WORKERS_DIR, settings

CANCEL_GRACE_SECONDS = 3
# Egress policies -> docker network. "none": no network at all. "test-service": internal network with only the
# local demo service (no Internet, no host). Anything else is refused.
NETWORK_POLICIES = {
    "none": "none",
    "test-service": "test-service",
}  # "test-service" -> settings.ORCH_EGRESS_TEST_NETWORK

# runtime -> (source file name, harness (None = shell), image setting, command inside the container)
RUNTIMES = {
    "python": {
        "file": "action.py",
        "harness": "python/harness.py",
        "image": "ORCH_PYTHON_IMAGE",
        "cmd": ["python", "-I", "/work/harness.py"],
        "local": [sys.executable, "-I"],
    },
    "node": {
        "file": "action.mjs",
        "harness": "node/harness.mjs",
        "image": "ORCH_NODE_IMAGE",
        "cmd": ["node", "/work/harness.mjs"],
        "local": ["node"],
    },
    "typescript": {
        "file": "action.ts",
        "harness": "node/harness.mjs",
        "image": "ORCH_NODE_IMAGE",
        "cmd": ["node", "--experimental-strip-types", "/work/harness.mjs"],
        "local": ["node", "--experimental-strip-types"],
    },
    "powershell": {
        "file": "action.ps1",
        "harness": None,
        "image": "ORCH_POWERSHELL_IMAGE",
        "cmd": ["pwsh", "-NoProfile", "-NonInteractive", "-File", "/work/action.ps1"],
        "local": ["pwsh", "-NoProfile", "-NonInteractive", "-File"],
    },
    "shell": {
        "file": "action.sh",
        "harness": None,
        "image": "ORCH_PYTHON_IMAGE",
        "cmd": ["bash", "/work/action.sh"],
        "local": ["bash"],
    },
}


@dataclass
class RunOutcome:
    ok: bool
    result: object = None
    error: dict | None = None
    logs: str = ""
    exit_code: int | None = None
    cancelled: bool = False
    extra: dict = field(default_factory=dict)


def _prepare(version, inputs) -> Path:
    spec = RUNTIMES[version.runtime]
    workdir = Path(tempfile.mkdtemp(prefix="orch-run-", dir=settings.ORCH_WORK_DIR or None))
    (workdir / spec["file"]).write_text(version.source)
    (workdir / "input.json").write_text(json.dumps({"entrypoint": version.entrypoint, "inputs": inputs}))
    if spec["harness"]:
        shutil.copy(WORKERS_DIR / spec["harness"], workdir / Path(spec["harness"]).name)
    workdir.chmod(0o755)
    for f in workdir.iterdir():
        f.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH | stat.S_IWUSR)
    return workdir


def _write_env_file(workdir: Path, env: dict) -> Path | None:
    """Secrets reach the container through a private env-file (never on the command line / `ps`); the file lives in
    the per-run temp dir, which is removed as soon as the run ends."""
    if not env:
        return None
    path = Path(tempfile.mkdtemp(prefix="orch-env-", dir=settings.ORCH_WORK_DIR or None)) / "env"
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        for k, v in env.items():
            if "\n" in v or "\r" in v:
                raise ValueError("Valeur de secret multi-lignes non prise en charge.")
            fh.write(f"{k}={v}\n")
    return path


def _parse(stdout: bytes, stderr: bytes, code: int | None, shell: bool = False) -> RunOutcome:
    logs = stderr[-settings.ORCH_MAX_LOG_BYTES :].decode(errors="replace")
    if len(stdout) > settings.ORCH_MAX_RESULT_BYTES:
        return RunOutcome(
            False,
            error={"code": "RESULT_TOO_LARGE", "message": "Le résultat dépasse la taille autorisée."},
            logs=logs,
            exit_code=code,
        )
    lines = [ln for ln in stdout.decode(errors="replace").splitlines() if ln.strip()]
    if shell:
        if code != 0:
            return RunOutcome(
                False,
                error={"code": "ACTION_ERROR", "message": f"Le script s'est terminé avec le code {code}."},
                logs=logs,
                exit_code=code,
            )
        try:
            return RunOutcome(True, result=json.loads(lines[-1]), logs=logs, exit_code=code)
        except Exception:  # noqa: BLE001
            return RunOutcome(
                False,
                error={"code": "PROTOCOL_ERROR", "message": "La dernière ligne de sortie n'est pas du JSON."},
                logs=logs,
                exit_code=code,
            )
    try:
        envelope = json.loads(lines[-1])
        assert envelope.get("protocol") == 1
    except Exception:  # noqa: BLE001
        return RunOutcome(
            False,
            error={"code": "PROTOCOL_ERROR", "message": "L'action n'a pas renvoyé de résultat exploitable."},
            logs=logs,
            exit_code=code,
        )
    if envelope.get("ok"):
        return RunOutcome(True, result=envelope.get("result"), logs=logs, exit_code=code)
    err = envelope.get("error") or {}
    return RunOutcome(
        False,
        error={"code": err.get("code", "ACTION_ERROR"), "message": err.get("message", "Erreur")},
        logs=logs,
        exit_code=code,
    )


def _communicate(proc, timeout, cancel: threading.Event | None, stop, started: threading.Event | None = None):
    """Wait for the process; returns (stdout, stderr, reason) with reason in {None, 'timeout', 'cancelled'}.

    The action's time limit starts when `started` is set (container actually running), so that a loaded host that is
    slow to start containers does not consume the action's budget. An absolute cap (limit + ORCH_START_CAP_SECONDS)
    still bounds the whole attempt if the container never starts."""
    waited = running_for = 0.0
    cap = timeout + (settings.ORCH_START_CAP_SECONDS if started is not None else 0)
    while True:
        try:
            out, err = proc.communicate(timeout=0.25)
            return out, err, None
        except subprocess.TimeoutExpired:
            waited += 0.25
            if started is None or started.is_set():
                running_for += 0.25
            if cancel is not None and cancel.is_set():
                reason = "cancelled"
            elif running_for >= timeout or waited >= cap:
                reason = "timeout"
            else:
                reason = None
            if reason:
                stop()
                out, err = proc.communicate()
                return out, err, reason


class SubprocessRunner:
    """NOT isolated: same host, same user. Dev/tests only (ORCH_RUNNER=subprocess)."""

    def run(
        self, version, inputs, timeout: int, cancel: threading.Event | None = None, env: dict | None = None
    ) -> RunOutcome:
        spec = RUNTIMES[version.runtime]
        workdir = _prepare(version, inputs)
        try:
            target = spec["file"] if spec["harness"] is None else Path(spec["harness"]).name
            proc_env = {
                "PATH": os.environ.get("PATH", ""),
                "ORCH_INPUT_FILE": str(workdir / "input.json"),
                **(env or {}),
            }
            proc = subprocess.Popen(  # noqa: S603
                [*spec["local"], str(workdir / target)],
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env,
            )

            def stop():
                proc.terminate()
                try:
                    proc.wait(CANCEL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()

            out, err, reason = _communicate(proc, timeout, cancel, stop)
            if reason == "cancelled":
                return RunOutcome(False, cancelled=True, error={"code": "CANCELLED", "message": "Annulée."})
            if reason == "timeout":
                return RunOutcome(False, error={"code": "TIMEOUT", "message": f"Délai de {timeout} s dépassé."})
            return _parse(out, err, proc.returncode, shell=version.runtime in ("shell", "powershell"))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def _watch_started(proc, name: str, started: threading.Event) -> None:
    """Set `started` once the container is running (or the docker process has ended)."""
    while not started.is_set() and proc.poll() is None:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True)  # noqa: S603,S607
        if r.stdout.strip() == "true":
            break
        time.sleep(0.15)
    started.set()


def build_docker_command(name, image, network, memory, workdir, env_file, spec) -> list[str]:
    """`docker run` arguments for one action. Pure function (unit-tested without Docker).

    Baseline: no network (or the internal test network), non-root, read-only rootfs, no capabilities,
    no-new-privileges, memory/CPU/pids limits. ORCH_HARDENED adds: no IPC namespace sharing, tighter ulimits and
    pids, an optional custom seccomp profile and an optional stronger runtime (e.g. gVisor `runsc`)."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        network,
        "--user",
        "65534:65534",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",  # noqa: S108 - container-side tmpfs, not a host path
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        f"{memory}m",
        "--memory-swap",
        f"{memory}m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
    ]
    if settings.ORCH_HARDENED:
        cmd[cmd.index("--pids-limit") + 1] = "32"
        cmd += ["--ipc", "none", "--ulimit", "nofile=256:256", "--ulimit", "nproc=64:64", "--ulimit", "core=0"]
        if settings.ORCH_SECCOMP_PROFILE:
            cmd += ["--security-opt", f"seccomp={settings.ORCH_SECCOMP_PROFILE}"]
    if settings.ORCH_DOCKER_RUNTIME:
        cmd += ["--runtime", settings.ORCH_DOCKER_RUNTIME]
    cmd += [
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "HOME=/tmp",  # read-only rootfs: tools that want a home directory use the tmpfs
        "-e",
        "POWERSHELL_TELEMETRY_OPTOUT=1",
        "-e",
        "POWERSHELL_UPDATECHECK=Off",
        "-e",
        "DOTNET_CLI_TELEMETRY_OPTOUT=1",
        "-e",
        "ORCH_INPUT_FILE=/work/input.json",
        *(["--env-file", str(env_file)] if env_file else []),
        "-v",
        f"{workdir}:/work:ro",
        "-w",
        "/work",
        image,
        *spec["cmd"],
    ]
    return cmd


class DockerRunner:
    """Untrusted-ish code in a throwaway container: no network, non-root, read-only rootfs,
    all capabilities dropped, memory/CPU/pids limits. Not a hostile-code sandbox (shared kernel)."""

    def run(
        self, version, inputs, timeout: int, cancel: threading.Event | None = None, env: dict | None = None
    ) -> RunOutcome:
        network = NETWORK_POLICIES.get(version.network_policy)
        if network == "test-service":
            network = settings.ORCH_EGRESS_TEST_NETWORK
        if network is None:
            raise ValueError(f"Politique réseau inconnue : {version.network_policy}")
        spec = RUNTIMES[version.runtime]
        workdir = _prepare(version, inputs)
        name = f"orch-{uuid.uuid4().hex[:12]}"
        env_file = _write_env_file(workdir, env or {})
        memory = int(version.limits.get("memory_mb", 128))
        image = getattr(settings, spec["image"])
        cmd = build_docker_command(name, image, network, memory, workdir, env_file, spec)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: S603
            started = threading.Event()
            threading.Thread(target=_watch_started, args=(proc, name, started), daemon=True).start()

            def stop():
                subprocess.run(["docker", "stop", "-t", str(CANCEL_GRACE_SECONDS), name], capture_output=True)  # noqa: S603,S607

            out, err, reason = _communicate(proc, timeout, cancel, stop, started)
            if reason == "cancelled":
                return RunOutcome(False, cancelled=True, error={"code": "CANCELLED", "message": "Annulée."})
            if reason == "timeout":
                return RunOutcome(False, error={"code": "TIMEOUT", "message": f"Délai de {timeout} s dépassé."})
            if proc.returncode == 137 and b'"protocol"' not in out:
                return RunOutcome(
                    False,
                    error={
                        "code": "OOM_OR_KILLED",
                        "message": f"L'action a été arrêtée (mémoire limitée à {memory} Mo ?).",
                    },
                    logs=err.decode(errors="replace"),
                    exit_code=137,
                )
            return _parse(out, err, proc.returncode, shell=version.runtime in ("shell", "powershell"))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            if env_file:
                shutil.rmtree(env_file.parent, ignore_errors=True)


def get_runner():
    return SubprocessRunner() if settings.ORCH_RUNNER == "subprocess" else DockerRunner()
