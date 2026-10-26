#!/usr/bin/env bash
# Backup: PostgreSQL (custom format) + artifact files. Encryption keys are NOT included unless --with-keys,
# because a backup holding both the data and the keys defeats encryption at rest.
# Usage: scripts/backup.sh [DEST_DIR] [--with-keys]
source "$(dirname "$0")/_common.sh"; load_env
DEST="${1:-$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)}"; [ "${1:-}" = "--with-keys" ] && DEST="$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST" && chmod 700 "$DEST"
DB="${ORCH_DB_NAME:-fluxonata}"; USERNAME="${ORCH_DB_USER:-fluxonata}"
docker exec -e PGPASSWORD="${ORCH_DB_PASSWORD:-}" fluxonata-db-1 pg_dump -U "$USERNAME" -Fc "$DB" > "$DEST/db.dump"
ART="${ORCH_ARTIFACTS_DIR:-$ROOT/var/artifacts}"
[ -d "$ART" ] && tar -C "$ART" -czf "$DEST/artifacts.tar.gz" . || tar -czf "$DEST/artifacts.tar.gz" -T /dev/null
if [[ " $* " == *" --with-keys "* ]]; then
  KEYS="${ORCH_KEY_DIR:-$HOME/.config/fluxonata/keys}"; tar -C "$KEYS" -czf "$DEST/keys.tar.gz" . && chmod 600 "$DEST/keys.tar.gz"
  echo "ATTENTION : keys.tar.gz contient les clés de chiffrement : stockez-le séparément du reste."
else
  echo "Clés de chiffrement NON incluses : sauvegardez ${ORCH_KEY_DIR:-~/.config/fluxonata/keys} séparément (sans elles, les secrets sont irrécupérables)."
fi
( cd "$DEST" && sha256sum db.dump artifacts.tar.gz > SHA256SUMS )
echo "Sauvegarde écrite dans $DEST ($(du -sh "$DEST" | cut -f1)). Les volumes Docker ne protègent pas contre la perte du disque : copiez ce dossier hors de la machine."
