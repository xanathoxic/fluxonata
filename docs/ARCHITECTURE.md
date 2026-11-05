# Architecture — rôles, privilèges, objectifs de service

Écrit le 2026-11-04 (AR0.2 et AR0.6 de `docs/ROADMAP_ARCHITECTURE.md`). **Méthode** : chaque affirmation « constaté » vient d'une lecture du code ou de `infra/`
à cette date (fichier cité) ; rien ici n'a été « exécuté » sauf les mesures citées avec leur source. Ce document corrige l'ADR 0022 là où le code contredit
ses hypothèses (voir § 3). Il se met à jour avec le code : un changement de privilège d'un rôle est une modification de ce fichier.

## 1. Processus et rôles

| Processus | Commande | Rôle fonctionnel |
|---|---|---|
| `api` | `gunicorn fluxonata.wsgi` (2 processus × 8 threads) | interface, API authentifiée, webhooks, formulaires publics, long-poll des runners |
| `orchestrator` | `manage.py run_orchestrator` | avance les runs (`engine.advance`), planifications, callbacks sortants, reprise des baux |
| `worker` | `manage.py run_worker` | exécute les actions (conteneurs) **et** 7 threads de fond (IA, étapes HTTP/SQL/SFTP/SMTP/LDAP, interrogation, listes dynamiques, alertes, évaluations, Git) |
| `migrate` | `manage.py migrate && createcachetable` | migrations, une fois au démarrage |
| `pgbouncer`, `db`, `web` (nginx), `testsvc` | images tierces | pooler, PostgreSQL, fichiers statiques et proxy, service de test |

Deux topologies de production existent : **compacte** (un `api`, un `worker` ; `docker-compose.prod.yml`) et **par rôles** (`docker-compose.prod.yml` + `docker-compose.roles.yml` : api-ui, api-ingest, runner-gw, orchestrator, worker-exec, worker-aux, worker-ai à la demande). Les tableaux § 2 décrivent la compacte ; § 2 bis la topologie par rôles. Rôles cibles : voir ADR 0022 (`api-ui`, `api-ingest`, `runner-gw`, `orchestrator`, `worker-exec`, `worker-aux`, `worker-ai`, `web`) et § 4.

## 2. Privilèges aujourd'hui (pile de production, `infra/docker-compose.prod.yml`)

| Service | Utilisateur | Base | Clé du coffre (`/keys`) | Artefacts | `docker.sock` | Réseau sortant |
|---|---|---|---|---|---|---|
| `api` | 10001, **capacités retirées, lecture seule** (AR2.5) | via PgBouncer | **oui** | volume ou S3 | non | **libre** (aucune règle de conteneur) ; filtré par `core/netsec` |
| `orchestrator` | 10001, capacités retirées, lecture seule | via PgBouncer | **non** (AR4.2) | **non** (AR2.5 : la rétention tourne dans le worker) | non | libre ; filtré par `core/netsec` |
| `worker` | **root (0:0)**, `no-new-privileges`, image `exec` (seule avec le CLI Docker) | via PgBouncer | **oui** | volume ou S3 | **oui** | libre ; filtré par `core/netsec` ; les conteneurs d'action sont `--network none` ou réseau de test |
| `migrate` | 10001, capacités retirées, lecture seule | **directe** | **non** (AR2.5) | **non** (AR2.5) | non | libre |
| `pgbouncer` | postgres | vers `db` | non | non | non | `db` seulement |
| `db` | postgres | — | non | non | non | aucun besoin |
| `web` | nginx | non | non | non | non | vers `api` |

## 2 bis. Privilèges aujourd'hui, topologie PAR RÔLES (`docker-compose.roles.yml`, mesurés AR2.7)

