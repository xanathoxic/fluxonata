#!/usr/bin/env bash
# Exercice de restauration sur DONNÉES JETABLES : le script d'essai planifié (`restore-test.sh`) et les scripts de sauvegarde et
# de restauration qu'il appelle tournent dans une COPIE isolée du dépôt (commit courant, ni `.env` ni modifications locales),
# contre une base PostgreSQL JETABLE (conteneur sur un port libre, mot de passe aléatoire) remplie de données de démonstration
# fictives. Rien de la pile de développement ni de vos données n'est lu ou écrit ; rien n'est programmé.
# Vérifie aussi qu'une sauvegarde altérée est REFUSÉE par la restauration.
# Usage : scripts/restore-drill.sh      (Docker requis ; ≈ 2 à 4 min)
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
TMP="$(mktemp -d)"; NAME="fluxonata-drill-$$"; WT="$TMP/src"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; git worktree remove --force "$WT" >/dev/null 2>&1 || true; git worktree prune; rm -rf "$TMP"; }
trap cleanup EXIT
git worktree add -q --detach "$WT" HEAD
PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
docker run -d --name "$NAME" --label fluxonata-drill="$$" -e POSTGRES_USER=fluxonata -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=fluxonata -p 127.0.0.1::5432 postgres:17-alpine >/dev/null
PORT="$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')"
ok=0; for _ in $(seq 1 60); do if docker exec "$NAME" pg_isready -h 127.0.0.1 -U fluxonata -q; then ok=$((ok+1)); [ $ok -ge 3 ] && break; else ok=0; fi; sleep 1; done
[ $ok -ge 3 ] || { echo "ÉCHEC : la base jetable ne démarre pas."; exit 1; }
ln -s "$ROOT/.venv" "$WT/.venv"
cp "$WT/.env.example" "$WT/.env"; chmod 600 "$WT/.env"
export ORCH_DB_HOST=127.0.0.1 ORCH_DB_PORT="$PORT" ORCH_DB_USER=fluxonata ORCH_DB_NAME=fluxonata ORCH_DB_PASSWORD="$PW" ORCH_DB_CONTAINER="$NAME"
export ORCH_SECRET_KEY="drill-$$-not-a-secret" ORCH_KEY_DIR="$TMP/keys" ORCH_ARTIFACTS_DIR="$TMP/artifacts" ORCH_WORK_DIR="$TMP/work"
mkdir -p "$ORCH_KEY_DIR" "$ORCH_ARTIFACTS_DIR" "$ORCH_WORK_DIR"
# les variables exportées l'emportent sur le .env jetable : on y écrit les mêmes valeurs pour que load_env ne les remplace pas
{ for v in ORCH_DB_HOST ORCH_DB_PORT ORCH_DB_USER ORCH_DB_NAME ORCH_DB_PASSWORD ORCH_DB_CONTAINER ORCH_SECRET_KEY ORCH_KEY_DIR ORCH_ARTIFACTS_DIR ORCH_WORK_DIR; do printf '%s=%s\n' "$v" "${!v}"; done; } >> "$WT/.env"
PY="$ROOT/.venv/bin/python"
echo "== données jetables : migrations et démonstration fictive"
( cd "$WT/backend" && "$PY" manage.py migrate --noinput >/dev/null && "$PY" manage.py seed_demo --reset-accounts >/dev/null )
echo "== essai de restauration (le script que le minuteur lance), sur la base jetable"
"$WT/scripts/restore-test.sh"
echo "== une sauvegarde altérée doit être refusée"
"$WT/scripts/backup.sh" "$TMP/bad" >/dev/null
printf 'x' >> "$TMP/bad/db.dump"
if "$WT/scripts/restore.sh" "$TMP/bad" fluxonata_drill_bad >/dev/null 2>&1; then echo "ÉCHEC : une sauvegarde altérée a été acceptée"; exit 1; else echo "  [ok] sauvegarde altérée refusée (code $?)"; fi
echo "EXERCICE DE RESTAURATION OK (données jetables, rien de réel touché)"
