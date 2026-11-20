# Dimensionnement et montée en charge

Ce document dit **ce qui se multiplie, ce qui se règle, ce qui plafonne**, et distingue ce qui a été **mesuré** de ce qui est **déduit de la configuration** ou **non vérifié**.
Aucun chiffre de capacité absolu n'est promis : les seules mesures de débit ont été prises sur une machine de développement partagée.

Légende : **Mesuré** (essai réel, source citée) · **Déduit** (arithmétique sur la configuration livrée) · **Non vérifié** (jamais exécuté).

## 1. Vue d'ensemble

| Rôle | Sans état ? | Multiplier ? | Ce qui plafonne |
|---|---|---|---|
| `api` / `api-ui` (interface, API) | oui (sessions et limites de débit en base) | oui | 16 requêtes simultanées par réplica, connexions PgBouncer |
| `api-ingest` (webhooks, formulaires publics) | oui | oui | idem ; 16 threads par processus |
| `runner-gw` (long-poll des runners) | oui | oui | un thread par runner en attente |
| `orchestrator` | oui (état en base) | oui, **mesuré** à 2 et 4 instances | écritures PostgreSQL |
| `worker` / `worker-aux` / `worker-exec` | oui | oui | démarrage des conteneurs d'actions, hôte Docker |
| PostgreSQL | **non** | **non** (pas de réplica ni de bascule livrés) | CPU, disque (`fsync`), connexions |
| Artefacts | selon le mode | oui en mode `s3`, **non** en mode `fs` | un seul hôte en mode `fs` |

## 2. Montée en charge horizontale

### 2.1 API
- **Sans état** : sessions en base, limites de débit strictes dans un cache partagé (`ORCH_CACHE=db`, valeur des compose de production et par rôles).
  **Mesuré** : avec `locmem` sur 2 processus, 18 requêtes passent là où la limite en autorise 10 ; avec `db`, 10.
  Ne jamais laisser `locmem` dès qu'il y a plus d'un processus. `redis://…` est prévu mais **non exécuté** ([INSTALL.md](INSTALL.md)).
- La limite *générale* par utilisateur reste locale à chaque processus : elle se multiplie avec le nombre de réplicas.
- Placer les réplicas derrière un répartiteur de charge quelconque ; aucune affinité de session n'est requise.
- Mise à jour d'un rôle à plusieurs réplicas : suivre la recette de [VERSIONING.md](VERSIONING.md) (`up` seul coupe l'interface ≈ 10 s, **mesuré**).

### 2.2 Orchestrateur
- Plusieurs instances sont sûres : une seule avance un run à la fois (verrou de ligne, bail et jeton de clôture). **Mesuré** avec 2 puis 4 instances et la mort brutale d'une instance (`kill -9`) : aucune transition perdue.
- Le partage de charge est minimal ; **le gain de débit avec N instances n'est pas mesuré**.
- Une instance suffit pour des centaines de runs en attente : le tour coûte ≈ 3 ms pour 800 runs en attente (**mesuré**, corrigé de 15 s ; dépend du `fsync` de la machine).

### 2.3 Workers et exécution d'actions
- `docker compose … up -d --scale worker=N` ([INSTALL.md](INSTALL.md)) : les workers se partagent la file, sans doublon (**mesuré** : 24/24 runs réussis, une seule exécution chacun).
- L'équité entre espaces est assurée par la plateforme : fenêtre d'examen `ORCH_CLAIM_WINDOW_PER_WORKSPACE` (50) et plafond `ORCH_MAX_CONCURRENT_PER_WORKSPACE` (4 par défaut).
- **Le débit n'est pas démontré linéaire.** Passage de 1 à 4 workers : **×1,36** (objectif ×1,8 non atteint) ; 4 créneaux dans un même worker : **×1,23**. Cause supposée, non démontrée : le démarrage d'un conteneur (≈ 1 à 8 s) sur un hôte déjà chargé. Un banc sur serveur dédié et inactif reste à faire ([BENCH_RUNBOOK.md](BENCH_RUNBOOK.md)).
- Les workers qui exécutent des actions ont besoin du socket Docker de **leur** hôte : les multiplier signifie les répartir sur plusieurs hôtes, pas seulement ajouter des conteneurs sur le même. Sur Kubernetes, l'exécution passe par des runners/agents distants ([RUNNERS.md](RUNNERS.md)).
- Autoscaling : la plateforme expose la profondeur de file (`fluxonata_executions{status="queued"}`), l'âge de la plus ancienne tâche (`fluxonata_queue_oldest_age_seconds`) et `fluxonata_service_instances{kind}` sur `/api/v1/metrics/`. **Aucun autoscaler n'est livré ni exercé** (KEDA ou équivalent à brancher soi-même).

