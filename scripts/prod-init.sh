#!/usr/bin/env bash
# Génère infra/.env.prod (secrets aléatoires, jamais écrasé) et, pour la topologie par agents, infra/.env.agent (jeton de l'agent, chargé par `exec-agent` SEUL).
# Usage : scripts/prod-init.sh [--migrate-agent-token | --rotate-agent-token]
#   (sans option)          installation existante : rien n'est modifié ; un jeton d'agent hérité dans .env.prod est SIGNALÉ, pas déplacé
#   --migrate-agent-token  transition : déplace le jeton hérité de .env.prod vers .env.agent (même valeur : le runner enregistré reste valable) et le retire de .env.prod
#   --rotate-agent-token   remplace le jeton de .env.agent par un nouveau ; ensuite : scripts/agent-register.sh puis redémarrer exec-agent
source "$(dirname "$0")/_common.sh"
F="$ROOT/infra/.env.prod"; A="$ROOT/infra/.env.agent"
MIGRATE=0; ROTATE=0
for a in "$@"; do case "$a" in --migrate-agent-token) MIGRATE=1 ;; --rotate-agent-token) ROTATE=1 ;; *) echo "option inconnue : $a" >&2; exit 2 ;; esac; done
rand() { python3 -c 'import secrets;print(secrets.token_urlsafe(40))'; }
agent_token() { echo "orr_$(rand)"; }
write_agent() { ( umask 077; printf 'ORCH_RUNNER_TOKEN=%s\n' "$1" > "$A" ); chmod 600 "$A"; }   # le jeton n'apparaît jamais dans une ligne de commande visible
legacy() { grep -q '^ORCH_EXEC_AGENT_TOKEN=' "$F" 2>/dev/null; }
if [ -f "$F" ]; then
  if [ "$ROTATE" = 1 ]; then
    write_agent "$(agent_token)"; echo "Nouveau jeton d'agent écrit dans $A. Suite : scripts/agent-register.sh, puis redémarrer exec-agent."
    if legacy; then sed -i '/^ORCH_EXEC_AGENT_TOKEN=/d' "$F"; echo "Ancienne entrée ORCH_EXEC_AGENT_TOKEN retirée de $F."; fi
  elif [ "$MIGRATE" = 1 ]; then
    if [ -f "$A" ]; then echo "$A existe déjà : rien déplacé." >&2; exit 2; fi
    legacy || { echo "Aucun jeton hérité dans $F : rien à migrer (utilisez sans option pour en créer un)." >&2; exit 2; }
    write_agent "$(grep '^ORCH_EXEC_AGENT_TOKEN=' "$F" | head -1 | cut -d= -f2-)"
    sed -i '/^ORCH_EXEC_AGENT_TOKEN=/d' "$F"
    echo "Jeton déplacé de $F vers $A (droits 600). Redémarrez les services pour qu'ils ne le portent plus ; le runner enregistré reste valable."
    echo "Ce jeton a été visible de tous les services qui chargent $F : envisagez --rotate-agent-token."
  else
    # Installation existante : on ne touche à rien. Un jeton d'agent hérité est signalé ; sans jeton d'agent, .env.agent est créé (nouvelle valeur, rien d'enregistré encore).
    if legacy; then echo "ATTENTION : $F porte encore ORCH_EXEC_AGENT_TOKEN (visible de tous les services qui le chargent). Procédure : scripts/prod-init.sh --migrate-agent-token." >&2
    elif [ ! -f "$A" ]; then write_agent "$(agent_token)"; echo "Jeton de l'agent d'exécution créé dans $A."; fi
    echo "$F existe déjà : conservé."
  fi
  exit 0
fi
cp "$ROOT/infra/.env.prod.example" "$F"; chmod 600 "$F"
sed -i "s|^ORCH_SECRET_KEY=.*|ORCH_SECRET_KEY=$(rand)|; s|^ORCH_DB_PASSWORD=.*|ORCH_DB_PASSWORD=$(rand)|" "$F"
WORK="${ORCH_WORK_HOST_DIR:-$ROOT/var/prod-work}"; mkdir -p "$WORK"; chmod 755 "$WORK"
sed -i "s|^ORCH_WORK_DIR=.*|ORCH_WORK_DIR=$WORK|" "$F"
[ -f "$A" ] || write_agent "$(agent_token)"
echo "Créé : $F et $A (valeurs aléatoires, droits 600, ignorés par Git)."
