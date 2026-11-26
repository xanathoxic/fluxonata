#!/usr/bin/env bash
# Exploitation d'un agent distant (Compose). Ne lit et n'affiche jamais le jeton ; il vient de l'entrée standard ou d'un fichier protégé.
#   agent-ctl.sh init              prépare le répertoire de travail et le fichier du jeton (lu sur l'entrée standard : `printf %s "$TOKEN" | agent-ctl.sh init`)
#   agent-ctl.sh up | down         démarre / arrête l'agent (les tâches en cours se terminent)
#   agent-ctl.sh health            état du conteneur et sonde de santé (plate-forme joignable, jeton accepté, démon Docker)
#   agent-ctl.sh rotate            nouveau jeton (entrée standard) : remplace le fichier puis recrée l'agent
#   agent-ctl.sh update            reconstruit l'image de l'agent puis le recrée
#   agent-ctl.sh revoke            arrête l'agent et supprime le jeton de cette machine (la révocation côté plate-forme : supprimer le runner dans l'interface)
#   agent-ctl.sh targets-network  crée le réseau Docker des playbooks Ansible (ORCH_ANSIBLE_NETWORK, sous-réseau ANSIBLE_TARGETS_SUBNET facultatif) ; sans effet s'il existe
#   agent-ctl.sh clean             arrête l'agent, supprime ses conteneurs d'action et ses répertoires de travail
set -euo pipefail
cd "$(dirname "$0")"
ENVF=agent.env
[ -f "$ENVF" ] || { echo "agent.env absent : copiez agent.env.example et renseignez-le." >&2; exit 2; }
set -a; . "./$ENVF"; set +a
WORK="${AGENT_WORK_DIR:-/var/lib/fluxonata-agent}"
SOCK="${AGENT_DOCKER_SOCKET:-/var/run/docker.sock}"
AGENT_USER="${AGENT_USER:-10001:10001}"
export AGENT_TOKEN_FILE="${AGENT_TOKEN_FILE:-$PWD/secrets/runner_token}"
export DOCKER_GID="${DOCKER_GID:-$(stat -c %g "$SOCK" 2>/dev/null || echo 0)}"
C=(docker compose --env-file "$ENVF" -f docker-compose.yml)
write_token() {  # entrée standard -> fichier 0400 du compte de l'agent ; jamais affiché
  umask 077; mkdir -p "$(dirname "$AGENT_TOKEN_FILE")"
  local t; t="$(cat)"; case "$t" in orr_*) ;; *) echo "jeton invalide (attendu : orr_…)" >&2; exit 2 ;; esac
  printf '%s' "$t" > "$AGENT_TOKEN_FILE.new"; chmod 0400 "$AGENT_TOKEN_FILE.new"
  sudo chown "${AGENT_USER%%:*}" "$AGENT_TOKEN_FILE.new" 2>/dev/null || chown "${AGENT_USER%%:*}" "$AGENT_TOKEN_FILE.new"
  mv -f "$AGENT_TOKEN_FILE.new" "$AGENT_TOKEN_FILE"
}
case "${1:-}" in
  init)
    sudo install -d -m 0750 -o "${AGENT_USER%%:*}" "$WORK" 2>/dev/null || install -d -m 0750 "$WORK"
    write_token; echo "Prêt. Renseignez agent.env puis : agent-ctl.sh up" ;;
  up) "${C[@]}" up -d --build ;;
  down) "${C[@]}" down ;;
  health) "${C[@]}" ps agent; "${C[@]}" exec -T agent python /opt/fluxonata/runner/agent.py --healthcheck ;;
  rotate) write_token; "${C[@]}" up -d --force-recreate agent ;;   # `restart` ne relirait pas le fichier
  update) "${C[@]}" build --pull agent; "${C[@]}" up -d --force-recreate agent ;;
  revoke) "${C[@]}" down; shred -u "$AGENT_TOKEN_FILE" 2>/dev/null || rm -f "$AGENT_TOKEN_FILE"; echo "Jeton supprimé de cette machine. Supprimez aussi le runner dans l'interface pour le révoquer." ;;
  targets-network)
    net="${ORCH_ANSIBLE_NETWORK:-fluxonata-ansible-targets}"
    if docker network inspect "$net" >/dev/null 2>&1; then echo "Réseau $net déjà présent."
    else docker network create --driver bridge ${ANSIBLE_TARGETS_SUBNET:+--subnet "$ANSIBLE_TARGETS_SUBNET"} "$net" >/dev/null && echo "Réseau $net créé. Limitez ce qu'il atteint avec le pare-feu de cette machine (voir docs/RUNNERS.md)."; fi ;;
  clean)
    "${C[@]}" down
    docker ps -aq --filter "label=fluxonata.sandbox" | xargs -r docker rm -f
    sudo find "$WORK" -mindepth 1 -maxdepth 1 \( -name 'orch-run-*' -o -name 'orch-env-*' \) -exec rm -rf {} + ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
