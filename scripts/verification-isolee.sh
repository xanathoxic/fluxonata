#!/usr/bin/env bash
# Vérification intégrée ISOLÉE : exécute `scripts/ci.sh` (E2E + axe compris) sur une copie propre d'un commit exact, avec une base,
# un réseau interne et des services JETABLES. Ne touche ni à la pile de développement, ni à sa base, ni aux comptes de démonstration,
# ni aux modifications non validées de l'arbre de travail. Les images PostgreSQL et Python sont celles épinglées par infra/docker-compose.yml.
# ÉTAT : écrit le 2026-11-13, NON EXÉCUTÉ (machine saturée) ; seuls `bash -n` et `--plan` ont été essayés.
# Usage : scripts/verification-isolee.sh [--commit REF] [--skip-e2e] [--plan] [--force]
#   --plan : affiche les étapes et les seuils, ne crée rien.   --force : outrepasse le refus ressources (à éviter).
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
REF=HEAD; SKIP=(); PLAN=0; FORCE=0
while [ $# -gt 0 ]; do case "$1" in
  --commit) REF="${2:?--commit exige une référence}"; shift;; --skip-e2e) SKIP=(--skip-e2e);; --plan) PLAN=1;; --force) FORCE=1;;
  *) echo "option inconnue : $1" >&2; exit 2;; esac; shift; done
SHA="$(git rev-parse --verify "$REF^{commit}")" || exit 2
img() { sed -n "s/^ *image: *\\(${1}[^ ]*\\)$/\\1/p" infra/docker-compose.yml | head -1; }
PG_IMG="$(img postgres)"; PY_IMG="$(img python)"
[ -n "$PG_IMG" ] && [ -n "$PY_IMG" ] || { echo "images épinglées introuvables dans infra/docker-compose.yml" >&2; exit 2; }
problems=()
swap_pct="$(awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2} END{ if (t>0) printf "%d", (t-f)*100/t; else print 0 }' /proc/meminfo)"
avail_mb="$(awk '/^MemAvailable:/{printf "%d", $2/1024}' /proc/meminfo)"
psi="$(awk -F'[ =]' '/^some/{print int($5)}' /proc/pressure/memory 2>/dev/null)"; psi="${psi:-0}"
[ "$swap_pct" -gt 50 ] && problems+=("swap utilisé à ${swap_pct} % (seuil 50 %)")
[ "$avail_mb" -lt 4096 ] && problems+=("mémoire disponible ${avail_mb} Mo (minimum 4096 Mo)")
[ "$psi" -gt 5 ] && problems+=("pression mémoire ${psi} % sur 60 s (seuil 5 %)")
command -v docker >/dev/null || problems+=("Docker introuvable")
if [ "$PLAN" = 1 ]; then
  cat <<PLANEOF
