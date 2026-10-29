#!/usr/bin/env bash
# Démarre un agent runner sur CETTE machine (développement). Le jeton s'obtient dans Plate-forme → Runners (ou
# Administration → Runners pour un runner dédié à un espace) et n'est affiché qu'une fois.
#   scripts/runner.sh orr_…                 # contre la pile locale (http://127.0.0.1:8100)
#   ORCH_URL=https://fluxonata.exemple scripts/runner.sh orr_…
#   ORCH_RUNNER_EXECUTOR=subprocess scripts/runner.sh orr_…   # SANS isolation : développement uniquement
source "$(dirname "$0")/_common.sh"
cd "$ROOT"
TOKEN="${1:-${ORCH_RUNNER_TOKEN:-}}"
[ -n "$TOKEN" ] || { echo "Usage : scripts/runner.sh <jeton orr_…>"; exit 1; }
export ORCH_URL="${ORCH_URL:-http://127.0.0.1:${ORCH_API_PORT:-8100}}"
export ORCH_RUNNER_TOKEN="$TOKEN"
exec "$ROOT/.venv/bin/python" "$ROOT/runner/agent.py" "${@:2}"
