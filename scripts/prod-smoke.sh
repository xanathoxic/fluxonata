#!/usr/bin/env bash
# Fresh-install proof: build images, start the production-like stack, bootstrap an admin, run a real action through nginx, tear down.
# Usage: scripts/prod-smoke.sh [--keep]   (needs Docker; uses ports 8080 by default via ORCH_HTTP_PORT)
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
scripts/prod-init.sh >/dev/null
set -a; . infra/.env.prod; set +a
export ORCH_HTTP_PORT="${ORCH_HTTP_PORT:-8080}"
C=(docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod)
cleanup() { [ "${1:-}" = "--keep" ] || "${C[@]}" down -v --remove-orphans >/dev/null 2>&1; }
trap 'cleanup "${1:-}"' EXIT
"${C[@]}" up -d --build 2>&1 | tail -3
BASE="http://127.0.0.1:$ORCH_HTTP_PORT"
for _ in $(seq 1 60); do curl -fs "$BASE/api/v1/health/ready/" >/dev/null 2>&1 && break; sleep 2; done
curl -fs "$BASE/api/v1/health/ready/" || { echo "API non prête"; "${C[@]}" logs --tail 30 api; exit 1; }
echo; "${C[@]}" exec -T -e ADMIN_PW=prod-smoke-pass-123 api python manage.py bootstrap --workspace principal --password-env ADMIN_PW | tail -2
"${C[@]}" exec -T api python manage.py seed_demo --password prod-smoke-pass-123 >/dev/null
J="$(mktemp)"; csrf() { grep csrftoken "$J" | awk '{print $7}'; }
curl -fs -c "$J" "$BASE/api/v1/auth/csrf/" >/dev/null
curl -fs -b "$J" -c "$J" -H "X-CSRFToken: $(csrf)" -H "Origin: $BASE" -H 'Content-Type: application/json' -d '{"username":"alice","password":"prod-smoke-pass-123"}' "$BASE/api/v1/auth/login/" >/dev/null
url="$(curl -fs -b "$J" -c "$J" -H "X-CSRFToken: $(csrf)" -H "Origin: $BASE" -H 'Content-Type: application/json' -d '{"action":"text-stats","environment":"test","inputs":{"text":"installation vierge"}}' "$BASE/api/v1/workspaces/demo/executions/" | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')"
for _ in $(seq 1 60); do
  body="$(curl -fs -b "$J" "$BASE$url")"; st="$(python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])' <<<"$body")"
  [ "$st" = succeeded ] || [ "$st" = failed ] && break; sleep 1
done
echo "statut: $st"; python3 -c 'import sys,json;print(json.load(sys.stdin)["result"])' <<<"$body"
curl -fsI "$BASE/" | grep -iE "content-security-policy|x-content-type-options" | head -2
[ "$st" = succeeded ] && echo "PROD SMOKE OK"
