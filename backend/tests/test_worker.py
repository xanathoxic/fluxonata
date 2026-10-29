import shutil
import subprocess

import pytest

from catalog.models import ActionVersion
from runs import services
from runs.models import Execution
from runs.runners import DockerRunner, SubprocessRunner
from runs.worker import process_one
from tenancy.models import Environment
from tests.test_services import new_exec


def docker_ready():
    if not shutil.which("docker"):
        return False
    from django.conf import settings

    return (
        subprocess.run(["docker", "image", "inspect", settings.ORCH_PYTHON_IMAGE], capture_output=True).returncode == 0
    )


needs_docker = pytest.mark.skipif(not docker_ready(), reason="docker or runtime image unavailable")
RUNNERS = [
    pytest.param(SubprocessRunner(), id="subprocess"),
    pytest.param(DockerRunner(), id="docker", marks=needs_docker),
]


@pytest.mark.parametrize("runner", RUNNERS)
def test_worker_runs_real_action(world, runner):
    ex = new_exec(world)
    assert process_one("test-worker", runner) is True
    ex.refresh_from_db()
    assert ex.status == "succeeded"
    assert ex.result["words"] == 2 and ex.result["characters"] == 3
    assert "analyse de" in ex.attempts.get().logs  # logs separated from result


def custom(world, source, **limits):
    base = world["version_a"]
    action = base.action
    v = ActionVersion.objects.create(
        action=action,
        version="9.9.9",
        source=source,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        limits={"timeout_seconds": 5, "memory_mb": 64} | limits,
    )
    env = Environment.objects.get(workspace=world["ws_a"], kind="test")
    ex, _ = services.create_execution(
        workspace=world["ws_a"], environment=env, action_version=v, user=world["designer"], inputs={}
    )
    return ex


@pytest.mark.parametrize("runner", RUNNERS)
def test_action_exception_is_reported_structurally(world, runner):
    ex = custom(world, "def run(i):\n    raise ValueError('boom')\n")
    process_one("w", runner)
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "ACTION_ERROR" and "boom" in ex.error["message"]
    assert ex.error["next_step"]


@pytest.mark.parametrize("runner", RUNNERS)
def test_invalid_output_contract_fails(world, runner):
    ex = custom(world, "def run(i):\n    return [1, 2]\n")
    process_one("w", runner)
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "OUTPUT_INVALID"


@pytest.mark.parametrize("runner", RUNNERS)
def test_timeout_is_enforced(world, runner):
    ex = custom(world, "import time\ndef run(i):\n    time.sleep(30)\n    return {}\n", timeout_seconds=2)
    process_one("w", runner)
    ex.refresh_from_db()
    assert ex.status == "failed" and ex.error["code"] == "TIMEOUT"


@needs_docker
def test_docker_sandbox_blocks_network_and_writes(world):
    src = (
        "import socket, json\n"
        "def run(i):\n"
        "    out = {}\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 80), timeout=2); out['net'] = 'open'\n"
        "    except OSError:\n"
        "        out['net'] = 'blocked'\n"
        "    try:\n"
        "        open('/work/x', 'w'); out['write'] = 'ok'\n"
        "    except OSError:\n"
        "        out['write'] = 'denied'\n"
        "    import os; out['uid'] = os.getuid()\n"
        "    return out\n"
    )
    ex = custom(world, src)
    process_one("w", DockerRunner())
    ex.refresh_from_db()
    assert ex.status == "succeeded", ex.error
    assert ex.result == {"net": "blocked", "write": "denied", "uid": 65534}


@needs_docker
def test_docker_memory_limit(world):
    ex = custom(world, "def run(i):\n    x = bytearray(400 * 1024 * 1024)\n    return {}\n", memory_mb=64)
    process_one("w", DockerRunner())
    ex.refresh_from_db()
    assert ex.status == "failed"


def test_empty_queue(world):
    assert process_one("w") is False
    assert Execution.objects.count() == 0


def test_docker_command_baseline_and_hardened_profile(settings, tmp_path):
    from runs.runners import RUNTIMES, build_docker_command

    spec = RUNTIMES["python"]
    base = build_docker_command("n", "img", "none", 64, tmp_path, None, spec)
    joined = " ".join(base)
    for must in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--user 65534:65534",
        "--memory 64m",
        "--pids-limit 64",
    ):
        assert must in joined
    assert "docker.sock" not in joined and "--privileged" not in joined and "--ipc none" not in joined
    settings.ORCH_HARDENED, settings.ORCH_DOCKER_RUNTIME, settings.ORCH_SECCOMP_PROFILE = (
        True,
        "runsc",
        "/etc/orch/seccomp.json",
    )
    hard = " ".join(build_docker_command("n", "img", "none", 64, tmp_path, None, spec))
    assert "--ipc none" in hard and "--pids-limit 32" in hard and "--ulimit nproc=64:64" in hard
    assert "--runtime runsc" in hard and "seccomp=/etc/orch/seccomp.json" in hard


@needs_docker
def test_hardened_profile_still_runs_real_actions(world, settings):
    settings.ORCH_HARDENED = True
    ex = new_exec(world)
    process_one("w", DockerRunner())
    ex.refresh_from_db()
    assert ex.status == "succeeded", ex.error


def test_action_time_limit_starts_when_the_container_runs_not_at_launch(settings):
    import subprocess
    import threading

    from runs.runners import _communicate

    def proc(seconds):
        return subprocess.Popen(["sleep", str(seconds)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    settings.ORCH_START_CAP_SECONDS = 60
    never_started = threading.Event()  # a loaded host that is still starting the container
    p = proc(1.6)
    _, _, reason = _communicate(p, 1, None, p.kill, never_started)
    assert reason is None  # 1.6 s > the 1 s limit, but the limit clock has not started yet
    running = threading.Event()
    running.set()
    p2 = proc(10)
    _, _, reason = _communicate(p2, 1, None, p2.kill, running)
    assert reason == "timeout"  # once running, the limit applies
    settings.ORCH_START_CAP_SECONDS = 1  # absolute cap: a container that never starts cannot hang the attempt
    p3 = proc(10)
    _, _, reason = _communicate(p3, 1, None, p3.kill, never_started)
    assert reason == "timeout"
