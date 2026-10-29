#!/usr/bin/env bash
# Pins the runtime images by DIGEST: prints the immutable reference (repo@digest) of the images currently pulled and, with
# --write [FILE], records them in FILE (default: .env) as ORCH_*_IMAGE so that production runs exactly these images, not
# whatever a tag points to tomorrow. Re-run it (then `docker compose pull`) to move to a patched image on purpose.
# Usage: scripts/pin-images.sh [--write [FILE]]
source "$(dirname "$0")/_common.sh"; load_env
DOCKER_BIN="${DOCKER_BIN:-docker}"
WRITE=0; FILE="$ROOT/.env"
if [ "${1:-}" = "--write" ]; then WRITE=1; [ -n "${2:-}" ] && FILE="$2"; fi
declare -A VARS=(
  ["${ORCH_PYTHON_IMAGE:-python:3.13-slim}"]=ORCH_PYTHON_IMAGE
  ["${ORCH_NODE_IMAGE:-node:22-slim}"]=ORCH_NODE_IMAGE
  ["${ORCH_POWERSHELL_IMAGE:-mcr.microsoft.com/powershell:lts-debian-12}"]=ORCH_POWERSHELL_IMAGE
  ["postgres:17-alpine"]=""
)
for img in "${!VARS[@]}"; do
  d="$("$DOCKER_BIN" image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null || true)"
  printf '%-55s %s\n' "$img" "${d:-(non téléchargée)}"
  var="${VARS[$img]}"
  if [ "$WRITE" = 1 ] && [ -n "$var" ] && [ -n "$d" ]; then
    touch "$FILE"; chmod 600 "$FILE"
    grep -v "^${var}=" "$FILE" > "$FILE.tmp" || true
    printf '%s=%s\n' "$var" "$d" >> "$FILE.tmp"
    mv "$FILE.tmp" "$FILE"; chmod 600 "$FILE"
  fi
done
[ "$WRITE" = 1 ] && echo "Références écrites dans $FILE (images épinglées par empreinte)."
exit 0
