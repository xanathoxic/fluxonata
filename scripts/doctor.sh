#!/usr/bin/env bash
# Read-only diagnostic: prerequisites, config, ports, services, migrations. Never deletes anything.
source "$(dirname "$0")/_common.sh"
cd "$ROOT"; load_env
ok() { echo "  [ok]   $*"; }; ko() { echo "  [KO]   $*"; FAIL=1; }; FAIL=0
echo "Prérequis"
for c in python3 node npm docker git; do command -v $c >/dev/null && ok "$c" || ko "$c manquant"; done
docker info >/dev/null 2>&1 && ok "démon Docker" || ko "démon Docker injoignable (lancez Docker / WSL integration)"
echo "Configuration"
[ -f .env ] && ok ".env présent" || ko ".env absent (lancez scripts/dev-up.sh)"
[ -n "${ORCH_SECRET_KEY:-}" ] && ok "ORCH_SECRET_KEY défini (valeur masquée)" || ko "ORCH_SECRET_KEY vide"
echo "Services"
state="$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep '^db' || true)"
[[ "$state" == *healthy* ]] && ok "PostgreSQL healthy (port ${ORCH_DB_PORT:-5433})" || ko "PostgreSQL non prêt ($state)"
for name in api orchestrator worker web; do is_running "$RUN_DIR/$name.pid" && ok "$name (pid $(cat "$RUN_DIR/$name.pid"))" || ko "$name arrêté"; done
curl -fs "http://127.0.0.1:${ORCH_API_PORT:-8100}/api/v1/auth/csrf/" >/dev/null && ok "API répond" || ko "API ne répond pas"
if [ -x .venv/bin/python ] && [[ "$state" == *healthy* ]]; then
  pending="$(cd backend && "$ROOT"/.venv/bin/python manage.py migrate --check >/dev/null 2>&1 && echo no || echo yes)"
  [ "$pending" = no ] && ok "migrations à jour" || ko "migrations en attente (dev-up.sh les applique)"
fi
docker image inspect "${ORCH_PYTHON_IMAGE:-python:3.13-slim}" >/dev/null 2>&1 && ok "image runtime ${ORCH_PYTHON_IMAGE:-python:3.13-slim}" || ko "image runtime absente (docker pull ${ORCH_PYTHON_IMAGE:-python:3.13-slim})"
exit $FAIL
