#!/usr/bin/env bash
# Idempotent: start PostgreSQL, migrate, seed demo data, start API + worker + web (no duplicates).
source "$(dirname "$0")/_common.sh"
cd "$ROOT"
if [ ! -f .env ]; then
  cp .env.example .env
  key="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')"
  sed -i "s|^ORCH_SECRET_KEY=.*|ORCH_SECRET_KEY=$key|" .env
  echo "→ .env créé (clé secrète générée, non versionné)"
fi
load_env
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt; }
[ -d frontend/node_modules ] || (cd frontend && npm ci)
"${COMPOSE[@]}" up -d --wait db testsvc
(cd backend && "$ROOT"/.venv/bin/python manage.py migrate --noinput >/dev/null && "$ROOT"/.venv/bin/python manage.py seed_demo)
start() { # name, command...
  local name="$1"; shift
  if is_running "$RUN_DIR/$name.pid"; then echo "→ $name déjà lancé (pid $(cat "$RUN_DIR/$name.pid"))"; return; fi
  nohup "$@" >"$RUN_DIR/$name.log" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"; echo "→ $name lancé (pid $!)"
}
start api bash -c "cd '$ROOT/backend' && exec $ROOT/.venv/bin/python manage.py runserver 127.0.0.1:${ORCH_API_PORT:-8100} --noreload"
start orchestrator bash -c "cd '$ROOT/backend' && exec \"$ROOT\"/.venv/bin/python manage.py run_orchestrator"
start worker bash -c "cd '$ROOT/backend' && exec $ROOT/.venv/bin/python manage.py run_worker"
start web bash -c "cd '$ROOT/frontend' && exec npm run dev -- --host 127.0.0.1"
echo "Interface : http://127.0.0.1:${ORCH_WEB_PORT:-5173}  (démo : alice / demo-fluxonata)"
