#!/usr/bin/env bash
# Full local CI: format, lint, types, migrations, tests, build, dependency audit, secrets, SBOM.
# Étapes optionnelles : COMPAT_BASE_REF, CI_POOLER=1 (voir plus bas).
# Needs PostgreSQL (scripts/dev-up.sh) and Docker (runtime image). Use --skip-e2e to omit the browser tests.
source "$(dirname "$0")/_common.sh"; cd "$ROOT"; load_env
PY="$ROOT/.venv/bin"
step() { printf '\n== %s\n' "$*"; }
step "ruff format --check";   (cd backend && "$PY/ruff" format --check . ../runner)
step "ruff check";            (cd backend && "$PY/ruff" check . && "$PY/ruff" check --config ruff.toml ../runner)  # runner/ : mêmes règles que le backend (sans cette option, ruff y applique sa configuration par défaut)
# Hors backend/ et runner/ : mêmes règles sauf la famille S (sécurité), dont 8 constats historiques restent à traiter. Le format n'est pas contrôlé ici (dérive historique).
step "ruff check (scripts, workers, tests, examples)"; (cd backend && "$PY/ruff" check --config ruff.toml --extend-ignore S ../scripts ../workers ../tests ../examples)
step "typage mypy (tout le backend applicatif ; baseline vide ; toute erreur est bloquante)"; "$PY/python" scripts/mypy_baseline.py
step "complexité cyclomatique (Ruff C901, seuil 10 ; baseline décroissante : aucune nouvelle fonction trop complexe)"; "$PY/python" scripts/complexity_baseline.py
step "règles d'alerte Prometheus (promtool, séries synthétiques)"; scripts/alerts-test.sh
step "migrations sûres pour une mise à jour progressive"; "$PY/python" scripts/lint_migrations.py
step "migrations à jour";     (cd backend && "$PY/python" manage.py makemigrations --check --dry-run)
step "pytest (-rs : raisons des tests ignorés consignées)"; (cd backend && "$PY/python" -m pytest -rs)
# Optionnels (chacun rejoue une suite complète, ≈ 4 à 12 min) : COMPAT_BASE_REF=<tag ou commit> (AR0.5), CI_POOLER=1 (AR1.2).
[ -z "${COMPAT_BASE_REF:-}" ] || { step "compatibilité : code $COMPAT_BASE_REF sur le schéma courant"; scripts/compat-test.sh "$COMPAT_BASE_REF"; }
[ "${CI_POOLER:-0}" != 1 ] || { step "pytest derrière PgBouncer (mode transaction)"; scripts/pooler-test.sh; }
step "tsc strict + build";    (cd frontend && npm run build)
step "linter frontend (Biome, baseline ; périmètres corrigés bloquants)"; "$PY/python" scripts/lint_frontend_baseline.py
step "mise en forme frontend (Biome, baseline décroissante : tout fichier hors baseline doit être conforme)"; "$PY/python" scripts/format_frontend_baseline.py
step "vitest + couverture (informative, sans seuil)"; (cd frontend && npm run test:coverage)
step "npm audit (prod)";      (cd frontend && npm audit --omit=dev)
step "pip-audit";             (cd backend && "$PY/pip-audit" -r requirements.txt --progress-spinner off)
step "scan de secrets";       python3 scripts/scan-secrets.py
step "SBOM CycloneDX";        mkdir -p artifacts && "$PY/cyclonedx-py" requirements backend/requirements.txt -o artifacts/sbom-python.json --of JSON >/dev/null \
                              && (cd frontend && npm sbom --omit=dev --sbom-format cyclonedx > ../artifacts/sbom-node.json) && ls -la artifacts | tail -2
if [ "${1:-}" != "--skip-e2e" ]; then step "E2E + axe"; scripts/e2e.sh; fi
printf '\nCI OK\n'
