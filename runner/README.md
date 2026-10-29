# Agent runner Fluxonata

Exécute des actions pour une plate-forme Fluxonata, comme un runner GitLab : il **appelle** la plate-forme (long-poll),
exécute chaque action dans un **conteneur jetable durci** (aucun réseau, utilisateur non privilégié, système de fichiers en
lecture seule, limites de mémoire/CPU/processus) et rend le résultat. Il n'a **jamais** accès à la base de données, à la
clé du coffre-fort ni aux tâches qu'on ne lui a pas confiées. Bibliothèque standard uniquement (hors le bac à sable partagé
`backend/runs/runners.py`).

```bash
# 1. Plate-forme → Runners (ou Administration → Runners pour un runner dédié) : « Nouveau runner » ; copier le jeton (affiché une fois)
# 2. Sur la machine qui exécutera les actions (Docker requis) :
ORCH_URL=https://fluxonata.exemple.org ORCH_RUNNER_TOKEN=orr_… python3 runner/agent.py
# ou, dans le dépôt :  scripts/runner.sh orr_…
```

| Variable | Rôle |
|---|---|
| `ORCH_URL` | adresse de la plate-forme (HTTPS hors réseau local) |
| `ORCH_RUNNER_TOKEN` / `ORCH_RUNNER_TOKEN_FILE` | jeton du runner (jamais journalisé) |
| `ORCH_RUNNER_CONCURRENCY` | tâches en parallèle (bornées par le réglage du runner côté plate-forme) |
| `ORCH_RUNNER_EXECUTOR` | `docker` (défaut) ou `subprocess` (**sans isolation**, développement) |
| `ORCH_CA_FILE` | autorité de certification pour un HTTPS interne |
| `ORCH_HARDENED`, `ORCH_PYTHON_IMAGE`, `ORCH_NODE_IMAGE`, `ORCH_WORK_DIR`… | mêmes réglages du bac à sable que les workers de la plate-forme |

Codes de sortie : `0` arrêt normal, `1` Docker inaccessible, `2` jeton refusé (runner supprimé ou jeton changé), `3` plate-forme injoignable au démarrage.
Arrêt : `SIGTERM`/Ctrl+C laisse finir les tâches en cours (une seconde fois : arrêt immédiat ; la plate-forme reprend la tâche à l'expiration du bail).

En conteneur : `docker build -f runner/Dockerfile -t fluxonata-runner .` puis voir `docs/RUNNERS.md` (socket Docker, répertoire de travail partagé).
Documentation complète : [docs/RUNNERS.md](../docs/RUNNERS.md).
