#!/usr/bin/env bash
# Idempotent: stop API/worker/web. Keeps the database volume. Use --db to also stop PostgreSQL (data kept).
source "$(dirname "$0")/_common.sh"
# Stop a process AND all its descendants (npm -> sh -> node vite): killing only the wrapper leaks the real server.
kill_tree() { local p="$1" c; for c in $(pgrep -P "$p" 2>/dev/null); do kill_tree "$c"; done; kill "$p" 2>/dev/null || true; }
for name in web demo-api worker orchestrator api; do
  if is_running "$RUN_DIR/$name.pid"; then
    pid="$(cat "$RUN_DIR/$name.pid")"; kill_tree "$pid"
    echo "→ $name arrêté"
  fi
  rm -f "$RUN_DIR/$name.pid"
done
[ "${1:-}" = "--db" ] && "${COMPOSE[@]}" stop db && echo "→ PostgreSQL arrêté (volume conservé)"
exit 0
