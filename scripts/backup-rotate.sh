#!/usr/bin/env bash
# Planned backup with rotation: runs scripts/backup.sh into DIR/<UTC timestamp>, checks the checksums it wrote, then keeps the
# N most recent backups and removes the older ones. Encryption keys are NOT included (see backup.sh): back them up separately.
# Usage: scripts/backup-rotate.sh [--keep N] [--dir DIR] [--prune-only]
set -euo pipefail
source "$(dirname "$0")/_common.sh"; load_env
KEEP=7; DIR="${ORCH_BACKUP_DIR:-$ROOT/backups}"; PRUNE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --prune-only) PRUNE_ONLY=1; shift ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done
[[ "$KEEP" =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 1 ] || { echo "--keep : entier >= 1" >&2; exit 2; }
mkdir -p "$DIR"
if [ "$PRUNE_ONLY" = 0 ]; then
  NEW="$DIR/$(date -u +%Y%m%dT%H%M%SZ)"
  "$ROOT/scripts/backup.sh" "$NEW"
  ( cd "$NEW" && sha256sum -c SHA256SUMS >/dev/null ) || { echo "ÉCHEC : les empreintes de $NEW ne correspondent pas." >&2; exit 1; }
fi
# oldest first; names are UTC timestamps so a plain sort is chronological
mapfile -t ALL < <(find "$DIR" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*T[0-9]*Z' | sort)
EXTRA=$(( ${#ALL[@]} - KEEP ))
for ((i = 0; i < EXTRA; i++)); do rm -rf -- "${ALL[$i]}"; echo "Supprimé : ${ALL[$i]}"; done
echo "Sauvegardes conservées : $(( ${#ALL[@]} - (EXTRA > 0 ? EXTRA : 0) )) (limite $KEEP) dans $DIR"
