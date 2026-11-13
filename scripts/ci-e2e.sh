#!/usr/bin/env bash
# E2E sur une base JETABLE (CI, pile éphémère) avec un ordre explicite :
#   1 base disponible → 2 migrations appliquées à CETTE base → 3 données de démonstration dans la même base (scripts/e2e.sh)
#   → 4 services prêts (API, orchestrateur, worker, web) → tests navigateur + axe.
# Pourquoi : `scripts/ci.sh` ne migre jamais (pytest crée sa propre base de test) et `scripts/e2e.sh` suppose une pile déjà lancée par
# `scripts/dev-up.sh` ; sur une base vide, `seed_demo` échoue (« relation "tenancy_organization" does not exist ») et aucun service ne tourne.
# Ce script applique des migrations : il ne s'utilise donc JAMAIS sur une base existante et REFUSE de partir sans la déclaration explicite
# CI_DISPOSABLE_DB=1 (posée par le workflow GitHub ; ne la posez pas sur votre pile de développement). Il refuse aussi si les ports
# de l'API ou du web sont déjà occupés (pile de développement en marche). Base, ports et secrets viennent du `.env` / de l'environnement.
# Usage : CI_DISPOSABLE_DB=1 scripts/ci-e2e.sh [arguments de playwright]
source "$(dirname "$0")/_common.sh"; cd "$ROOT"; load_env
[ "${CI_DISPOSABLE_DB:-}" = 1 ] || { echo "Refus : ce script applique des migrations ; déclarez une base jetable avec CI_DISPOSABLE_DB=1 (jamais sur la base de développement)." >&2; exit 2; }
API="${ORCH_API_PORT:-8100}"; WEB="${ORCH_WEB_PORT:-5173}"; PY="$ROOT/.venv/bin/python"
for p in "$API" "$WEB"; do
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}$"; then echo "Refus : le port $p est déjà utilisé (pile en marche ?)." >&2; exit 2; fi
done
OUT="$ROOT/artifacts/ci-e2e"; mkdir -p "$OUT"; rm -f "$OUT"/*.pid
cleanup() { for f in "$OUT"/*.pid; do [ -f "$f" ] && kill -TERM -- "-$(cat "$f")" 2>/dev/null || true; done; }
trap cleanup EXIT
echo "== 1 base disponible"
ready=0; for _ in $(seq 1 30); do
  if (cd backend && "$PY" manage.py shell -c "from django.db import connection; connection.ensure_connection()") >/dev/null 2>&1; then ready=1; break; fi; sleep 2
done
[ "$ready" = 1 ] || { echo "Base injoignable avec les paramètres ORCH_DB_* (hôte ${ORCH_DB_HOST:-?}, port ${ORCH_DB_PORT:-?})." >&2; exit 1; }
echo "== 2 migrations appliquées à la base E2E"
(cd backend && "$PY" manage.py migrate --noinput) > "$OUT/migrate.log" 2>&1 || { tail -20 "$OUT/migrate.log" >&2; exit 1; }
tail -1 "$OUT/migrate.log"
echo "== 3 services (les données de démonstration sont créées par scripts/e2e.sh dans cette même base)"
# Chaque service démarre dans sa propre session (setsid) ; le fichier .pid contient l'identifiant du chef de ce groupe, que `cleanup` arrête
# (et lui seul : jamais un processus que ce script n'a pas créé). Commande simple en arrière-plan : `$!` est bien le chef de groupe.
start() { n=$1; d=$2; shift 2; pushd "$d" >/dev/null; setsid nohup "$@" > "$OUT/$n.log" 2>&1 < /dev/null & echo $! > "$OUT/$n.pid"; popd >/dev/null; }
start api backend "$PY" manage.py runserver "127.0.0.1:$API" --noreload
start orchestrator backend "$PY" manage.py run_orchestrator
start worker backend "$PY" manage.py run_worker
start web frontend npm run dev -- --host 127.0.0.1 --port "$WEB"
echo "== 4 services prêts"
ok=0; for _ in $(seq 1 60); do
  curl -fs -o /dev/null "http://127.0.0.1:$API/api/v1/health/ready/" && curl -fs -o /dev/null "http://127.0.0.1:$WEB/" && { ok=1; break; }; sleep 2
done
[ "$ok" = 1 ] || { echo "Services non prêts (journaux : $OUT)." >&2; tail -5 "$OUT/api.log" >&2; exit 1; }
echo "== E2E + axe"
scripts/e2e.sh "$@"
