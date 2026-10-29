# Installation

Ce guide couvre l'installation de **développement / démonstration**, l'installation **de type production sur un hôte**, la configuration, les runners distants,
les sauvegardes et les mises à niveau.

## Sommaire
1. [Prérequis](#1-prérequis)
2. [Développement et démonstration](#2-développement-et-démonstration)
3. [Production sur un hôte (Docker Compose)](#3-production-sur-un-hôte-docker-compose)
4. [Configuration](#4-configuration)
5. [Runners distants](#5-runners-distants)
6. [Sauvegarde et restauration](#6-sauvegarde-et-restauration)
7. [Mise à niveau](#7-mise-à-niveau)
8. [Durcissement et limites](#8-durcissement-et-limites)
9. [Dépannage](#9-dépannage)
10. [Désinstallation](#10-désinstallation)

## 1. Prérequis
| Élément | Version |
|---|---|
| Système | Linux ou Windows avec WSL2 (le dépôt de préférence **dans le système de fichiers Linux**, pas sous `/mnt/c`) |
| Docker | démon accessible à l'utilisateur ; Compose v2 |
| Python | 3.12 ou plus (3.13 conseillé) |
| Node.js | 22 ou plus (développement et build de l'interface) |
| Git | — |

Au premier lancement, Docker télécharge les images `postgres:17-alpine` et `python:3.13-slim` (actions Python). Les actions Node/TypeScript et PowerShell demandent
`node:22-slim` et `mcr.microsoft.com/powershell:lts-debian-12`.

## 2. Développement et démonstration
```bash
git clone https://github.com/xanathoxic/fluxonata.git
cd fluxonata
scripts/dev-up.sh     # .env (clé secrète générée), venv, dépendances, PostgreSQL, migrations, données fictives, 4 processus
scripts/doctor.sh     # diagnostic en lecture seule
scripts/smoke.sh      # vérification de bout en bout (connexion, lancement d'une action réelle)
scripts/dev-down.sh   # arrêt (ajoutez --db pour arrêter PostgreSQL ; les données sont conservées)
```
Ports (liés à 127.0.0.1) : API **8100**, interface **5173**, PostgreSQL **5433**.

Comptes de démonstration (données fictives, mot de passe `demo-fluxonata`, **à ne jamais utiliser en production**) :
`alice` (concepteur), `bob` (lecteur), `claire` (approbatrice), `admin` (administrateur).

Espace de démonstration complet, avec workflows imbriqués et skills IA : `scripts/vitrine.sh` (le relie à un serveur d'IA local compatible OpenAI si vous en avez un, sinon un faux fournisseur).

Tests : `cd backend && ../.venv/bin/python -m pytest` · `cd frontend && npm test` · tout vérifier : `scripts/ci.sh`.

## 3. Production sur un hôte (Docker Compose)
```bash
scripts/prod-init.sh        # génère infra/.env.prod (droits 600, secrets aléatoires ; jamais versionné)
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d --build
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod exec api python manage.py bootstrap --workspace principal
```
`bootstrap` crée l'organisation, l'espace (développement / recette / production) et **le premier administrateur** ; son mot de passe est affiché **une seule fois**
(ou lu via `--password-env`). Ce compte est aussi administrateur de la plateforme.

`scripts/prod-smoke.sh` éprouve une installation à partir de rien : construction des images, démarrage de la pile, création d'un administrateur, exécution d'une action réelle, nettoyage.

Composants de la pile : `db` (PostgreSQL), `migrate` (migrations au démarrage), `api` (gunicorn), `orchestrator`, `worker`, `web` (nginx : interface + proxy de l'API), plus un service de test local pour les actions de démonstration.

### TLS (obligatoire hors évaluation)
Terminez TLS devant nginx (ou ajoutez un bloc `listen 443 ssl`), puis **retirez** `ORCH_INSECURE_COOKIES` et définissez `ORCH_BEHIND_TLS_PROXY=1`.
Renseignez `ORCH_ALLOWED_HOSTS` et `ORCH_TRUSTED_ORIGINS` (ex. `https://fluxonata.exemple.org`).

### Points d'attention
- **Worker** : il monte le socket Docker pour lancer les conteneurs d'actions. C'est un composant de confiance (accès au socket ≈ root sur l'hôte) : dédiez l'hôte ou durcissez-le (§8).
  Le répertoire `ORCH_WORK_DIR` doit avoir **le même chemin** sur l'hôte et dans le conteneur du worker.
- **Clés** : le volume `keys` contient les clés de chiffrement des secrets et de signature. Sauvegardez-le **séparément** de la base : sans lui, secrets et webhooks sont irrécupérables.
- **Plusieurs workers** : `docker compose … up -d --scale worker=N`. Plusieurs processus se partagent la file ; l'équité entre espaces est assurée par la plateforme.
- **API** : l'image lance gunicorn avec des workers **à threads** (`gthread`), nécessaires aux runners distants dont les requêtes durent jusqu'à 20 s ; ne repassez pas à des workers synchrones.

## 4. Configuration
Tout se règle par variables d'environnement (`.env` en développement, `infra/.env.prod` en production). Valeurs principales :

| Variable | Défaut | Rôle |
|---|---|---|
| `ORCH_SECRET_KEY` | — (obligatoire) | clé secrète Django (générée par les scripts) |
| `ORCH_DEBUG` | `0` | mode debug (développement seulement) |
| `ORCH_ALLOWED_HOSTS` | `localhost,127.0.0.1` | noms d'hôte acceptés |
| `ORCH_TRUSTED_ORIGINS` | — | origines HTTPS de confiance (CSRF) |
| `ORCH_DB_HOST` / `_PORT` / `_NAME` / `_USER` / `_PASSWORD` | `127.0.0.1` / `5433` / `fluxonata` / `fluxonata` / — | PostgreSQL |
| `ORCH_BEHIND_TLS_PROXY` | `0` | `1` derrière un proxy TLS : l'API se fie à `X-Forwarded-Proto: https` (le proxy doit l'envoyer) |
| `ORCH_INSECURE_COOKIES` | `0` | `1` pour une évaluation en HTTP simple uniquement |
| `ORCH_KEY_DIR` | `~/.config/fluxonata/keys` | clés de chiffrement et de signature |
| `ORCH_ARTIFACTS_DIR` | `var/artifacts` | fichiers produits par les actions |
| `ORCH_RUNNER` | `docker` | exécuteur du worker : `docker` (isolé) ou `subprocess` (développement, **non isolé**) |
| `ORCH_WORK_DIR` | — | répertoire de travail des conteneurs d'actions (même chemin hôte/conteneur) |
| `ORCH_PYTHON_IMAGE` / `_NODE_IMAGE` / `_POWERSHELL_IMAGE` | images officielles | images d'exécution (référencez-les **par digest** en production : `scripts/pin-images.sh`) |
| `ORCH_LEASE_SECONDS` | `30` | durée du bail d'une tâche (reprise sur panne) |
| `ORCH_MAX_ATTEMPTS` | `3` | tentatives par tâche |
| `ORCH_MAX_CONCURRENT_PER_WORKSPACE` | `4` | plafond de tâches simultanées par espace |
| `ORCH_WORKER_TAGS` | — | étiquettes des workers intégrés (pour les actions qui en exigent) |
| `ORCH_EGRESS_ALLOWLIST` | — | hôtes privés explicitement autorisés pour les sorties réseau (anti-SSRF) |
| `ORCH_LOGIN_RATE` / `_API_RATE` / `_WEBHOOK_RATE` / `_AI_RATE` / `_RUNNER_RATE` | `10/min` / `600/min` / `120/min` / `30/min` / `1500/min` | limites de débit |
| `ORCH_METRICS_TOKEN` | — | jeton d'accès à `/api/v1/metrics/` (Prometheus) ; vide = désactivé |
| `ORCH_LOG_FORMAT` | `json` | format des journaux |
| `ORCH_HARDENED` | `0` | profil de conteneur renforcé (seccomp, capacités retirées) — §8 |
| `ORCH_OIDC_ISSUER` / `_CLIENT_ID` / `_CLIENT_SECRET` / `_REDIRECT_URI` | — | SSO OpenID Connect (voir [docs/ADMIN.md](ADMIN.md)) |

Les secrets (mots de passe, clés) ne doivent **jamais** être versionnés ni écrits dans les journaux : `scripts/scan-secrets.py` le contrôle en CI.

## 5. Runners distants
Un runner est un agent installé sur une autre machine (ou la même) qui exécute des actions pour la plateforme, partagé ou dédié à des espaces.
Création, démarrage (hôte ou conteneur), sécurité et exploitation : voir **[docs/RUNNERS.md](RUNNERS.md)**.

## 6. Sauvegarde et restauration
Les volumes Docker survivent à l'arrêt d'un processus, **pas** à la perte du disque ou de la distribution WSL : copiez les sauvegardes hors de la machine.
```bash
scripts/backup.sh                 # base PostgreSQL + artefacts + sommes de contrôle -> backups/<horodatage>/
scripts/backup.sh --with-keys     # ajoute les clés de chiffrement (à stocker SÉPARÉMENT de la base)
scripts/restore.sh backups/<horodatage> fluxonata_restore   # restaure dans une NOUVELLE base (refuse d'écraser la base active)
```
Après vérification : `ORCH_DB_NAME=fluxonata_restore`, `cd backend && ../.venv/bin/python manage.py migrate`, relancez les services.
`scripts/restore-test.sh` vérifie qu'une sauvegarde est restaurable (restauration dans une base jetable, comptes de lignes, déchiffrement d'un secret, migrations).

Conservation : `manage.py prune_data --days 90` (simulation) puis `--apply` supprime les exécutions anciennes et leurs artefacts ; l'audit n'est supprimé qu'après export (`--audit-days N --audit-export fichier`).

## 7. Mise à niveau
1. **Sauvegardez** (§6, y compris les clés).
2. Arrêtez les services (ou mettez l'API en maintenance).
3. Récupérez la nouvelle version, puis `pip install -r backend/requirements.txt` et `cd frontend && npm ci && npm run build` (avec Docker : reconstruisez les images).
4. `cd backend && ../.venv/bin/python manage.py migrate` — les migrations sont conçues pour être **additives**.
5. Relancez orchestrateur, workers puis API. Les exécutions en cours ne sont pas touchées : leur définition est figée au lancement et leur état est en base.
6. Vérifiez : `scripts/doctor.sh`, `scripts/smoke.sh`, Administration → Santé.

**Retour arrière** : revenir au code précédent ne défait pas les migrations. En cas de problème, restaurez la sauvegarde de l'étape 1 dans une base neuve et pointez l'application dessus.

## 8. Durcissement et limites
- Un conteneur partage le noyau de l'hôte : une faille du noyau ou de Docker permettrait une évasion. Pour du code hostile en multi-tenant, utilisez un runtime à noyau isolé
  (gVisor, Kata, micro-VM : `ORCH_DOCKER_RUNTIME`) ou des hôtes dédiés par équipe. `ORCH_HARDENED=1` active un profil renforcé (seccomp, capacités retirées).
- Le worker (et l'agent runner) doivent pouvoir lancer des conteneurs : ne les exposez jamais aux actions ; isolez-les (utilisateur et hôte dédiés, ou Docker rootless).
- Référencez les images d'exécution par digest pour les publications. Les dépendances ne s'installent pas à l'exécution : construisez des images dédiées pour les actions qui en ont besoin.
- Livraison des tâches **au moins une fois** ; les actions à effet externe non idempotentes ne sont jamais rejouées automatiquement (voir [docs/ACTION_SDK.md](ACTION_SDK.md)).

## 9. Dépannage
| Symptôme | Piste |
|---|---|
| `scripts/doctor.sh` signale un service arrêté | consulter `.run/<service>.log` (développement) ou `docker compose logs <service>` |
| Une action reste « en file » | aucun worker actif (Administration → Santé), plafond de l'espace atteint, ou espace réservé à des runners dédiés sans runner en ligne |
| Une action échoue au démarrage du conteneur | Docker inaccessible au worker, image absente (`docker pull`), `ORCH_WORK_DIR` différent entre hôte et conteneur |
| Erreur CSRF / cookie refusé derrière un proxy | `ORCH_TRUSTED_ORIGINS`, `ORCH_BEHIND_TLS_PROXY`, en-tête `Host` transmis |
| Appels sortants refusés (SSRF) | l'hôte est privé : ajoutez-le explicitement à `ORCH_EGRESS_ALLOWLIST` |
| Secrets illisibles après restauration | clés de chiffrement absentes : restaurez `ORCH_KEY_DIR` d'origine |

## 10. Désinstallation
`docker compose -f infra/docker-compose.prod.yml down` ; ajoutez `-v` pour supprimer les volumes (**destructif** : sauvegardez d'abord). En développement : `scripts/dev-down.sh --db`, puis supprimez le volume `fluxonata_fluxonata-pgdata` si vous voulez repartir de zéro.