| Service | Réseaux | Clé | Artefacts | `docker.sock` | Image | Sortie Internet |
|---|---|---|---|---|---|---|
| `api-ui` | edge, data, egress | oui | oui | non | runtime | **oui** (mesuré) |
| `api-ingest` | edge, data (**internes**) | oui (signature des webhooks) | **non** | non | runtime | **non — mesuré** : connexion à 1.1.1.1:443 impossible |
| `runner-gw` | edge, data, egress | oui | oui | non | runtime | oui |
| `orchestrator` | **data seul** (AR4.2) | **non** | non | non | runtime | **non** |
| `worker-exec` | data, egress | oui | oui | **oui** | exec (CLI Docker), root | oui |
| `worker-aux` | data, egress | oui | oui | non | runtime | oui |
| `worker-ai` (profil `ai`) | data, egress | oui | oui | **oui** (essai à blanc, C-2) | exec, root | oui |
| `db`, `pgbouncer`, `migrate` | data (interne) | non (`migrate` sans volumes) | non | non | tiers / runtime | non |
| `web` | front, edge | non | non | non | nginx | — |

**Topologie par agents** (`docker-compose.agent.yml`, en plus des deux fichiers) : `worker-exec` et `worker-ai` sont remplacés par `exec-agent` — socket Docker **oui**, clé **non**, volume des artefacts **non**, variables de base **non**, réseau `edge` seul (parle uniquement à `runner-gw`). C'est le seul mode où I1 est vrai.

Isolation mesurée : `api-ingest` gelé et inondé de 40 webhooks → `GET /actions/` p95 21 ms, 0 erreur.

Constats d'ensemble (topologie compacte) : la clé du coffre est montée dans **quatre** services ; `docker.sock` (équivalent root de l'hôte) est dans le **même** conteneur que la clé et que
sept threads de fond ; **aucune restriction réseau au niveau des conteneurs** : toute la protection SSRF est applicative (`core/netsec.py`, ADR 0006). L'image du backend
contient `docker-cli` pour tous les rôles (`infra/Dockerfile.backend`).

## 3. Constats de l'inventaire (corrections de l'ADR 0022)

Chaque ligne : ce que le code fait → conséquence pour les rôles → tâche.

