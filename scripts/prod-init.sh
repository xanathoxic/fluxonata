#!/usr/bin/env bash
# Generate infra/.env.prod with random secrets (idempotent: never overwrites an existing file).
source "$(dirname "$0")/_common.sh"
F="$ROOT/infra/.env.prod"
agent_token() { echo "orr_$(python3 -c 'import secrets;print(secrets.token_urlsafe(40))')"; }
if [ -f "$F" ]; then
  # Installation existante : on ne touche à rien, sauf à AJOUTER le jeton de l'agent d'exécution s'il manque (topologie par agents, ADR 0023).
  grep -q '^ORCH_EXEC_AGENT_TOKEN=' "$F" || { echo "ORCH_EXEC_AGENT_TOKEN=$(agent_token)" >> "$F"; echo "Jeton de l'agent d'exécution ajouté à $F."; }
  echo "$F existe déjà : conservé."; exit 0
fi
cp "$ROOT/infra/.env.prod.example" "$F"; chmod 600 "$F"
rand() { python3 -c 'import secrets;print(secrets.token_urlsafe(40))'; }
sed -i "s|^ORCH_SECRET_KEY=.*|ORCH_SECRET_KEY=$(rand)|; s|^ORCH_DB_PASSWORD=.*|ORCH_DB_PASSWORD=$(rand)|" "$F"
WORK="${ORCH_WORK_HOST_DIR:-$ROOT/var/prod-work}"; mkdir -p "$WORK"; chmod 755 "$WORK"
sed -i "s|^ORCH_WORK_DIR=.*|ORCH_WORK_DIR=$WORK|" "$F"
sed -i "s|^ORCH_EXEC_AGENT_TOKEN=.*|ORCH_EXEC_AGENT_TOKEN=$(agent_token)|" "$F"
echo "Créé : $F (valeurs aléatoires, droits 600, ignoré par Git)."
