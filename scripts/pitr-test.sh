#!/usr/bin/env bash
# AR5.5 — restauration À UN INSTANT DONNÉ (PITR), prouvée sur de vrais conteneurs PostgreSQL JETABLES (jamais la base de développement ni de production).
#
#   scripts/pitr-test.sh
#
# Déroulé : PostgreSQL 17 avec archivage continu des journaux de transactions (WAL) → sauvegarde de base → des données « avant » → un instant T → des données « après » →
# un SINISTRE (suppression) → restauration d'une copie de la sauvegarde de base JUSQU'À T → vérification : les données « avant » sont là, les « après » et le sinistre non.
# Mesure : le temps de restauration (RTO) et la fenêtre de perte (RPO) = tout ce qui a été écrit après T.
# Ce que cela NE prouve pas : la tenue sur une vraie volumétrie, l'archivage vers un stockage distant, l'automatisation (l'archive est ici un volume Docker local).
source "$(dirname "$0")/_common.sh"
IMG="${PITR_IMAGE:-postgres:17-alpine}"; P="fluxonata-pitr-$$"
cleanup() { docker rm -f "$P-src" "$P-restore" >/dev/null 2>&1; docker volume rm "$P-data" "$P-archive" "$P-base" "$P-rdata" >/dev/null 2>&1; }
trap cleanup EXIT
psqls() { docker exec -u postgres "$1" psql -qAt -v ON_ERROR_STOP=1 -c "$2"; }
wait_ready() { for _ in $(seq 1 60); do docker exec -u postgres "$1" pg_isready -h 127.0.0.1 -q && return 0; sleep 1; done; echo "PostgreSQL ($1) ne démarre pas"; docker logs --tail 15 "$1"; return 1; }
docker volume create "$P-data" >/dev/null; docker volume create "$P-archive" >/dev/null; docker volume create "$P-base" >/dev/null; docker volume create "$P-rdata" >/dev/null
# permissions du volume d'archive : l'utilisateur postgres de l'image doit pouvoir y écrire
docker run --rm -v "$P-archive:/archive" -v "$P-base:/base" "$IMG" sh -c 'chown postgres:postgres /archive /base && chmod 700 /archive /base'

echo "== 1. PostgreSQL avec archivage continu des WAL"
docker run -d --name "$P-src" -e POSTGRES_PASSWORD=pitr -v "$P-data:/var/lib/postgresql/data" -v "$P-archive:/archive" "$IMG" \
  -c wal_level=replica -c archive_mode=on -c "archive_command=test ! -f /archive/%f && cp %p /archive/%f" -c archive_timeout=5 >/dev/null
wait_ready "$P-src"
psqls "$P-src" "CREATE TABLE evenements (id serial PRIMARY KEY, lot text, ecrit_le timestamptz DEFAULT clock_timestamp())"
psqls "$P-src" "INSERT INTO evenements (lot) SELECT 'avant' FROM generate_series(1,100)"

echo "== 2. sauvegarde de base (pg_basebackup, pendant que la base vit)"
docker exec -u postgres "$P-src" pg_basebackup -h 127.0.0.1 -D /tmp/base -Ft -z -X none -c fast >/dev/null
BASE_DIR="$(mktemp -d)"; docker cp "$P-src:/tmp/base/." "$BASE_DIR"; ls "$BASE_DIR" | tr '\n' ' '; echo

echo "== 3. des données APRÈS la sauvegarde, puis l'instant visé T"
psqls "$P-src" "INSERT INTO evenements (lot) SELECT 'apres-sauvegarde' FROM generate_series(1,50)"
sleep 2
T="$(psqls "$P-src" "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00'")"
echo "   T = $T"
sleep 2
psqls "$P-src" "INSERT INTO evenements (lot) SELECT 'apres-T' FROM generate_series(1,30)"
psqls "$P-src" "SELECT pg_switch_wal()" >/dev/null
echo "== 4. SINISTRE : suppression des données"
psqls "$P-src" "DELETE FROM evenements"; psqls "$P-src" "SELECT pg_switch_wal()" >/dev/null
for _ in $(seq 1 30); do [ "$(docker exec "$P-src" sh -c 'ls /archive | wc -l')" -ge 3 ] && break; sleep 1; done
echo "   après sinistre : $(psqls "$P-src" 'SELECT count(*) FROM evenements') ligne(s) ; WAL archivés : $(docker exec "$P-src" sh -c 'ls /archive | grep -vc history')"

echo "== 5. restauration jusqu'à T (copie de la sauvegarde de base + WAL archivés)"
T0=$SECONDS
docker run --rm -v "$P-rdata:/restore" -v "$BASE_DIR:/base:ro" "$IMG" sh -c 'cd /restore && tar xzf /base/base.tar.gz && chown -R postgres:postgres /restore && chmod 700 /restore'
docker run --rm -v "$P-rdata:/restore" "$IMG" sh -c 'touch /restore/recovery.signal && chown postgres:postgres /restore/recovery.signal'
docker run -d --name "$P-restore" -v "$P-rdata:/var/lib/postgresql/data" -v "$P-archive:/archive:ro" "$IMG" \
  -c "restore_command=cp /archive/%f %p" -c "recovery_target_time=$T" -c recovery_target_action=promote -c recovery_target_inclusive=true >/dev/null
wait_ready "$P-restore"
for _ in $(seq 1 30); do [ "$(psqls "$P-restore" 'SELECT pg_is_in_recovery()')" = f ] && break; sleep 1; done
RTO=$((SECONDS - T0))
avant="$(psqls "$P-restore" "SELECT count(*) FROM evenements WHERE lot='avant'")"; entre="$(psqls "$P-restore" "SELECT count(*) FROM evenements WHERE lot='apres-sauvegarde'")"
apres="$(psqls "$P-restore" "SELECT count(*) FROM evenements WHERE lot='apres-T'")"
echo "   restauré : avant=$avant (attendu 100) · après la sauvegarde mais avant T=$entre (attendu 50) · après T=$apres (attendu 0 : perte voulue, c'est le RPO) · temps de restauration (RTO) = ${RTO}s"
[ "$avant" = 100 ] && [ "$entre" = 50 ] && [ "$apres" = 0 ] && echo "PITR OK : base restaurée à T, sinistre et écritures postérieures exclus" || { echo "PITR ÉCHEC"; docker logs --tail 20 "$P-restore"; exit 1; }
