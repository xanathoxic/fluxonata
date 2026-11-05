#!/usr/bin/env bash
# Real kill -9 / database-stop scenarios against the dev stack (~6-8 min for A..H). Needs Docker + demo data.
#   scripts/resilience.sh            tous les scénarios (A à H)
#   scripts/resilience.sh E G H      seulement ceux-là
# A-D : tout tué / worker tué (effet externe, idempotent) / PostgreSQL redémarré. E-H (AR0.4) : orchestrateur seul, API seule,
# PostgreSQL arrêté pendant une rafale de webhooks, deux orchestrateurs dont un tué. Seuils : docs/ROADMAP_ARCHITECTURE.md (AR0.4).
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
exec python3 tests/resilience/scenarios.py "$@"