| # | Constaté (fichier) | Conséquence | Tâche |
|---|---|---|---|
| C-1 | `runs/engine.py:_image_digest` lance `docker image inspect` depuis **`start_run`**, donc dans le process **API** (webhooks, lancements manuels, formulaires) et l'orchestrateur (sous-workflows). Sans socket (cas de la pile de production : seul le worker en a un), l'appel échoue en silence et le digest enregistré dans la définition du run vaut **`None`** | l'empreinte d'image d'exécution n'est jamais tracée en production ; l'API appelle le CLI Docker (jusqu'à 10 s, thread bloqué) ; `docker-cli` est inutilement dans les images API/orchestrateur | AR3 : digest fourni par la configuration (image épinglée) ou rapporté par l'exécuteur, plus d'appel Docker hors exécuteur |
| C-2 | `ai/service.py:1202` : l'essai à blanc d'un workflow généré (`dryrun`) exécute des scripts via `get_runner()` — donc **Docker** — dans le thread `ai-jobs` du worker | `worker-ai` a besoin d'un bac à sable : l'ADR 0022 (« `docker.sock` : non ») est **faux** tant que l'essai à blanc est local | AR2.4 : `worker-ai` sans socket **uniquement si** l'essai à blanc passe par l'exécuteur isolé (AR3) ; sinon, il garde le socket et on le dit |
| C-3 (**corrigé**, AR4.2) | `runs/orchestrator.py:tick` appelait `callbacks.deliver_due()` : jusqu'à **50 POST sortants de 5 s** (`runs/callbacks.py`) **dans le tick**, chaque appel signé avec un secret d'espace déchiffré | l'orchestrateur a besoin de la **clé** et d'un **réseau sortant** ; un point d'arrivée lent peut retarder l'avancement de **tous** les runs jusqu'à 250 s par tick (constaté par lecture, **non mesuré**) | **Fait (AR4.2, 2026-11-05)** : les rappels partent du fil `callbacks` du rôle `aux` ; l'orchestrateur n'a plus ni clé ni réseau sortant (testé, mesuré en conteneur) |
| C-4 | `triggers/views.py:_signing_secret` : la vérification HMAC d'un webhook déchiffre le secret du webhook | `api-ingest` a besoin de la **clé** (l'ADR disait « non, à confirmer ») ; mais **aucun réseau sortant** n'est nécessaire | AR2.3 ; piste de réduction : AR7.2 (courtier de secrets, clé par usage) |
| C-5 (**assumé**, AR2.8, 2026-11-05) | `ai/views.py` : `ExplainErrorView`, `edit_workflow(dry_run=False)` et la recherche appellent un fournisseur d'IA **dans la requête web** (`service.run_prompt`, `service.catalog_search`) ; seuls le plan, la génération et l'édition de l'éditeur passent par des jobs du worker | `api-ui` a besoin d'un réseau sortant vers les fournisseurs d'IA et d'accès à leurs clés | AR2 : décision à prendre — soit assumé, soit passage en jobs (`worker-ai`) |
| C-6 | `runs/runner_api.py` : `runner-gw` livre aux runners des secrets déchiffrés et, à la fin d'une tâche, écrit les artefacts (`finalize`) | `runner-gw` : clé **oui**, écriture dans le stockage d'artefacts **oui** (S3 → réseau sortant vers ce seul service) | AR2.3 |
| C-7 | `migrate` héritait des volumes `keys` et `artifacts` du bloc `x-backend` | privilèges inutiles pour un job de migration | **corrigé (AR2.5)** : `volumes: []`, testé |
| C-8 | `vault/management/commands/rotate_keys`, `tenancy/.../bootstrap`, `seed_demo` : opérations d'administration exécutées par `docker compose exec` dans le conteneur `api` | un rôle « admin ponctuel » existe de fait : il faut la clé et la base directe | AR2.5 : le documenter comme tel (conteneur jetable, pas dans un service permanent) |
| C-9 | Les 7 threads du worker (`runs/worker.py`) : IA, étapes HTTP/SQL/SFTP/SMTP/LDAP (`http_node.process_one` → `sql_node`, `sftp_node`, `smtp_node`, `ldap_node`), interrogation (`triggers/poller`, `triggers/mail`), listes dynamiques, alertes, évaluations, Git (`workflows/gitsync`) | tous demandent la **clé** (secrets de connexion) et un **réseau sortant** vers des systèmes du client ; **aucun** n'a besoin de `docker.sock` hormis `ai-jobs` (essai à blanc, constaté) et `ai-evals` **seulement** quand la politique d'IA autorise l'essai à blanc et que le workflow contient un script (constaté en AR2.9) | AR2.4 |

## 4. Privilèges cibles par rôle (ADR 0022 corrigé)

`B` = base (P = via pooler, D = directe), `K` = clé du coffre, `S` = `docker.sock`, `R` = réseau sortant (vers quoi), `A` = stockage d'artefacts.

| Rôle | B | K | S | R | A | Dépend de | Remarque |
|---|---|---|---|---|---|---|---|
| `api-ui` | P | oui | non | OIDC, coffre externe, **fournisseurs d'IA** (C-5), test de connexions, S3 | lecture | AR2.3 | le plus exposé aux utilisateurs authentifiés |
| `api-ingest` | P | oui (**webhooks seulement**, C-4) | non | **aucun** | non | AR2.3 | exposé à Internet : c'est le rôle où « pas de sortie » a le plus de valeur |
| `runner-gw` | P | oui (livraison de secrets) | non | S3 seulement | écriture | AR2.3 | |
| `orchestrator` | P | **non** (AR4.2) | non | **aucun** (AR4.2) | non | — | fait |
| `worker-exec` | P (puis aucune après AR3) | oui (puis non) | **oui** | conteneurs d'action : `none` ou réseau de test | écriture | AR3 | destiné à devenir un agent sans base ni clé |
| `worker-aux` | P | oui | non | systèmes du client (HTTP, SQL, SFTP, SMTP, LDAP, Git, messagerie, alertes) | lecture/écriture | AR2.4 | |
| `worker-ai` | P | oui (clés des fournisseurs) | **oui tant que l'essai à blanc est local** (C-2) | fournisseurs d'IA | non | AR2.4, AR3 | |
| `migrate` (job) | **D** | non | non | aucun | non | AR2.5 | |
| `admin` (job ponctuel, C-8) | D ou P | oui | non | selon la commande | selon | AR2.5 | pas un service permanent |
| `web` | non | non | non | `api-*` | non | — | |