### 2.4 Artefacts
- Mode `fs` (défaut) : volume local, **un seul hôte**. Les workers sur deux hôtes ne partagent pas leurs artefacts.
- Mode `s3` (`ORCH_ARTIFACTS_STORE=s3`, variables `ORCH_S3_*`) : partageable. **Vérifié** avec SeaweedFS ; deux hôtes **simulés** par deux volumes ; `https` et restauration S3 **non testés**.

### 2.5 PostgreSQL : le point unique
- Il porte la file, l'état des runs, l'audit et les sessions. Pas de réplica, pas de bascule automatique dans le dépôt.
- Livré : PgBouncer, sauvegardes, restauration à un instant donné (`infra/docker-compose.pitr.yml`). Une indisponibilité de la base arrête la plateforme jusqu'à reprise.
- Pour la haute disponibilité : service PostgreSQL managé ou Patroni, **hors du dépôt, non fourni, non testé**. Pointer `ORCH_DB_HOST` vers le point d'entrée stable.

### 2.6 Kubernetes
`infra/k8s/` (kustomize) : `api-ui` 2 réplicas, `api-ingest` 2, `runner-gw` 2, `orchestrator` 1, `worker-aux` 1, `pgbouncer` 1 ; HPA sur le CPU (70 %) pour `api-ui` (2 à 6) et `api-ingest` (2 à 8) ; PDB ; NetworkPolicy.
`kubectl kustomize` rend les objets et `tests/test_k8s_manifests.py` en vérifie la structure ; **jamais appliqué sur un cluster** : aucun pod démarré, aucune NetworkPolicy exercée. PostgreSQL, TLS et interface statique sont à fournir.

## 3. Montée en charge verticale

Plus de CPU et de mémoire n'améliorent le débit que si l'on change aussi les réglages ci-dessous : **la plupart sont fixes dans l'image ou le compose**.

| Réglage | Valeur livrée | Où | Comment le changer |
|---|---|---|---|
| Processus gunicorn | `-w 2` | `CMD` de `infra/Dockerfile.backend`, `command:` de `infra/docker-compose.roles.yml` | **pas de variable d'environnement** : redéfinir `command:` dans un fichier compose de surcharge |
| Threads par processus | `--threads 8` (16 pour `runner-gw`) | idem | idem |
| Requêtes simultanées par réplica API | 16 (2 × 8) | déduit | `-w` × `--threads` |
| Créneaux d'exécution par worker | `ORCH_WORKER_CONCURRENCY=1` (1 à 16) | variable d'environnement | à essayer sur un hôte qui suit ; gain **non démontré** (×1,23 mesuré) |
| Actions simultanées par espace | `ORCH_MAX_CONCURRENT_PER_WORKSPACE=4` | variable d'environnement | augmenter si l'hôte et les workers suivent |
| Bail des tâches | `ORCH_LEASE_SECONDS=30` | variable d'environnement | |
| Connexions persistantes | `ORCH_DB_CONN_MAX_AGE=60` (avec PgBouncer) | variable d'environnement | |

Règle de bon sens (déduite) : **mieux vaut plus de réplicas moyens qu'un seul très gros**, car un réplica API est borné à 16 requêtes simultanées quelle que soit sa taille tant que `-w` et `--threads` ne changent pas, et car l'arrêt ou la mise à jour d'un réplica reste sans coupure quand il y en a plusieurs.

## 4. Arithmétique des connexions (déduit)

Configuration de `infra/docker-compose.prod.yml` : PgBouncer en mode transaction, `MAX_CLIENT_CONN=400`, `DEFAULT_POOL_SIZE=20`, `QUERY_WAIT_TIMEOUT=30`.

