"""Settings used by the action sandbox (`runs.runners`).

Inside the platform they come from Django. The standalone runner agent has no Django: the same names are then read from
the environment, with the same defaults as `fluxonata/settings.py`, so one sandbox implementation serves both."""

import os
from pathlib import Path

_DEFAULTS = {
    "ORCH_WORK_DIR": "",
    "ORCH_START_CAP_SECONDS": 120,
    "ORCH_EGRESS_TEST_NETWORK": "fluxonata-egress-test",
    "ORCH_HARDENED": False,
    "ORCH_SECCOMP_PROFILE": "",
    "ORCH_DOCKER_RUNTIME": "",
    "ORCH_RUNNER": "docker",
    "ORCH_PYTHON_IMAGE": "python:3.13-slim",
    "ORCH_NODE_IMAGE": "node:22-slim",
    "ORCH_POWERSHELL_IMAGE": "mcr.microsoft.com/powershell:lts-debian-12",
    "ORCH_MAX_LOG_BYTES": 64 * 1024,
    "ORCH_MAX_RESULT_BYTES": 1024 * 1024,
}
_ENV_PATHS = {"ORCH_WORK_DIR"}


class _Settings:
    def __getattr__(self, name: str):
        try:
            from django.conf import settings as dj

            if dj.configured:
                return getattr(dj, name)
        except ImportError:  # no Django in the runner image
            pass
        if name not in _DEFAULTS:
            raise AttributeError(name)
        default, raw = _DEFAULTS[name], os.environ.get(name)
        if raw is None:
            return default
        if isinstance(default, bool):
            return raw == "1"
        return int(raw) if isinstance(default, int) else raw


settings = _Settings()
WORKERS_DIR = (
    Path(os.environ.get("ORCH_WORKERS_DIR", ""))
    if os.environ.get("ORCH_WORKERS_DIR")
    else Path(__file__).resolve().parents[2] / "workers"
)
