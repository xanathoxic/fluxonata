#!/usr/bin/env bash
# Print a factual checkpoint (git state + services). Append to the session log with: scripts/checkpoint.sh --log "note"
source "$(dirname "$0")/_common.sh"
cd "$ROOT"
out="$(date -u +%Y-%m-%dT%H:%M:%SZ) | branche=$(git branch --show-current 2>/dev/null) commit=$(git rev-parse --short HEAD 2>/dev/null || echo none) | non commités=$(git status --porcelain 2>/dev/null | wc -l)"
echo "$out"
if [ "${1:-}" = "--log" ]; then echo "- CHECKPOINT $out — ${2:-sans note}" >>docs/SESSION_LOG.md; echo "(ajouté à docs/SESSION_LOG.md)"; fi
