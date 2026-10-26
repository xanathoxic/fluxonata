#!/usr/bin/env bash
# Real kill -9 / database-restart scenarios against the dev stack (~3-4 min). Needs Docker + demo data.
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
exec python3 tests/resilience/scenarios.py