- Connexions **clientes** ≈ Σ (processus × threads) de chaque réplica API, plus un fil par boucle de worker et d'orchestrateur. Un réplica API à `-w 2 --threads 8` en prend jusqu'à 16 (la connexion reste ouverte 60 s).
  Avec 400 connexions clientes autorisées, on atteint la limite autour de **25 réplicas API** (en ignorant les workers) : à relever (`MAX_CLIENT_CONN`) avant d'y arriver.
- Connexions **serveur** : 20 vers PostgreSQL, partagées. Quand tous les réplicas sont occupés, les requêtes attendent une connexion libre, au plus 30 s, puis échouent proprement. Si la latence monte avec le nombre de réplicas, le goulot est ce pool (ou PostgreSQL), pas l'API.
- `DEFAULT_POOL_SIZE` doit rester **très inférieur** à `max_connections` de PostgreSQL (100 par défaut), en comptant les autres consommateurs (sauvegardes, supervision, console).
- Mode transaction : la plateforme n'utilise ni `SET` de session, ni verrous consultatifs, ni curseurs serveur (vérifié par la suite de tests). Ne rien ajouter qui en dépende.
- La mesure de connexions ouvertes avant/après PgBouncer **n'a pas été faite**.

## 5. Ressources : par où commencer

Aucune mesure de consommation mémoire ou CPU par rôle n'est publiée. Les points de départ ci-dessous sont des **ordres de grandeur prudents à valider par la supervision**, pas des garanties.

| Taille | Usage visé | Topologie | Remarque |
|---|---|---|---|
| Petite | une équipe, quelques runs par minute | pile `docker-compose.prod.yml` : 1 `api`, 1 `orchestrator`, 1 `worker`, PgBouncer, PostgreSQL ; 4 vCPU, 8 Go sur un seul hôte | c'est la configuration exercée en développement |
| Moyenne | plusieurs équipes, webhooks | pile par rôles (`docker-compose.roles.yml`), 2 `api-ui`, 2 `api-ingest`, 2 `orchestrator`, 2 à 4 `worker` ; PostgreSQL sur un hôte dédié | recette de mise à jour progressive disponible |
| Grande | forte charge d'actions | workers d'exécution répartis sur plusieurs hôtes Docker ou agents, artefacts en `s3`, PostgreSQL managé en haute disponibilité | **non éprouvée** : à valider par un banc avant engagement |

## 6. Que surveiller pour savoir quand agrandir

Règles d'alerte livrées : `infra/monitoring/fluxonata.rules.yml` (testées par `scripts/alerts-test.sh`).

- **File qui ne se vide pas** : `fluxonata_executions{status="queued"}` et `fluxonata_queue_oldest_age_seconds` → ajouter des workers/créneaux, ou agrandir l'hôte d'exécution.
- **Latence de l'interface** : p95 des requêtes → plus de réplicas `api-ui` (objectif de test de charge : 300 ms au p95 pour la liste des actions ; atteint sur un essai, **non atteint** de 11 % sur un autre, avec davantage d'actions dans l'espace).
- **Attentes de connexion** (erreurs « timeout » de PgBouncer) → relever `DEFAULT_POOL_SIZE` dans la limite de PostgreSQL, ou réduire le nombre de threads.
- **Base** : CPU, latence d'écriture (`fsync`), taille des tables d'historique (la purge et la rétention sont décrites dans [ADMIN.md](ADMIN.md)).

## 7. Ce qui n'est pas démontré

1. Un **débit** qui croît avec le nombre de workers : objectif ×1,8 non atteint sur la machine d'essai ; à refaire sur un hôte dédié.
2. Le gain de débit avec plusieurs **orchestrateurs** (la sûreté, elle, est démontrée).
3. La **haute disponibilité** de PostgreSQL.
4. Les manifestes **Kubernetes** appliqués sur un cluster réel et l'**autoscaling** sur la file.
5. Les artefacts S3 sur **deux vrais hôtes**, la restauration S3, `https`.
6. Un pool de **conteneurs préchauffés** qui lèverait le plafond lié au démarrage des conteneurs : décision documentée, non implémentée.
7. Toute consommation **mémoire/CPU** par rôle.

Pour trancher 1, 2 et 7 : [BENCH_RUNBOOK.md](BENCH_RUNBOOK.md) et `scripts/loadtest.sh`, sur un serveur dédié et inactif.
