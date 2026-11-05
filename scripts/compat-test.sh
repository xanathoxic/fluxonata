#!/usr/bin/env bash
# AR0.5 — l'ancien code tient-il sur le nouveau schéma ? (condition d'une mise à jour progressive et d'un retour arrière d'image)
#
#   scripts/compat-test.sh <réf-git> [-- options pytest]    ex. : scripts/compat-test.sh v0.1.0   (ou COMPAT_BASE_REF=… sans argument)
#   scripts/compat-test.sh <réf-git> --selftest             prouve que le test détecte une migration incompatible (doit « échouer » dedans)
#
# Déroulé : 1) base de test jetable `test_fluxonata_compat` migrée avec le code COURANT (schéma N+1, comme le service `migrate`
# de la pile de production qui se termine avant tout autre service) ; 2) la suite pytest de l'ANCIEN code (<réf-git>, extrait avec
# `git archive`, jamais dans votre arbre de travail) s'exécute contre cette base, sans la recréer. Un échec = l'ancien code, encore
# en service pendant le déploiement ou après un retour d'image, casse sur le nouveau schéma : la migration doit être scindée
# (expand/contract : ajouter nullable ou avec défaut, ne supprimer/renommer qu'une version plus tard).
#
# Hors périmètre, volontairement : « nouveau code sur ancien schéma ». La pile impose migrate → reste (`service_completed_successfully`),
# donc ce cas ne se produit pas en exploitation normale ; un retour arrière de migration n'est pas une opération supportée (voir UPGRADE.md).
# Limites : les dépendances Python sont celles de l'environnement courant (pas celles de l'ancienne version) ; les tests marqués `docker` sont exclus.
source "$(dirname "$0")/_common.sh"; cd "$ROOT"; load_env
PY="$ROOT/.venv/bin/python"
BASE="${COMPAT_BASE_REF:-}"; SELFTEST=0; PYTEST_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --selftest) SELFTEST=1 ;;
    --) shift; PYTEST_ARGS=("$@"); break ;;
    *) BASE="$1" ;;
  esac
  shift
done
[ -n "$BASE" ] || { echo "usage : scripts/compat-test.sh <réf-git> [--selftest] [-- options pytest]  (ou COMPAT_BASE_REF)" >&2; exit 2; }
git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null || { echo "réf inconnue : $BASE" >&2; exit 2; }

DB=test_fluxonata_compat   # base jetable propre à ce script : jamais la base de développement
TMP="$(mktemp -d)"
sql() {  # sql <base> <instruction> : connexion directe, sans afficher le mot de passe
  "$PY" -I - "$1" "$2" <<'PYEOF'
import os, sys
import psycopg
with psycopg.connect(host=os.environ["ORCH_DB_HOST"], port=os.environ["ORCH_DB_PORT"], user=os.environ["ORCH_DB_USER"],
                     password=os.environ["ORCH_DB_PASSWORD"], dbname=sys.argv[1], autocommit=True) as c:
    c.execute(sys.argv[2])
PYEOF
}
cleanup() { sql postgres "DROP DATABASE IF EXISTS $DB WITH (FORCE)" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== schéma du code courant ($(git rev-parse --short HEAD) + arbre de travail)"
sql postgres "DROP DATABASE IF EXISTS $DB WITH (FORCE)"
sql postgres "CREATE DATABASE $DB"
(cd backend && ORCH_DB_NAME="$DB" "$PY" manage.py migrate --noinput 2>&1 | tail -1)
if [ "$SELFTEST" = 1 ]; then
  echo "== auto-test : colonne NOT NULL sans défaut ajoutée à tenancy_workspace (migration volontairement hostile)"
  sql "$DB" "ALTER TABLE tenancy_workspace ADD COLUMN compat_selftest text NOT NULL"
  PYTEST_ARGS=(tests/test_api.py -x)
fi

echo "== ancien code ($(git rev-parse --short "$BASE")) contre ce schéma"
mkdir -p "$TMP/old"
# Tout le dépôt sauf le front (plusieurs tests lisent scripts/, infra/, examples/, runner/ à côté de backend/).
git archive "$BASE" -- . ':(exclude)frontend' | tar -x -C "$TMP/old"
set +e
(cd "$TMP/old/backend" && PYTHONPATH="$ROOT/scripts" ORCH_DB_NAME=fluxonata_compat "$PY" -m pytest -p compat_plugin --reuse-db -p no:cacheprovider -m "not docker" "${PYTEST_ARGS[@]}")
rc=$?
set -e
if [ "$SELFTEST" = 1 ]; then
  [ $rc -ne 0 ] && { echo "AUTO-TEST OK : la migration hostile a bien été détectée (pytest a échoué comme attendu)."; exit 0; }
  echo "AUTO-TEST ÉCHOUÉ : la migration hostile n'a pas été détectée." >&2; exit 1
fi
[ $rc -eq 0 ] && echo "COMPAT OK : l'ancien code ($BASE) passe sur le schéma courant." || echo "COMPAT ÉCHEC : l'ancien code ($BASE) casse sur le schéma courant (code $rc)." >&2
exit $rc
