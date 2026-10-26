#!/usr/bin/env bash
# Full local CI: format, lint, types, migrations, tests, build, dependency audit, secrets, SBOM.
# Needs PostgreSQL (scripts/dev-up.sh) and Docker (runtime image). Use --skip-e2e to omit the browser tests.
source "$(dirname "$0")/_common.sh"; cd "$ROOT"; load_env
PY="$ROOT/.venv/bin"
step() { printf '\n== %s\n' "$*"; }
step "ruff format --check";   (cd backend && "$PY/ruff" format --check . ../runner)
step "ruff check";            (cd backend && "$PY/ruff" check . ../runner)
step "migrations à jour";     (cd backend && "$PY/python" manage.py makemigrations --check --dry-run)
step "pytest";                (cd backend && "$PY/python" -m pytest)
step "tsc strict + build";    (cd frontend && npm run build)
step "vitest";                (cd frontend && npm test)
step "npm audit (prod)";      (cd frontend && npm audit --omit=dev)
step "pip-audit";             (cd backend && "$PY/pip-audit" -r requirements.txt --progress-spinner off)
step "scan de secrets";       python3 scripts/scan-secrets.py
step "SBOM CycloneDX";        mkdir -p artifacts && "$PY/cyclonedx-py" requirements backend/requirements.txt -o artifacts/sbom-python.json --of JSON >/dev/null \
                              && (cd frontend && npm sbom --omit=dev --sbom-format cyclonedx > ../artifacts/sbom-node.json) && ls -la artifacts | tail -2
if [ "${1:-}" != "--skip-e2e" ]; then step "E2E + axe"; scripts/e2e.sh; fi
printf '\nCI OK\n'
