#!/usr/bin/env bash
# Proves a backup is restorable: backup -> restore into a scratch DB -> compare row counts -> decrypt a secret with the
# real key directory -> drop the scratch DB. Needs the dev stack (PostgreSQL) and demo data.
source "$(dirname "$0")/_common.sh"; load_env; cd "$ROOT"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"; docker exec -e PGPASSWORD="${ORCH_DB_PASSWORD:-}" fluxonata-db-1 psql -U "${ORCH_DB_USER:-fluxonata}" -d postgres -qc "DROP DATABASE IF EXISTS fluxonata_restore_test" >/dev/null 2>&1' EXIT
PY="$ROOT/.venv/bin/python"
# make sure there is an encrypted secret to verify
(cd backend && "$PY" manage.py shell -c "
from vault.models import Secret
from tenancy.models import Workspace
ws = Workspace.objects.get(slug='demo')
s, _ = Secret.objects.get_or_create(workspace=ws, name='restore-probe')
s.set_value('probe-value-12345'); s.save()" 2>/dev/null)
scripts/backup.sh "$TMP/b" >/dev/null
RESTORE_ARTIFACTS_DIR="$TMP/art" scripts/restore.sh "$TMP/b" fluxonata_restore_test >/dev/null
count() { docker exec -e PGPASSWORD="${ORCH_DB_PASSWORD:-}" fluxonata-db-1 psql -U "${ORCH_DB_USER:-fluxonata}" -d "$1" -Atc "SELECT count(*) FROM $2"; }
ok=1
for t in tenancy_workspace auth_user catalog_action catalog_actionversion workflows_workflow workflows_workflowversion runs_workflowrun runs_execution audit_auditevent vault_secret; do
  a="$(count "${ORCH_DB_NAME:-fluxonata}" $t)"; b="$(count fluxonata_restore_test $t)"
  if [ "$a" = "$b" ]; then echo "  [ok] $t : $a lignes"; else echo "  [KO] $t : source=$a restauré=$b"; ok=0; fi
done
# the restored database must be usable by the application: migrations state + secret decrypts with the key dir
RESULT="$(cd backend && ORCH_DB_NAME=fluxonata_restore_test "$PY" manage.py shell -c "
from vault.models import Secret
print('SECRET_OK' if Secret.objects.get(name='restore-probe').reveal() == 'probe-value-12345' else 'SECRET_BAD')
from django.core.management import call_command
call_command('migrate', interactive=False, verbosity=0)  # standard post-restore step: forward migrations only
from django.db.migrations.executor import MigrationExecutor
from django.db import connection
ex = MigrationExecutor(connection); print('MIGRATIONS_OK' if not ex.migration_plan(ex.loader.graph.leaf_nodes()) else 'MIGRATIONS_PENDING')" 2>/dev/null)"
echo "$RESULT" | sed 's/^/  /'
[[ "$RESULT" == *SECRET_OK* && "$RESULT" == *MIGRATIONS_OK* && $ok = 1 ]] && { echo "RESTORE TEST OK"; exit 0; } || { echo "RESTORE TEST FAILED"; exit 1; }