## 5. Invariants à tester (AR2.5)

| # | Invariant | Vrai aujourd'hui ? |
|---|---|---|
| I1 | Aucun rôle ne détient à la fois `docker.sock` **et** la clé du coffre (hors `worker-ai` tant que C-2 dure) | **non** en topologie compacte et par rôles (le worker) ; **oui en topologie PAR AGENTS** (`docker-compose.agent.yml`, ADR 0023 : `exec-agent` sans clé ni base, mesuré en conteneur) |
| I2 | `docker.sock` n'est monté que dans `worker-exec` (et `worker-ai` tant que C-2) | oui (seul le `worker`) ; **testé** |
| I3 | `api-ingest` n'a aucun accès réseau sortant | **oui dans la topologie par rôles — mesuré** (réseaux internes seulement) ; sans objet en compacte |
| I4 | `orchestrator` n'a ni clé ni réseau sortant | **oui** (AR4.2 : `tests/test_orchestrator_without_key.py`, `tests/test_privilege_matrix.py`, `prod-smoke.sh` — un vrai workflow avancé par un orchestrateur sans `/keys` ; réseau `data` seul en topologie par rôles) |
| I5 | `migrate` n'a ni clé ni artefacts | **oui** (AR2.5, `tests/test_privilege_matrix.py`) |
| I6 | Aucun rôle sauf `worker-exec` n'a le CLI Docker | **oui pour l'image** (AR2.5 : cible `runtime` sans CLI, testée) ; **le code l'appelle encore** (`_image_digest` → `None`, sans effet) : AR3.8 |
| I7 | Chaque rôle démarre sans les privilèges qu'il ne doit pas avoir (ou échoue explicitement) | **partiel** : sans volume des clés → erreur explicite (`tests/test_key_autocreate.py`) ; sans CLI Docker → `None` sans exception ; les autres privilèges non testés |

## 6. Objectifs de service (AR0.6)

Objectifs de **conception** pour une installation sur un hôte ou quelques hôtes, pas des engagements contractuels. Ceux qui ont déjà été mesurés l'ont été **avec des seuils
posés avant la mesure** (sources citées) ; les autres sont des cibles **non mesurées**, fixées ici avant de l'être.

