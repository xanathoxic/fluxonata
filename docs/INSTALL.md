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
| `ORCH_ARTIFACTS_STORE` | `fs` | où sont écrits les fichiers produits par les actions et les archives de paquets : `fs` (volume local, un seul hôte) ou `s3` (service compatible S3 : API et workers peuvent alors tourner sur des hôtes différents) |
| `ORCH_S3_ENDPOINT` / `ORCH_S3_BUCKET` | — | adresse `http(s)://hôte[:port]` du service et nom du bucket (adressage « path-style »). **L'hôte doit figurer dans `ORCH_EGRESS_ALLOWLIST`** dès qu'il est interne : toute sortie réseau passe par la protection SSRF |
| `ORCH_S3_ACCESS_KEY` / `ORCH_S3_SECRET_KEY` | — | identifiants du service (environnement uniquement ; jamais dans un journal ni un message d'erreur) |
| `ORCH_S3_PREFIX` / `ORCH_S3_REGION` | vide / `us-east-1` | préfixe de toutes les clés (un bucket partagé entre installations) et région de signature |
| `ORCH_S3_TIMEOUT` / `ORCH_S3_MAX_OBJECT_MB` | `30` / `64` | délai par requête (s) et taille maximale d'un objet : au-delà, écriture et lecture sont refusées plutôt que tronquées |
| `ORCH_CACHE` | `locmem` (pile de production : `db`) | cache des limites de débit strictes (connexion, webhooks, formulaires publics, IA, runners) : `locmem` (un seul processus), `db` (table `fluxonata_cache`, créée par `manage.py createcachetable`) ou `redis://hôte:6379/1` (paquet `redis` à installer ; non exécuté ici). Avec plusieurs processus, `locmem` multiplie le plafond par leur nombre |
| `ORCH_USER_THROTTLE_CACHE` | `local` | `default` rend la limite générale par utilisateur exacte entre processus, au prix de deux requêtes SQL par appel (cache `db`) |
| `ORCH_KEY_AUTOCREATE` | `1` (pile de production : `0`) | `1` : la première clé du coffre se crée au premier usage (développement, tests). `0` : **jamais** — un service démarré sans le volume des clés fabriquerait sinon une clé neuve que rien ne conserverait ; la première clé se crée alors explicitement, une fois, par `manage.py vault_init` (déjà lancé par `bootstrap`) ; tout autre service échoue avec un message qui dit quoi faire. Sauvegardez le dossier des clés À PART |
| `ORCH_WORKER_CONCURRENCY` | `1` | actions exécutées EN PARALLÈLE par un process worker (rôle `exec`, 1 à 16). Mesuré sur une machine de développement : ×1,23 pour 4 créneaux (le démarrage de conteneur Docker borne l'hôte) ; à essayer sur un hôte qui suit, pas à supposer gagnant. L'arrêt propre attend la fin de toutes les actions en cours (`stop_grace_period`) |
| `ORCH_CLAIM_WINDOW_PER_WORKSPACE` | `50` | tâches en file examinées PAR ESPACE à chaque prise (les plus anciennes, après priorité) : borne le coût d'une prise sans qu'un espace au long carnet n'en prive un autre |
| `ORCH_ROLES` | vide (= tous) | rôles portés par CE process (ADR 0022) — une valeur par conteneur. **Worker** (`run_worker`, `--roles` l'emporte) : `exec` (exécution des actions), `aux` (étapes HTTP/SQL/SFTP/SMTP/LDAP, interrogation, listes dynamiques, alertes, Git), `ai` (assistant, évaluations) ; un process sans `exec` n'exécute aucune action et **n'est pas compté comme worker** par la santé. **API** (gunicorn) : `ui` (interface et API authentifiée), `ingest` (webhooks, formulaires publics, reprise d'attente : les seules routes exposées à Internet), `runner-gw` (protocole des runners) ; une route d'un rôle non servi **n'existe pas** dans le process (404) ; `health/live` et `health/ready` sont servies par tous. Plusieurs rôles possibles, séparés par des virgules. Une valeur inconnue — ou un rôle de worker sur l'API, et inversement — empêche le démarrage |
| `ORCH_RUNNER_LONG_POLL_SLOTS` / `ORCH_RUNNER_BUSY_RETRY_SECONDS` | `4` / `5` | attentes longues simultanées **par processus** de l'API (chacune occupe un thread) ; au-delà, un runner reçoit `429` « réessayez dans N s » et revient plus tard. Une tâche disponible est toujours servie. Pile de production : 2 processus × 4 = 8 attentes pour 16 threads |
| `ORCH_DB_POOLED` | `0` (pile de production : `1` pour api, orchestrator et worker) | `1` quand `ORCH_DB_HOST`/`PORT` désignent un pooler en **mode transaction** (PgBouncer) : désactive les curseurs serveur et les instructions préparées automatiques (état de session perdu entre deux transactions), active la vérification de connexion. **Jamais pour `migrate`** |
| `ORCH_DB_CONN_MAX_AGE` | `0` (derrière un pooler : `60`) | durée de conservation d'une connexion par fil, en secondes |
| `ORCH_RUNNER` | `docker` | exécuteur du worker : `docker` (isolé) ou `subprocess` (développement, **non isolé**) |
| `ORCH_WORK_DIR` | — | répertoire de travail des conteneurs d'actions (même chemin hôte/conteneur) |
| `ORCH_PYTHON_IMAGE` / `_NODE_IMAGE` / `_POWERSHELL_IMAGE` | images officielles | images d'exécution (référencez-les **par digest** en production : `scripts/pin-images.sh`) |
| `ORCH_LEASE_SECONDS` | `30` | durée du bail d'une tâche (reprise sur panne) |
| `ORCH_MAX_ATTEMPTS` | `3` | tentatives par tâche |
| `ORCH_MAX_CONCURRENT_PER_WORKSPACE` | `4` | plafond de tâches simultanées par espace |
| `ORCH_WORKER_TAGS` | — | étiquettes des workers intégrés (pour les actions qui en exigent) |
| `ORCH_EGRESS_ALLOWLIST` | — | hôtes privés explicitement autorisés pour les sorties réseau (anti-SSRF). Les administrateurs de plate-forme peuvent en ajouter d'autres dans **Plate-forme → Réseau** (sans redémarrage) ; cette variable reste l'affaire de l'opérateur et n'est pas modifiable depuis l'interface |
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
Principe : **sauvegarder, récupérer la nouvelle version, reconstruire, vérifier**. Les migrations de base sont conçues pour être **additives**, et les exécutions en cours ne sont pas touchées (leur définition est figée au lancement, leur état est en base).

### 7.1 Installation de type développement
1. **Sauvegardez** (§6, y compris les clés).
2. `scripts/dev-down.sh`, récupérez la nouvelle version (§7.3), puis `scripts/dev-up.sh` : dépendances, migrations et processus sont refaits.
3. Vérifiez : `scripts/doctor.sh`, `scripts/smoke.sh`, Administration → Santé.

### 7.2 Production sur un hôte (Docker Compose)
Les commandes s'exécutent dans le dossier du dépôt. Le projet Compose s'appelle `fluxonata-prod` : ses volumes sont `fluxonata-prod_pgdata`, `fluxonata-prod_keys` et `fluxonata-prod_artifacts`.
`scripts/backup.sh` vise la pile de développement : en production, sauvegardez ainsi.

**1. Sauvegarder** (à copier ensuite **hors du serveur** ; gardez `keys.tar.gz` séparé du reste : sans les clés, secrets et webhooks sont irrécupérables)
```bash
git rev-parse HEAD > /tmp/fluxonata-avant-maj.txt        # commit actuellement déployé, pour un retour arrière
TS=$(date -u +%Y%m%dT%H%M%SZ); mkdir -p backups/$TS && chmod 700 backups/$TS
C="docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod"
$C exec -T db pg_dump -U fluxonata -Fc fluxonata > backups/$TS/db.dump
docker run --rm -v fluxonata-prod_keys:/d:ro -v "$PWD/backups/$TS":/out alpine tar -C /d -czf /out/keys.tar.gz .
docker run --rm -v fluxonata-prod_artifacts:/d:ro -v "$PWD/backups/$TS":/out alpine tar -C /d -czf /out/artifacts.tar.gz .
ls -lh backups/$TS
```

**2. Récupérer la nouvelle version** (§7.3).

**3. Reconstruire et redémarrer**
```bash
$C up -d --build
```
Le service `migrate` applique les migrations avant le démarrage de l'API, de l'orchestrateur et du worker. **Ne relancez pas `bootstrap`** (il sert à la première installation). Comptez quelques minutes d'indisponibilité pendant la reconstruction.

**4. Vérifier**
```bash
$C ps                                           # tout « running » ; migrate « exited (0) »
$C logs migrate | tail -15                      # aucune erreur
$C exec api python manage.py showmigrations | grep -c "\[ \]"    # 0 : toutes les migrations sont appliquées
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/   # 200 (ou l'adresse de votre proxy)
```
Connectez-vous, puis Administration → **Santé**. Un nouveau réglage éventuel se trouve dans `infra/.env.prod` (fichier non suivi par Git : il est conservé d'une version à l'autre) ; après toute modification, relancez l'étape 3.

**Retour arrière.** Revenir au code précédent **n'annule pas** les migrations : en cas de problème, restaurez la sauvegarde de l'étape 1.
```bash
$C stop api orchestrator worker web
$C exec -T db dropdb -U fluxonata fluxonata && $C exec -T db createdb -U fluxonata fluxonata
$C exec -T db pg_restore -U fluxonata -d fluxonata --no-owner < backups/$TS/db.dump
git reset --hard $(cat /tmp/fluxonata-avant-maj.txt)
$C up -d --build
```
Si l'installation est importante, **essayez d'abord la restauration** sur une base jetable (`scripts/restore-test.sh` fait cette vérification pour la pile de développement).

### 7.3 Récupérer la nouvelle version
```bash
git fetch origin
git status                     # des modifications locales à conserver ? sauvegardez-les d'abord
git pull --ff-only             # cas normal
```
Si `git pull` signale que les historiques ont divergé (cas d'une installation antérieure à la v0.4.0, dont l'historique du dépôt a été reconstruit), alignez le dépôt local sur la version publiée :
```bash
git reset --hard origin/main   # remplace les fichiers suivis par Git ; .env, infra/.env.prod, var/ et backups/ ne sont pas touchés
```
Si vous avez modifié des fichiers suivis (par exemple `infra/docker-compose.prod.yml`), refaites ces modifications ensuite. Un `git clone` neuf avec recopie de `infra/.env.prod` convient aussi : les volumes Docker sont conservés tant que le nom de projet reste `fluxonata-prod`.

### 7.4 À savoir pour la v0.4
- Trois migrations **additives** (jeux de test des actions, secret de signature des webhooks, coffre externe) : rien n'est supprimé.
- Le **coffre externe** (HashiCorp Vault) est désactivé tant qu'il n'est pas configuré (Plate-forme → Coffre externe). Pour un Vault du réseau interne, ajoutez son adresse à `ORCH_EGRESS_ALLOWLIST` dans `infra/.env.prod`.
- Les boutons « Générer avec l'IA » (workflows, actions, skills) n'apparaissent que si l'assistant IA est activé (Administration → Assistant IA).

## 8. Durcissement et limites
- Un conteneur partage le noyau de l'hôte : une faille du noyau ou de Docker permettrait une évasion. Pour du code hostile en multi-tenant, utilisez un runtime à noyau isolé
  (gVisor, Kata, micro-VM : `ORCH_DOCKER_RUNTIME`) ou des hôtes dédiés par équipe. `ORCH_HARDENED=1` active un profil renforcé (seccomp, capacités retirées).
- Le worker (et l'agent runner) doivent pouvoir lancer des conteneurs : ne les exposez jamais aux actions ; isolez-les (utilisateur et hôte dédiés, ou Docker rootless).
- **L'API n'exécute jamais de code** : tout ce qui lance un conteneur (actions, scripts, et l'**essai à blanc** de l'assistant IA) passe par le worker, seul composant à monter le socket Docker et `ORCH_WORK_DIR`. N'ajoutez pas ces montages au conteneur `api` : une API exposée au web avec le socket Docker aurait un accès root à l'hôte. Conséquence : sans worker actif, l'assistant IA de l'éditeur reste « en attente d'un travailleur ».
- Référencez les images d'exécution par digest pour les publications. Les dépendances ne s'installent pas à l'exécution : construisez des images dédiées pour les actions qui en ont besoin.
- Livraison des tâches **au moins une fois** ; les actions à effet externe non idempotentes ne sont jamais rejouées automatiquement (voir [docs/ACTION_SDK.md](ACTION_SDK.md)).

## 9. Dépannage
| Symptôme | Piste |
|---|---|
| `scripts/doctor.sh` signale un service arrêté | consulter `.run/<service>.log` (développement) ou `docker compose logs <service>` |
| Une action reste « en file » | aucun worker actif (Administration → Santé), plafond de l'espace atteint, ou espace réservé à des runners dédiés sans runner en ligne |
| Une action échoue au démarrage du conteneur | Docker inaccessible au worker, image absente (`docker pull`), `ORCH_WORK_DIR` différent entre hôte et conteneur |
| Erreur CSRF / cookie refusé derrière un proxy | `ORCH_TRUSTED_ORIGINS`, `ORCH_BEHIND_TLS_PROXY`, en-tête `Host` transmis |
| « Essai à blanc non effectué (bac à sable indisponible : …) » | le texte après les deux-points donne la cause, relevée par le **worker** (journal : `docker compose logs worker`). Vérifier dans le worker : `docker compose … exec worker sh -c 'docker run --rm --network none -v "$ORCH_WORK_DIR":/work python:3.13-slim python -c "print(1)"'` ; causes fréquentes : image `python:3.13-slim` absente (serveur sans accès Internet : `docker pull` ou `docker load`), `ORCH_WORK_DIR` inexistant ou sans droit de lecture (`755`) sur l'hôte, socket Docker inaccessible au worker |
| Connexion à une boîte e-mail (IMAP) en échec | le message distingue : identifiant refusé, certificat refusé, serveur injoignable (avec le type d'erreur) et erreur interne (voir `docker compose logs worker`). Le serveur doit répondre en TLS (port 993) ; les adresses IPv4 sont essayées avant les IPv6, une par une |
| Appels sortants refusés (SSRF) | l'hôte est privé : un administrateur de plate-forme l'autorise dans **Plate-forme → Réseau** (ou l'opérateur l'ajoute à `ORCH_EGRESS_ALLOWLIST`) |
| Secrets illisibles après restauration | clés de chiffrement absentes : restaurez `ORCH_KEY_DIR` d'origine |

## 10. Désinstallation
`docker compose -f infra/docker-compose.prod.yml down` ; ajoutez `-v` pour supprimer les volumes (**destructif** : sauvegardez d'abord). En développement : `scripts/dev-down.sh --db`, puis supprimez le volume `fluxonata_fluxonata-pgdata` si vous voulez repartir de zéro.

## Arrêt propre et sondes de santé (AR2.6)
`docker compose stop` envoie SIGTERM : l'orchestrateur finit son tour, le worker **termine l'action en cours** (puis ne reprend rien), les deux se retirent du registre de santé et sortent en code 0 ; l'API (gunicorn) finit ses requêtes. Si le délai (`stop_grace_period` : API 30 s, orchestrateur 20 s, worker 120 s) est dépassé, Docker tue le process : l'exécution en cours est reprise après expiration de son bail (voir `docs/RECOVERY.md`), plus lentement mais sans perte. **Une action plus longue que 120 s** doit faire augmenter `stop_grace_period` du worker.
Sondes : `api` interroge `/api/v1/health/ready/` ; `orchestrator` et `worker` exécutent `manage.py healthcheck --kind …` (le battement de cœur de leur conteneur date de moins de 30 s). `docker compose ps` affiche leur état.

## Topologie par rôles (AR2.4, AR2.7)
Par défaut, la pile de production est **compacte** : un `api` et un `worker`. Pour séparer les surfaces et les privilèges :
```
docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.roles.yml --env-file infra/.env.prod up -d --build
docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.roles.yml --env-file infra/.env.prod --profile ai up -d   # + le worker de l'assistant (garde le socket Docker)
```
`api-ui` (interface), `api-ingest` (webhooks, formulaires publics ; **aucune sortie réseau**), `runner-gw` (runners distants), `worker-exec` (actions, seul avec `worker-ai` à détenir le socket Docker), `worker-aux` (étapes de connexion, interrogation, alertes, Git). nginx route par préfixe ; l'arrêt d'un rôle n'indisponibilise que sa surface. Les réseaux `data` et `edge` sont internes : un service qui doit appeler l'extérieur doit être aussi sur `egress` (voir le fichier). `scripts/prod-smoke.sh --roles` rejoue l'installation vierge sur cette topologie. **Ne pas mélanger** les deux topologies sur un même projet sans `down` : les noms de services diffèrent.

## Topologie par agents (AR3.2, ADR 0023)
Pour que le service qui détient le socket Docker n'ait **ni la base ni la clé du coffre** : ajoutez `-f infra/docker-compose.agent.yml` aux deux fichiers de la topologie par rôles. `scripts/prod-init.sh` (rejoué sur une installation existante) ajoute `ORCH_EXEC_AGENT_TOKEN` à `infra/.env.prod`. Une fois la pile démarrée :
```
C="docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.roles.yml -f infra/docker-compose.agent.yml --env-file infra/.env.prod"
$C exec -T -e ORCH_EXEC_AGENT_TOKEN api-ui python manage.py runner_bootstrap      # enregistre le runner (jeton de l'environnement, jamais affiché)
$C --profile agent up -d --build exec-agent
```
Limites : le worker de l'assistant (`worker-ai`) n'est pas disponible dans ce mode ; la prise de tâche passe par HTTP (latence non mesurée) ; l'agent tourne sur le même hôte (le déplacer sur un hôte dédié est le but du mode, non testé ici). `scripts/prod-smoke.sh --agent` rejoue l'installation vierge.
