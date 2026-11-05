# Versionnement des interfaces et politique de mise à jour (AR6.2)

Écrit le 2026-11-05. Ce document dit **ce qui est promis** et **ce qui le vérifie** ; une promesse sans vérification est marquée comme telle.

## 1. Ce qui est versionné
| Interface | Version actuelle | Qui l'utilise | Promesse | Vérification |
|---|---|---|---|---|
| API REST | `/api/v1` | interface web, intégrations, jetons de service, MCP | pas de suppression d'opération ni de changement incompatible dans la v1 ; les ajouts sont compatibles ; une rupture ouvre `/api/v2` en parallèle | `scripts/api_contract.py` + `tests/test_api_contract.py` (**opérations seulement** : un champ retiré d'une réponse n'est pas détecté) |
| Protocole des runners (agent ↔ plateforme) | `protocol: 1` (`GET /runner/me/`) | `runner/agent.py` | un agent N reste accepté par la plateforme N+1 ; `ORCH_MIN_AGENT_VERSION` permet d'exiger une version minimale (réponse `426`, l'agent s'arrête avec le code 3) | `tests/test_runner_protocol.py` ; compatibilité inter-versions de l'agent **non testée** |
| Protocole worker v1 (harnais d'actions) | `v1` (`workers/`) | exécuteur de conteneurs | entrée JSON, une enveloppe JSON sur stdout (ADR 0001) | tests du harnais |
| Schéma de base de données | migrations Django | tous les rôles | « expand puis contract » : l'ancien code tourne sur le nouveau schéma pendant un déploiement | `scripts/lint_migrations.py` (CI), `scripts/compat-test.sh` (ancien code sur nouveau schéma) |
| Configuration (`ORCH_*`) | — | opérateurs | un réglage n'est retiré qu'après avoir été déclaré obsolète dans `UPGRADE.md` | non vérifié automatiquement |

## 2. Dépréciation
Une opération d'API, un réglage ou une version d'agent à retirer est **annoncé dans `docs/UPGRADE.md`** une version mineure avant, marqué `deprecated` dans l'OpenAPI quand c'est une route, et conservé **au moins une version mineure** après l'annonce. La suppression exige : une décision écrite (ADR ou ligne de `UPGRADE.md`), puis `scripts/api_contract.py --write` (qui regèle le contrat), une dérogation `# lint-migrations: allow M1 — raison` pour un champ de base, ou le relèvement de `ORCH_MIN_AGENT_VERSION`.
**Aujourd'hui rien n'est déprécié.** La politique n'a été appliquée à aucune suppression réelle.

## 3. Ordre d'une mise à jour progressive (topologie par rôles)
1. **Migrations « expand »** (service `migrate`, connexion directe) : l'ancien code continue de servir pendant et après. Vérifié par `scripts/compat-test.sh`.
2. **Rôles d'exécution** : `worker-aux`, `worker-exec`, puis `runner-gw` (les runners distants se reconnectent ; un agent trop ancien reçoit `426`).
3. **Orchestrateur** (sans état : tout est dans PostgreSQL ; reprise sur bail).
4. **`api-ingest`** puis **`api-ui`** (plusieurs réplicas pour ne pas interrompre la surface).
5. **Migrations « contract »** dans une version **ultérieure** (jamais la même).
**Recette pour un rôle à plusieurs réplicas (compose)** — `docker compose up` seul recrée les réplicas l'un après l'autre sans attendre le suivant : **mesuré, 9-10 s de coupure de l'interface** pendant la bascule d'`api-ui`. À la place (`scripts/rolling_upgrade_test.py:roll_replicas`, éprouvé) :
1. `ORCH_BACKEND_IMAGE=<nouvelle> docker compose … up -d --no-build --no-deps --no-recreate --scale <service>=<2×réplicas> <service>` — démarre les nouveaux À CÔTÉ des anciens ;
2. attendre que les nouveaux soient `healthy` (`docker inspect -f '{{.State.Health.Status}}'`) ;
3. `docker stop -t 30` puis `docker rm` des anciens conteneurs (ils finissent leurs requêtes).
Kubernetes fait cela nativement (`maxUnavailable: 0`, sonde de disponibilité : `infra/k8s/`, **jamais appliqué ici**).
Chaque rôle s'arrête proprement (SIGTERM, `stop_grace_period`) : l'action en cours se termine, rien n'est rejoué. Preuve sous charge : `scripts/rolling_upgrade_test.py` (AR6.1) — voir `TASKS.md` pour le résultat réel et ses limites.

## 4. Retour arrière
Un retour d'image est supporté **tant que le schéma n'a reçu que des migrations « expand »** (c'est le cas de l'ancien code sur le nouveau schéma). Un retour arrière de **migration** n'est pas une opération supportée : restaurer une sauvegarde (`docs/BACKUP.md`).