| # | Objectif (SLO) | Cible | Mesure / source | État |
|---|---|---|---|---|
| S1 | Aucune exécution ni livraison acquittée perdue ou dupliquée, sous panne d'un rôle ou de la base | **0** | `scripts/resilience.sh` A–H ; `tests/test_services` ; O3a de `LOAD_TEST.md` | **mesuré** 2026-11-04 : 8/8 (pile de développement) ; un défaut réel corrigé (webhooks) |
| S2 | Reprise après la panne d'un rôle seul | orchestrateur ≤ 30 s ; API ≤ 15 s ; base ≤ 20 s | scénarios E, F, G | **mesuré** : 6 s, 6 s, 5 s (développement) |
| S3 | Reprise après arrêt brutal de tous les services pendant une attente | run terminé sans étape rejouée | scénario A | **mesuré** : OK |
| S4 | Latence de lecture de l'API (liste des actions, 20 clients) | p95 < 300 ms | O1 de `LOAD_TEST.md` | mesuré : 193 ms (t3), 334 ms (t4, 36 actions) ; **sérialisation 17,2 → 2,9 ms** depuis AR1.5 ; **non remesuré en HTTP** |
| S5 | Acceptation d'une soumission (webhook, lancement) | p95 < 500 ms | O2 | mesuré : 216 ms, 213 ms |
| S6 | L'interface reste réactive pendant une rafale de webhooks ou 60 runners en attente longue | `health/live` : 0 erreur, max < 1 s | `scripts/loadtest_runners.py` (AR1.3) | **mesuré** : 289 réponses, 0 erreur, max 500 ms (4 créneaux) ; **sans plafond : max 30 s** |
| S7 | Débit du moteur sans conteneur | ≥ 500 runs/min (1 orchestrateur, 1 worker) | O4 | mesuré : 763 runs/min — **mais O4 était « informatif », sans objectif ; cette cible est posée après coup et ne compte pas comme un objectif tenu** |
| S8 | Débit avec actions conteneurisées | passage 1→4 agents ≥ ×1,8 | O3b | **non atteint** (×1,40, hôte saturé) → AR3.4 |
| S9 | Délai déclencheur → début de run (hors file d'actions) | p95 ≤ 2 s | à mesurer sur le banc AR0.3 | **non mesuré** ; **coût du tour de l'orchestrateur mesuré** (AR4.4) : 3 ms avec 800 runs en attente (15 s pour 400 avant le correctif) |
| S10 | Perte de données tolérée après un sinistre (RPO) | ≤ 24 h **si** la sauvegarde quotidienne est installée ; ≈ 60 s avec l'archivage WAL optionnel (AR5.5) | `scripts/backup.sh`, `restore-test.sh` | restauration testée à la main (2026-10-25) ; **unités systemd de sauvegarde quotidienne non exécutées** (`docs/PROJECT_STATE.md`) ; RPO continu non fait |
| S11 | Temps de restauration (RTO) | ≤ 1 h pour une base de démonstration | `scripts/restore-test.sh`, `scripts/pitr-test.sh` | **PITR : 19 s sur une base minuscule** ; `restore-test.sh` : durée toujours non consignée ; aucune mesure sur une vraie volumétrie |
| S12 | Mise à jour d'un rôle sous charge | 0 perte, 0 doublon ; erreurs UI ≤ 2 %, coupure ≤ 8 s (fixés avant la mesure) | AR6.1 `scripts/rolling_upgrade_test.py` | **mesuré** : 0 perte / 0 doublon sur ≈ 900 livraisons ×3 ; **avec garde de santé 0 erreur UI** ; **sans garde, coupure de 9-10 s** (seuil non tenu) ; 1 seul réplica pour runner-gw/orchestrateur/workers : surface brièvement indisponible, sans perte |
| S13 | Un espace qui sature la plateforme dégrade un autre espace de moins de X % | X à fixer avant mesure | AR5.4 | **non chiffré** ; l'équité de prise a été auditée (plafond par espace + espace le moins chargé d'abord) et une **famine réelle corrigée** (un espace au plafond avec > 200 tâches en file empêchait tout autre espace d'être servi) ; pas de quota de longueur de file |

Règles de lecture : un objectif « mesuré » dit sur quelle pile ; **aucun** n'a été mesuré sur la pile de production ni sur un hôte dédié (AR0.3 reste à faire) ; un objectif
n'est jamais modifié après sa mesure pour s'y conformer (une correction du banc est notée comme telle, voir `docs/TASKS.md` AR0.4).

## 7. Limites de ce document
- Les rôles cibles n'existent pas encore : les § 4 et 5 décrivent ce qu'il faudra obtenir, pas ce qui est.
- L'inventaire C-1 à C-9 est issu de lectures ciblées (recherche des appels Docker, de la clé, du réseau sortant) ; un appel indirect non trouvé reste possible. Le test
  d'invariants (I7) est précisément fait pour le révéler.
- Le fait qu'une action de client s'exécute avec `--network none` ou sur un réseau de test (`runs/runners.py`) est une propriété de **l'exécuteur**, pas des rôles ci-dessus.