Commit vérifié   : ${SHA:0:12} (copie détachée, sans modification de l'arbre de travail)
Images jetables  : $PG_IMG
                   $PY_IMG
Étapes           : 1 copie propre du commit  2 base PostgreSQL jetable + réseau interne + service de test
                   3 migrations + comptes de démonstration SUR LA BASE JETABLE  4 API/orchestrateur/worker/vite sur ports libres
                   5 scripts/ci.sh ${SKIP[*]:-(E2E + axe inclus)}  6 nettoyage des seuls objets étiquetés créés ici
Journaux         : artifacts/verification-isolee-<horodatage>/ (ignoré par Git)
Seuils           : swap <= 50 %, MemAvailable >= 4096 Mo, PSI memory some avg60 <= 5 %, Docker présent
Mesures actuelles: swap ${swap_pct} %, MemAvailable ${avail_mb} Mo, PSI ${psi} %  -> $([ "${#problems[@]}" -gt 0 ] && echo "REFUS : ${problems[*]}" || echo ACCEPTÉ)
PLANEOF
  exit 0
fi
if [ "${#problems[@]}" -gt 0 ]; then
  printf 'MACHINE NON ADAPTÉE :\n'; printf '  - %s\n' "${problems[@]}"
  [ "$FORCE" = 0 ] && { echo "Refus (rien créé)." >&2; exit 3; }
  echo "ATTENTION : --force, résultats à interpréter avec prudence." >&2
fi
set -uo pipefail
TS="$(date -u +%Y%m%dT%H%M%SZ)"; ID="$TS-$$"; OUT="$ROOT/artifacts/verification-isolee-$TS"; W="$(mktemp -d)"; TREE="$W/tree"
NET="fluxiso-$ID"; mkdir -p "$OUT"; PIDS=()
say() { echo "[$(date +%T)] $*" | tee -a "$OUT/journal.txt"; }
cleanup() {
  for p in "${PIDS[@]}"; do kill -TERM -- "-$p" 2>/dev/null; done; sleep 2
  for c in $(docker ps -aq --filter "label=fluxiso=$ID"); do docker rm -f "$c" >/dev/null 2>&1; done
  docker network rm "$NET" >/dev/null 2>&1
  git -C "$ROOT" worktree remove --force "$TREE" >/dev/null 2>&1; rm -rf "$W"
  say "nettoyage terminé (conteneurs restants : $(docker ps -aq --filter "label=fluxiso=$ID" | wc -l))"
}
trap cleanup EXIT
free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
git worktree add --detach "$TREE" "$SHA" >/dev/null && ln -s "$ROOT/.venv" "$TREE/.venv" || exit 2
say "commit ${SHA:0:12} ; images $PG_IMG ; $PY_IMG"
PGPW="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"; API="$(free_port)"; WEB="$(free_port)"
docker network create --internal --label "fluxiso=$ID" "$NET" >/dev/null
docker run -d --name "fluxiso-testsvc-$ID" --label "fluxiso=$ID" --network "$NET" --network-alias testsvc --read-only --user 65534:65534 \
  --cap-drop ALL --security-opt no-new-privileges:true -v "$TREE/infra/testsvc/server.py:/srv/server.py:ro" "$PY_IMG" python /srv/server.py >/dev/null
docker run -d --name "fluxiso-pg-$ID" --label "fluxiso=$ID" -p 127.0.0.1::5432 -e POSTGRES_USER=fluxonata -e POSTGRES_PASSWORD="$PGPW" \
  -e POSTGRES_DB=fluxonata "$PG_IMG" >/dev/null
ok=0; for _ in $(seq 1 60); do if docker exec "fluxiso-pg-$ID" pg_isready -h 127.0.0.1 -U fluxonata -q; then ok=$((ok+1)); [ $ok -ge 3 ] && break; else ok=0; fi; sleep 1; done
[ $ok -ge 3 ] || { say "base jetable non prête"; exit 4; }
PGPORT="$(docker port "fluxiso-pg-$ID" 5432/tcp | head -1 | sed 's/.*://')"
cp "$TREE/.env.example" "$TREE/.env"
cat >> "$TREE/.env" <<ENVEOF
ORCH_SECRET_KEY=iso-$ID-not-a-secret
ORCH_DB_HOST=127.0.0.1
ORCH_DB_PORT=$PGPORT
ORCH_DB_PASSWORD=$PGPW
ORCH_API_PORT=$API
ORCH_WEB_PORT=$WEB
ORCH_KEY_DIR=$W/keys
ORCH_WORK_DIR=$W/work
ORCH_EGRESS_TEST_NETWORK=$NET
ENVEOF
set -a; . "$TREE/.env"; set +a
PY="$TREE/.venv/bin/python"; mkdir -p "$W/keys" "$W/work"
( cd "$TREE/backend" && "$PY" manage.py migrate --noinput && "$PY" manage.py seed_demo --reset-accounts ) > "$OUT/preparation.log" 2>&1 || { say "préparation en échec (voir preparation.log)"; exit 4; }
start() { n=$1; shift; setsid nohup "$@" > "$OUT/$n.log" 2>&1 < /dev/null & PIDS+=("$!"); }
( cd "$TREE/backend" && start api "$PY" manage.py runserver "127.0.0.1:$API" --noreload; start orchestrator "$PY" manage.py run_orchestrator; start worker "$PY" manage.py run_worker )
( cd "$TREE/frontend" && { [ -d node_modules ] || npm ci --no-audit --no-fund > "$OUT/npm-ci.log" 2>&1; } && start web npm run dev -- --host 127.0.0.1 --port "$WEB" )
for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$API/api/v1/health/ready/" && curl -s -o /dev/null "http://127.0.0.1:$WEB/" && break; sleep 2; done
LIBS="$ROOT/.run/libs/root/usr/lib/x86_64-linux-gnu"; [ -d "$LIBS" ] && export LD_LIBRARY_PATH="$LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
say "début ci.sh ${SKIP[*]:-}"
( cd "$TREE" && scripts/ci.sh "${SKIP[@]}" ) 2>&1 | tee "$OUT/ci.log"; rc=${PIPESTATUS[0]}
say "fin : code de sortie de ci.sh = $rc ; journaux : $OUT"
exit "$rc"
