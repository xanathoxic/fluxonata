#!/usr/bin/env bash
# Restore a backup into a NEW database (never overwrites the live one).
# Usage: scripts/restore.sh BACKUP_DIR [TARGET_DB=fluxonata_restore]
# Switching the application to the restored database is a deliberate, manual step (set ORCH_DB_NAME).
source "$(dirname "$0")/_common.sh"; load_env
SRC="${1:?usage: restore.sh BACKUP_DIR [TARGET_DB]}"; TARGET="${2:-fluxonata_restore}"
[ "$TARGET" = "${ORCH_DB_NAME:-fluxonata}" ] && { echo "Refus : la base cible est la base active ($TARGET). Choisissez un autre nom."; exit 2; }
( cd "$SRC" && sha256sum -c SHA256SUMS ) || { echo "Somme de contrôle invalide : sauvegarde corrompue."; exit 3; }
PSQL=(docker exec -i -e PGPASSWORD="${ORCH_DB_PASSWORD:-}" fluxonata-db-1)
"${PSQL[@]}" psql -U "${ORCH_DB_USER:-fluxonata}" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$TARGET\"" -c "CREATE DATABASE \"$TARGET\""
"${PSQL[@]}" pg_restore -U "${ORCH_DB_USER:-fluxonata}" -d "$TARGET" --no-owner < "$SRC/db.dump"
echo "Base restaurée dans « $TARGET »."
if [ -f "$SRC/artifacts.tar.gz" ]; then
  OUT="${RESTORE_ARTIFACTS_DIR:-$ROOT/var/artifacts-restored}"; mkdir -p "$OUT"; tar -C "$OUT" -xzf "$SRC/artifacts.tar.gz" && echo "Artefacts restaurés dans $OUT."
fi
