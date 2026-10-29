#!/usr/bin/env bash
# Shared helpers. Never prints secret values.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml" -p fluxonata)
mkdir -p "$RUN_DIR"
load_env() { set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a; }
is_running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
