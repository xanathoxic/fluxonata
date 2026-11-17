#!/usr/bin/env bash
# Enregistre l'agent d'exécution (topologie par agents) : lit le jeton dans infra/.env.agent et n'écrit en base que son empreinte. Jamais affiché.
# Usage : scripts/agent-register.sh   (pile démarrée) ; à rejouer après `scripts/prod-init.sh --rotate-agent-token`, puis redémarrer exec-agent.
# Le jeton ne passe ni par un argument de commande, ni par l'environnement d'un conteneur : seulement par celui du client `docker` (variable transmise par son nom).
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
F="infra/.env.agent"
[ -f "$F" ] || { echo "$F absent : lancez scripts/prod-init.sh d'abord." >&2; exit 2; }
TOKEN="$(grep '^ORCH_RUNNER_TOKEN=' "$F" | head -1 | cut -d= -f2-)"
[ -n "$TOKEN" ] || { echo "ORCH_RUNNER_TOKEN vide dans $F." >&2; exit 2; }
# Enregistrer ne démarre aucun agent : la variable n'est lue que pour que Compose accepte de charger le fichier du profil de confiance (son vrai usage exige un accord explicite au démarrage).
export ORCH_ACCEPT_SHARED_DOCKER_AGENT="${ORCH_ACCEPT_SHARED_DOCKER_AGENT:-oui}"
FILES=(-f infra/docker-compose.prod.yml -f infra/docker-compose.roles.yml -f infra/docker-compose.agent.yml)
ORCH_EXEC_AGENT_TOKEN="$TOKEN" docker compose "${FILES[@]}" --env-file infra/.env.prod exec -T -e ORCH_EXEC_AGENT_TOKEN "${ORCH_AGENT_API_SERVICE:-api-ui}" python manage.py runner_bootstrap
