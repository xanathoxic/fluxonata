#!/usr/bin/env bash
# AR1.2 — la suite pytest derrière un vrai PgBouncer en mode transaction (celui de la pile de production, même image épinglée).
#
#   scripts/pooler-test.sh [-- options pytest]     ex. : scripts/pooler-test.sh -- tests/test_engine.py -q
#
# Démarre un conteneur PgBouncer temporaire (réseau de l'hôte, 127.0.0.1:6432) devant la PostgreSQL de développement (scripts/dev-up.sh), lance
# pytest avec ORCH_DB_POOLED=1 sur une base de test jetable `test_fluxonata_pooler`, puis supprime le conteneur et la base.
# Ce que cela prouve : qu'aucun test ne dépend d'un état de session (curseurs serveur, instructions préparées, SET) ni d'un verrou qui
# survivrait à une transaction. Ce que cela ne prouve pas : le dimensionnement du pool, ni la tenue en charge (banc AR0.3).
source "$(dirname "$0")/_common.sh"; cd "$ROOT"; load_env
PY="$ROOT/.venv/bin/python"
PORT="${POOLER_TEST_PORT:-6432}"; NAME=fluxonata-pooler-test; DB=test_fluxonata_pooler
IMG="$(grep -oE 'edoburu/pgbouncer@sha256:[0-9a-f]{64}' infra/docker-compose.prod.yml | head -1)"
[ -n "$IMG" ] || { echo "image PgBouncer introuvable dans infra/docker-compose.prod.yml" >&2; exit 2; }
PYTEST_ARGS=(); [ "${1:-}" = "--" ] && { shift; PYTEST_ARGS=("$@"); }

sql() {  # connexion DIRECTE à PostgreSQL (jamais via le pooler) : sql <base> <instruction>
  "$PY" -I - "$1" "$2" <<'PYEOF'
import os, sys
import psycopg
with psycopg.connect(host=os.environ["ORCH_DB_HOST"], port=os.environ["ORCH_DB_PORT"], user=os.environ["ORCH_DB_USER"],
                     password=os.environ["ORCH_DB_PASSWORD"], dbname=sys.argv[1], autocommit=True) as c:
    c.execute(sys.argv[2])
PYEOF
}
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; sql postgres "DROP DATABASE IF EXISTS $DB WITH (FORCE)" 2>/dev/null || true; }
trap cleanup EXIT
cleanup

# PgBouncer rejoint le réseau de la pile de développement (`db:5432`) et publie 127.0.0.1:$PORT : le réseau « host » de Docker Desktop est
# celui de sa VM, pas celui de WSL. Le mot de passe passe par l'environnement du processus `docker run` (jamais sur la ligne de commande).
NET="${POOLER_TEST_NETWORK:-fluxonata_default}"
docker network inspect "$NET" >/dev/null 2>&1 || { echo "réseau $NET absent : lancez scripts/dev-up.sh" >&2; exit 2; }
export DATABASE_URL="postgres://$ORCH_DB_USER:$ORCH_DB_PASSWORD@db:5432"
docker run -d --name "$NAME" --network "$NET" -p "127.0.0.1:$PORT:5432" -e DATABASE_URL \
  -e AUTH_TYPE=scram-sha-256 -e POOL_MODE=transaction -e MAX_CLIENT_CONN=200 -e DEFAULT_POOL_SIZE=10 -e ADMIN_USERS="$ORCH_DB_USER" \
  "$IMG" >/dev/null
unset DATABASE_URL
for _ in $(seq 1 30); do docker exec "$NAME" pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && break; sleep 1; done
docker exec "$NAME" pg_isready -h 127.0.0.1 -p 5432 >/dev/null || { docker logs "$NAME" | tail -5; exit 1; }
echo "== PgBouncer $(docker exec "$NAME" pgbouncer --version | head -1 | cut -d' ' -f2) en mode transaction sur 127.0.0.1:$PORT (serveur : db:5432)"

set +e
(cd backend && ORCH_TEST_TARGET_DB_HOST="$ORCH_DB_HOST" ORCH_TEST_TARGET_DB_PORT="$ORCH_DB_PORT" \
   ORCH_DB_HOST=127.0.0.1 ORCH_DB_PORT="$PORT" ORCH_DB_POOLED=1 ORCH_DB_NAME=fluxonata_pooler \
   "$PY" -m pytest --reuse-db -p no:cacheprovider "${PYTEST_ARGS[@]}")
rc=$?
set -e
if [ $rc -eq 0 ]; then echo "POOLER OK"; else echo "POOLER ÉCHEC (code $rc) — fin du journal de PgBouncer :" >&2; docker logs --tail 15 "$NAME" 2>&1 | cut -c1-200 >&2; docker ps -a --filter "name=$NAME" --format "état du conteneur : {{.Status}}" >&2; fi
exit $rc
