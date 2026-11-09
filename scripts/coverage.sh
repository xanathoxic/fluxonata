#!/usr/bin/env bash
# Mesure de couverture du backend (politique d'ingénierie du projet) — INFORMATIVE : aucun seuil ne bloque.
# Environnement ISOLÉ : copie propre du commit courant (worktree détaché : ni .env, ni modifications locales), venv temporaire,
# PostgreSQL jetable (conteneur postgres:17-alpine sur un port libre, mot de passe aléatoire). Rien de la pile de développement n'est utilisé.
# Usage : scripts/coverage.sh [--write-baseline]     (Docker requis ; ≈ 15 à 40 min). Ne pas lancer en parallèle d'une autre suite.
# Limite : seul le processus pytest est mesuré (les processus lancés par les tests, par exemple un worker, ne le sont pas).
# Variables : COV_PYTHON (interpréteur, défaut python3 ; la CI vise 3.13, un autre écart est signalé et consigné dans la baseline),
#             COV_PYTEST_ARGS (sous-ensemble de tests pour valider le script : la baseline ne peut alors PAS être écrite).
# Journalisé à chaque mesure : tests passés / ignorés (avec raison) / xfail, motifs omis de la mesure, nombre de fichiers mesurés.
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
WRITE=0; [ "${1:-}" = "--write-baseline" ] && WRITE=1
PY="${COV_PYTHON:-python3}"; read -r -a EXTRA <<<"${COV_PYTEST_ARGS:-}"
[ "$WRITE" = 1 ] && [ "${#EXTRA[@]}" -gt 0 ] && { echo "Refus : --write-baseline avec COV_PYTEST_ARGS mesurerait un sous-ensemble de la suite." >&2; exit 2; }
CI_PY="$(sed -n 's/.*python-version: *"\([0-9.]*\)".*/\1/p' "$ROOT/.github/workflows/ci.yml" | head -1)"
TMP="$(mktemp -d)"; NAME="fluxonata-cov-$$"; WT="$TMP/src"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; git worktree remove --force "$WT" >/dev/null 2>&1 || true; git worktree prune; rm -rf "$TMP"; }
trap cleanup EXIT
git worktree add -q --detach "$WT" HEAD
COMMIT="$(git rev-parse --short HEAD)"
echo "== environnement isolé (commit $COMMIT, $("$PY" --version))"
case "$("$PY" -c 'import platform;print(platform.python_version())')" in
  "$CI_PY".*) ;;
  *) echo "ATTENTION : interpréteur $("$PY" --version) alors que la CI vise Python ${CI_PY:-?} ; la mesure n'est pas celle de la CI (COV_PYTHON=...)." >&2 ;;
esac
"$PY" -m venv "$TMP/venv"
"$TMP/venv/bin/pip" install -q --disable-pip-version-check -r "$WT/backend/requirements.txt" -r "$WT/backend/requirements-dev.txt" -r "$ROOT/backend/requirements-coverage.txt"
PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
docker run -d --name "$NAME" --label fluxonata-coverage="$$" -e POSTGRES_USER=fluxonata -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=fluxonata -p 127.0.0.1::5432 postgres:17-alpine >/dev/null
PORT="$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')"
READY=0
for _ in $(seq 1 60); do docker exec "$NAME" pg_isready -h 127.0.0.1 -U fluxonata -q && { READY=1; break; }; sleep 1; done
[ "$READY" = 1 ] || { echo "PostgreSQL jetable non prêt après 60 s : mesure abandonnée (aucun résultat de test à interpréter)." >&2; exit 1; }
set -a; . "$WT/.env.example"; set +a   # mêmes réglages que la CI (.env.example)
export ORCH_DB_HOST=127.0.0.1 ORCH_DB_PORT="$PORT" ORCH_DB_USER=fluxonata ORCH_DB_PASSWORD="$PW" ORCH_DB_NAME=fluxonata
ORCH_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')"; export ORCH_SECRET_KEY
echo "== pytest avec couverture"
set +e
(cd "$WT/backend" && "$TMP/venv/bin/python" -B -m pytest -rs -p no:cacheprovider --cov --cov-config="$ROOT/backend/.coveragerc" --cov-report="json:$TMP/coverage.json" --cov-report= --junitxml="$TMP/junit.xml" "${EXTRA[@]}")
RC=$?
set -e
[ -f "$TMP/coverage.json" ] || { echo "Aucun rapport de couverture produit (pytest code $RC)." >&2; exit 1; }
ARGS=("$TMP/coverage.json" --commit "$COMMIT" --python "$("$TMP/venv/bin/python" -c 'import platform;print(platform.python_version())')" --pytest-exit "$RC" --junit "$TMP/junit.xml" --coveragerc "$ROOT/backend/.coveragerc" --baseline "$ROOT/backend/tests/fixtures/coverage_baseline.json")
[ "${#EXTRA[@]}" -gt 0 ] && ARGS+=(--subset)
[ "$WRITE" = 1 ] && ARGS+=(--write)
echo "== résumé"; python3 "$ROOT/scripts/coverage_report.py" "${ARGS[@]}" || exit $?
exit "$RC"
