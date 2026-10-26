#!/usr/bin/env bash
# Regenerate docs/img/*.png from the running dev stack (fictional demo data only). Needs scripts/dev-up.sh.
source "$(dirname "$0")/_common.sh"
SHOTS=1 exec "$ROOT/scripts/e2e.sh" e2e/screenshots.spec.ts
