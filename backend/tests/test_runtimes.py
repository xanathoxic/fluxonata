import shutil

import pytest

from catalog.models import Action, ActionVersion
from runs import services
from runs.runners import DockerRunner, SubprocessRunner
from runs.worker import process_one
from tenancy.models import Environment
from tests.test_worker import docker_ready
from tests.wf_helpers import (
    make_action,  # noqa: F401
    schema,
)

NODE_SRC = "export function run(i) {\n  console.log('log line');\n  return { doubled: i.n * 2 };\n}\n"
TS_SRC = "export function run(i: { n: number }): { doubled: number } {\n  const k: number = i.n;\n  return { doubled: k * 2 };\n}\n"
SH_SRC = "#!/usr/bin/env bash\nset -eu\necho 'log line' >&2\nn=$(python3 -c \"import json,os;print(json.load(open(os.environ['ORCH_INPUT_FILE']))['inputs']['n'])\" 2>/dev/null || echo 4)\necho '{\"doubled\": 8}'\n"
SH_FAIL = "#!/usr/bin/env bash\necho oops >&2\nexit 3\n"

has_node = shutil.which("node") is not None
node_image = pytest.mark.skipif(
    not docker_ready()
    or __import__("subprocess").run(["docker", "image", "inspect", "node:22-slim"], capture_output=True).returncode
    != 0,
    reason="node image unavailable",
)
RUNNERS = [
    pytest.param(SubprocessRunner(), id="subprocess"),
    pytest.param(DockerRunner(), id="docker", marks=node_image),
]


def run_action(world, runtime, source, inputs, runner, **extra):
    ws = world["ws_a"]
    action = Action.objects.create(
        workspace=ws, slug=f"rt-{runtime}-{__import__('uuid').uuid4().hex[:6]}", name=runtime
    )
    v = ActionVersion.objects.create(
        action=action,
        version="1.0.0",
        runtime=runtime,
        source=source,
        input_schema=schema(),
        output_schema={"type": "object"},
        limits={"timeout_seconds": 30, "memory_mb": 128},
        **extra,
    )
    ex, _ = services.create_execution(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=v,
        user=world["designer"],
        inputs=inputs,
    )
    process_one("w", runner)
    ex.refresh_from_db()
    return ex


@pytest.mark.skipif(not has_node, reason="node missing")
@pytest.mark.parametrize("runner", RUNNERS)
def test_node_action(world, runner):
    ex = run_action(world, "node", NODE_SRC, {"n": 21}, runner)
    assert ex.status == "succeeded", ex.error
    assert ex.result == {"doubled": 42} and "log line" in ex.attempts.get().logs


def _host_node_has_ts() -> bool:
    import subprocess

    if not has_node:
        return False
    out = subprocess.run(["node", "--experimental-strip-types", "-e", "const x: number = 1"], capture_output=True)
    return out.returncode == 0


TS_RUNNERS = [
    pytest.param(
        SubprocessRunner(),
        id="subprocess",
        marks=pytest.mark.skipif(not _host_node_has_ts(), reason="host node lacks TypeScript type stripping"),
    ),
    pytest.param(DockerRunner(), id="docker", marks=node_image),
]


@pytest.mark.parametrize("runner", TS_RUNNERS)
def test_typescript_action(world, runner):
    ex = run_action(world, "typescript", TS_SRC, {"n": 5}, runner)
    assert ex.status == "succeeded", ex.error
    assert ex.result == {"doubled": 10}


@pytest.mark.skipif(not has_node, reason="node missing")
@pytest.mark.parametrize("runner", RUNNERS)
def test_node_error_is_structured(world, runner):
    ex = run_action(world, "node", "export function run() { throw new TypeError('bad'); }", {}, runner)
    assert ex.status == "failed" and ex.error["code"] == "ACTION_ERROR" and "TypeError: bad" in ex.error["message"]


@pytest.mark.parametrize(
    "runner",
    [SubprocessRunner(), pytest.param(DockerRunner(), marks=pytest.mark.skipif(not docker_ready(), reason="docker"))],
)
def test_shell_action(world, runner):
    ex = run_action(world, "shell", SH_SRC, {"n": 4}, runner)
    assert ex.status == "succeeded", ex.error
    assert ex.result == {"doubled": 8} and "log line" in ex.attempts.get().logs


@pytest.mark.parametrize(
    "runner",
    [SubprocessRunner(), pytest.param(DockerRunner(), marks=pytest.mark.skipif(not docker_ready(), reason="docker"))],
)
def test_shell_failure_code_is_reported(world, runner):
    ex = run_action(world, "shell", SH_FAIL, {}, runner)
    assert ex.status == "failed" and "code 3" in ex.error["message"] and "oops" in ex.attempts.get().logs


@node_image
def test_node_container_is_sandboxed(world):
    src = (
        "import net from 'node:net';\nimport fs from 'node:fs';\n"
        "export async function run() {\n"
        "  const out = { uid: process.getuid() };\n"
        "  out.net = await new Promise((r) => { const s = net.connect(80, '1.1.1.1'); s.setTimeout(1500); s.on('connect', () => r('open')); s.on('error', () => r('blocked')); s.on('timeout', () => r('blocked')); });\n"
        "  try { fs.writeFileSync('/work/x', '1'); out.write = 'ok'; } catch { out.write = 'denied'; }\n"
        "  return out;\n}\n"
    )
    ex = run_action(world, "node", src, {}, DockerRunner())
    assert ex.status == "succeeded", ex.error
    assert ex.result == {"uid": 65534, "net": "blocked", "write": "denied"}


PS_SRC = (
    "$ErrorActionPreference = 'Stop'\n"
    "$in = Get-Content $env:ORCH_INPUT_FILE | ConvertFrom-Json\n"
    "[Console]::Error.WriteLine('log ps')\n"
    "@{ doubled = $in.inputs.n * 2; shell = 'pwsh' } | ConvertTo-Json -Compress\n"
)
ps_image = pytest.mark.skipif(
    not docker_ready()
    or __import__("subprocess")
    .run(["docker", "image", "inspect", "mcr.microsoft.com/powershell:lts-debian-12"], capture_output=True)
    .returncode
    != 0,
    reason="powershell image unavailable (docker pull mcr.microsoft.com/powershell:lts-debian-12)",
)


@ps_image
def test_powershell_action_runs_in_dedicated_image(world):
    ex = run_action(world, "powershell", PS_SRC, {"n": 21}, DockerRunner())
    assert ex.status == "succeeded", ex.error
    assert ex.result == {"doubled": 42, "shell": "pwsh"} and "log ps" in ex.attempts.get().logs


@ps_image
def test_powershell_error_and_sandbox(world):
    ex = run_action(world, "powershell", "throw 'boom'\n", {}, DockerRunner())
    assert ex.status == "failed" and ex.error["code"] == "ACTION_ERROR"
    src = (
        "$r = @{}\n"
        "try { Invoke-WebRequest -Uri http://1.1.1.1 -TimeoutSec 3 | Out-Null; $r.net = 'open' } catch { $r.net = 'blocked' }\n"
        "try { Set-Content -Path /work/x -Value 1 -ErrorAction Stop; $r.write = 'ok' } catch { $r.write = 'denied' }\n"
        "$r | ConvertTo-Json -Compress\n"
    )
    ok = run_action(world, "powershell", src, {}, DockerRunner())
    assert ok.status == "succeeded", ok.error
    assert ok.result == {"net": "blocked", "write": "denied"}
