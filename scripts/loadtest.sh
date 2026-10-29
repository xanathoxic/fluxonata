#!/usr/bin/env bash
# Load test. Stops the dev worker/orchestrator first so that the measured workers are the only ones.
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
scripts/dev-down.sh >/dev/null; scripts/dev-up.sh >/dev/null 2>&1; scripts/dev-down.sh >/dev/null
docker compose -f infra/docker-compose.yml -p fluxonata up -d --wait db >/dev/null 2>&1
exec python3 tests/load/loadtest.py
