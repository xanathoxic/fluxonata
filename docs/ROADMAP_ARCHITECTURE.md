# Feuille de route applicative — architecture

Rédigée le 2026-11-04 à partir d'une lecture du code, des ADR 0001–0021, de `infra/` et de `docs/LOAD_TEST.md`.
**Rien de ce document n'a été exécuté ni mesuré** : tout critère de sortie est « non exécuté » tant qu'une preuve (test, mesure, procédure rejouée) n'est pas consignée dans `docs/SESSION_LOG.md`.
Complète `docs/ROADMAP.md` (fonctionnel) ; n'en modifie pas les priorités A à C.

Légende : ✅ fait · 🟡 partiel · ⬜ à faire. Effort : S ≈ 1 jour, M ≈ 2-4 jours, L ≈ 1-2 semaines, XL > 2 semaines.
Règles inchangées : aucun module « enterprise », aucun secret dans Git/logs/prompts, autorisation côté serveur, jamais d'action dans le process web, pas de réécriture d'historique sans accord.

---

## 0 bis. Bilan au 2026-11-05 (45 tâches `AR*.*`)

**28 faites et prouvées · 10 partielles · 7 non faites** (dont 4 par décision argumentée, 3 non exécutables ici). Le détail, preuve par preuve, est dans `docs/TASKS.md` (section « Architecture ») ; la liste ci-dessous dit seulement ce qui change pour l'exploitant.

**Ce qui est livré et mesuré** — isolation des pannes (8 scénarios de panne réels dont 4 orchestrateurs dont un tué ; `api-ingest` gelé sans effet sur l'interface), mises à jour partielles (migrations sûres vérifiées en CI, ancien code sur nouveau schéma, **mise à jour progressive sous charge : 0 livraison perdue sur ≈ 900, 0 erreur de l'interface avec garde de santé**), séparation des privilèges (orchestrateur sans clé ni réseau ; `api-ingest` sans sortie ; image sans CLI Docker ; **agent d'exécution sans base ni clé**), scalabilité du moteur (**tour de l'orchestrateur 15 s → 3 ms** pour 400 runs en attente), équité entre espaces (famine corrigée), restauration à un instant donné prouvée.

**Défauts réels du produit trouvés et corrigés au passage** (aucun n'était dans le plan initial) : perte d'événement de webhook entre deux transactions ; clé du coffre fabriquée silencieusement par un service démarré sans son volume ; coût linéaire (verrou + `fsync` par run) du tour de l'orchestrateur ; famine d'un espace derrière un long carnet ; `ORCH_ROLES` d'un worker rejeté par le chargement des routes de l'API ; runner « bootstrap » éligible à aucune tâche ; course de démarrage PostgreSQL/migrations sur volume neuf ; digest d'image tracé à `None` en production.

**Ce qui n'est PAS atteint** — le passage à l'échelle de l'**exécution des actions** (×1,23 avec 4 créneaux, objectif ×1,8) : le démarrage d'un conteneur borne l'hôte de mesure ; il faut un hôte dédié (`docs/BENCH_RUNBOOK.md`). Aucun objectif de service n'a été mesuré **sur un hôte dédié** ni sur la pile Kubernetes (jamais appliquée).

**Décisions prises sous délégation de l'utilisateur, à rouvrir par lui** : AR2.8 (appels d'IA synchrones assumés dans `api-ui`), ADR 0023 (le worker intégré reste ; les agents sont le mode « sans clé »), ADR 0024 (aucune extraction de services), AR4.3 / AR5.2 / AR5.3 (non faits faute de besoin mesuré).

## 1. Objectif et principes

**Objectif.** Une plateforme qui (1) limite le rayon d'une panne, (2) se met à jour par composant sans arrêt global, (3) monte en charge par ajout de capacité mesurée, (4) reste installable par un seul `compose` et exploitable sans équipe dédiée.

**Principes directeurs**
1. **Découper selon les axes de panne, de charge et de confiance — pas selon les modules de code.** Le graphe d'imports est cyclique (`runs`↔`workflows`, `runs`→`vault` ×18, `ai`→`workflows` ×19) : un découpage par module serait artificiel.
2. **Le noyau transactionnel reste unique** : `tenancy`, `catalog`, `workflows`, `triggers`, `runs` (moteur) partagent une base PostgreSQL ; la garantie « état + tâche dans la même transaction » (ADR 0003) est un acquis prouvé qu'on ne sacrifie pas.
3. **Un code, plusieurs déploiements** avant toute extraction de service.
4. **Moindre privilège par rôle** : chaque rôle ne reçoit que la base, les clés et les sockets dont il a besoin.
5. **Compatibilité N/N-1** entre rôles et schéma : condition de toute mise à jour progressive.
6. **Pas de composant sans mesure** : aucun nouveau composant d'infrastructure (cache, broker, pooler) n'entre sans besoin chiffré et sans ADR.
7. **Chaque jalon est livré avec sa preuve** (test ou script rejouable), dans l'esprit de `resilience.sh`, `loadtest.sh`, `restore-test.sh`.

## 2. Point de départ (constats)

| # | Constat | Gravité |
|---|---|---|
| C1 | `run_worker` : exécution des actions + 7 threads de fond (ai-jobs, http-steps, poll-triggers, form-lookups, alerts, ai-evals, git-sync) dans un seul process root avec `docker.sock` et la clé du coffre | Critique |
| C2 | Volume `keys` monté dans api, orchestrator et worker : tous peuvent déchiffrer | Critique |
| C3 | API unique (`gunicorn -w 2 --threads 8` = 16 requêtes simultanées) pour interface, webhooks, formulaires publics et long-poll des runners (1 thread chacun) | Critique |
| M1 | Débit avec action en conteneur : 8–11 runs/min (≈ 5 s/`docker run`) contre 763 runs/min moteur seul (O3b non atteint) | Majeur |
| M2 | Orchestrateur : balayage de ≤ 500 runs actifs toutes les 0,5 s, séquentiel ; sûreté multi-instances non démontrée ; livraison de callbacks dans le tick | Majeur |
| M3 | PostgreSQL : SPOF, file + état + audit + sessions, `CONN_MAX_AGE=0`, pas de pooler, pas de réplica ; polling multiplié par le nombre de processus | Majeur |
| M4 | `LocMemCache` : limites de débit par process (× N réplicas) | Majeur |
| M5 | Artefacts sur volume local | Majeur |
| M6 | Une image, un `migrate` : pas de mise à jour progressive garantie | Majeur |
| m1 | Équité par espace (voisin bruyant), croissance des tables d'historique, traces distribuées | Mineur — *(à vérifier)* |

Points forts à préserver : reprise durable (lease/fencing), protocole runner HTTP (ADR 0013), protocole worker v1, finalisation côté plateforme (`runs/finalize.py`), sessions en base (partagées entre réplicas).

## 3. Vue d'ensemble des jalons

| Jalon | Thème | Effort | Dépend de | Résout |
|---|---|---|---|---|
| **AR0** | Fondations de preuve (mesure, chaos, compat N/N-1) | M | — | prérequis de tous |
| **AR1** | Correctifs à architecture constante | M | AR0 | M3 (partiel), M4, M5, C3 (partiel) |
| **AR2** | Déploiements par rôle (même code) | M–L | AR0, AR1 | C1 (partiel), C3, M6 (partiel) |
| **AR3** | Plan d'exécution isolé (worker = runner) | L | AR2 | C1, C2, M1 |
| **AR4** | Orchestrateur scalable | L | AR0, AR2 | M2 |
| **AR5** | Données : HA, historique, équité | L–XL | AR1 | M3, m1 |
| **AR6** | Livraison et exploitation (progressive, Kubernetes, observabilité) | L | AR2 | M6 |
| **AR7** | Extractions facultatives (passerelle IA, courtier de secrets) | L chacune | AR3, AR6 | C2 (final) |

Ordre recommandé : AR0 → AR1 → AR2 → (AR3 ∥ AR4) → AR5 → AR6 → AR7 sur besoin. Chaque jalon est livrable seul et laisse la plateforme installable.

---

## 4. Détail des jalons

### AR0 — Fondations de preuve (M)
Avant de modifier, pouvoir démontrer qu'on n'a rien cassé.

| Tâche | Contenu | Preuve attendue | État |
|---|---|---|---|
| AR0.1 | **Cartographie des dépendances** : script CI qui calcule le graphe d'imports inter-modules et échoue si un nouveau cycle apparaît (import-linter ou équivalent) | CI rouge sur cycle ajouté | ✅ 2026-11-04 (base gelée de 71 dépendances ; **9 modules sur 12 dans un seul cycle** — ce qui confirme qu'on ne peut pas extraire un module sans d'abord le désenchevêtrer) |
| AR0.2 | **Contrats de rôle** : doc `docs/ARCHITECTURE.md` listant pour chaque rôle base / clé / socket / réseau / fichiers autorisés | Relu, référencé par l'ADR 0022 | ✅ 2026-11-04 (`docs/ARCHITECTURE.md` ; **a corrigé 5 hypothèses de l'ADR 0022**) |
| AR0.3 | **Banc de charge de référence** sur hôte dédié et inactif (relance de O1–O4, ajout de O5 : N orchestrateurs, O6 : 100 runners en long-poll) | Résultats dans `LOAD_TEST.md`, sans modifier les objectifs fixés | ⬜ non exécutable ici ; runbook livré |
| AR0.4 | **Chaos élargi** : `resilience.sh` + arrêt de chaque rôle isolément, PostgreSQL coupé pendant une rafale de webhooks, deux orchestrateurs simultanés | 0 perte / 0 doublon, reprise en ≤ X s (X fixé avant la mesure) | ✅ 2026-11-04 : 8/8 scénarios (A–D existants ; E orchestrateur seul 6 s ≤ 30, F API seule 1re réponse 6 s ≤ 15, G base arrêtée en rafale de 30 webhooks : 30/30 runs réussis, 0 doublon, 5 s ≤ 20, H deux orchestrateurs dont un tué avec 12 runs en vol : 12/12, 0 étape démarrée deux fois, 10 s ≤ 60). **A trouvé et corrigé un défaut réel** (perte d'événement webhook, voir TASKS). Réserves : le worker n'est pas testé en double instance ; le banc G **ne détecte pas** ce défaut (passe aussi avec l'ancien code) — seul le test unitaire le couvre ; seuils mesurés sur la pile de développement (un process par rôle, `runserver`, Docker Desktop), pas sur la pile de production |
| AR0.5 | **Test de compatibilité N/N-1** : ancien code sur le nouveau schéma (le sens inverse ne se produit pas : `migrate` se termine avant tout autre service) | Script rejouable `scripts/compat-test.sh`, branché en option dans `ci.sh` | ✅ 2026-11-04 (ancien code `49e2d43` vert sur le schéma courant ; auto-test de détection OK) |
| AR0.6 | **Objectifs de service (SLO) publiés** : disponibilité API, délai entre déclencheur et début de run, perte tolérée (zéro) | Section dans `ARCHITECTURE.md` | ✅ 2026-11-04 (13 objectifs : 5 mesurés, 3 mesurés avec réserves, 1 non atteint, 4 non mesurés) |

**Sortie :** banc et SLO fixés *avant* toute optimisation ; cycle d'imports gelé.

### AR1 — Correctifs à architecture constante (M)
Gains immédiats, sans nouveau rôle.

| Tâche | Contenu | Critère de sortie | État |
|---|---|---|---|
| AR1.1 | **Limites de débit partagées** : backend de cache partagé (cache en base par défaut ; Redis en option documentée) pour les throttles DRF ; test à 2 processus | Plafond effectif = plafond configuré, indépendamment du nombre de réplicas | ✅ 2026-11-04 pour les portées strictes (mesuré : 18 requêtes passent avec `locmem` sur 2 workers, 10 avec `db`) ; Redis non exécuté ; limite générale par utilisateur volontairement locale |
| AR1.2 | **Pooler de connexions** : PgBouncer (mode transaction) dans le compose de production ; `CONN_MAX_AGE` > 0 hors pooler ; vérifier l'absence de dépendance à l'état de session (`SET`, verrous consultatifs, curseurs serveur) | Suite pytest verte via le pooler ; mesure de connexions ouvertes avant/après | ✅ 2026-11-04 sur la suite (aucune dépendance d'état de session trouvée dans la plateforme : 2 `.iterator()` neutralisés par `DISABLE_SERVER_SIDE_CURSORS`, aucun verrou consultatif ni `LISTEN`) ; **mesure de connexions avant/après non faite** (banc AR0.3) |
| AR1.3 | **Long-poll des runners hors du pool de threads** : variante ASGI du seul endpoint `jobs/request`, ou plafond de connexions de long-poll par process avec réponse `429` + retry | 100 runners en attente n'augmentent pas la latence p95 de `GET /actions/` (nouvel objectif O6) | ✅ 2026-11-04 variante plafond (60 runners : `health/live` max 30 s → 500 ms ; mesuré sur `health/live`, pas sur `GET /actions/`) ; ASGI non fait |
| AR1.4 | **Interface de stockage des artefacts** : abstraction (`ArtifactStore`) avec backends disque et S3-compatible ; migration d'un volume existant | API et worker sur deux hôtes lisent les mêmes artefacts ; test de restauration inclut les artefacts | ✅ 2026-11-04 (SeaweedFS réel + faux serveur ; deux hôtes **simulés** par deux volumes) ; **restauration S3 non testée**, `https` non essayé |
| AR1.5 | ~~Cache du contrat vérifié~~ → **réutiliser le sérialiseur de version** : la mesure a montré que le contrat ne pèse que ≈ 1 % de la liste des actions | O1 sous 300 ms au p95 sur le banc AR0.3 | ✅ 2026-11-04 pour la sérialisation (17,2 → 2,9 ms pour 62 actions, en processus) ; **p95 HTTP non remesuré** |
| AR1.6 | **Index et requêtes** : revue des requêtes du moteur et des listes (`EXPLAIN`), garde-fou « pas de N+1 » déjà présent à généraliser | Tests de comptage de requêtes sur les 10 listes les plus utilisées | ✅ 2026-11-04 pour les listes de l'interface (6 N+1 corrigés, 7 tests de comptage) ; **requêtes du moteur et `EXPLAIN` non revus** ; listes d'administration non mesurées |

**Sortie :** M4 et M5 clos ; C3 atténué ; PostgreSQL moins sollicité en connexions.

### AR2 — Déploiements par rôle (M–L)
Même image, même dépôt, **plusieurs déploiements indépendants**.

| Tâche | Contenu | Critère de sortie | État |
|---|---|---|---|
| AR2.1 | **ADR 0022 « Rôles de déploiement »** : définition, privilèges, flags | ADR accepté | ✅ 2026-11-04 (accepté ; addendum de corrections 2026-11-04) |
| AR2.2 | **Sélecteur de rôle** : `ORCH_ROLES` (ou sous-commandes) pour chaque thread de fond du worker ; le comportement par défaut actuel reste inchangé (rétrocompatible) | `run_worker` sans option = comportement actuel (test) | ✅ 2026-11-04 (worker : `exec`/`aux`/`ai` ; API : `ui`/`ingest`/`runner-gw`) |
| AR2.3 | **Séparation des surfaces HTTP** : `api-ui` (authentifiée), `api-ingest` (webhooks, formulaires publics, listes dynamiques), `runner-gw` (jobs/request, heartbeat, complete), derrière le même reverse proxy par préfixe de chemin | Un blocage de `api-ingest` n'affecte pas `GET /actions/` (test de chaos) | ✅ 2026-11-04 (application + déploiement ; un défaut trouvé et corrigé sur la vraie topologie) |
| AR2.4 | **Séparation des workers** : `worker-exec` (actions), `worker-aux` (poller, alerts, lookups, git-sync, http-steps), `worker-ai` (ai-jobs, ai-evals) | Arrêt de `worker-ai` : 0 impact sur un workflow sans étape IA | ✅ 2026-11-04 (`worker-ai` non démarré) |
| AR2.5 | **Retrait des privilèges inutiles** : `docker.sock` uniquement pour `worker-exec` ; clé du coffre uniquement pour les rôles qui déchiffrent (`worker-exec`, `worker-aux` si connexions, `api-ui` pour l'écriture) — inventaire par rôle | Matrice rôle × privilège testée (le rôle démarre sans le privilège retiré, ou échoue explicitement) | ✅ 2026-11-05 (matrice testée ; I1 vrai en mode agents seulement ; I7 partiel) |
| AR2.6 | **Santé et arrêt propre par rôle** : endpoints/commandes de santé distincts, arrêt gracieux (fin de tâche, libération du bail), `stop_grace_period` | Redémarrage d'un rôle pendant la charge : 0 perte, 0 doublon | ✅ 2026-11-04 (worker tué par SIGTERM en pleine action : terminée, 0 rejeu ; 3 services `healthy` puis arrêtés en code 0 sur la pile de production ; reste : mesure sous charge) |
| AR2.7 | **Compose de production par rôles** (`docker-compose.prod.yml` avec profils ; `prod-smoke.sh` couvre les deux topologies : « compacte » et « par rôles ») | Installation vierge verte dans les deux | ✅ 2026-11-04 (compacte et par rôles : `prod-smoke.sh` OK ; `api-ingest` sans sortie réseau mesuré ; isolation mesurée) |

**Sortie :** C3 clos ; C1 et C2 réduits ; une panne ou une mise à jour d'IA, d'alertes ou d'interrogation n'arrête plus l'exécution.

### AR3 — Plan d'exécution isolé (L)
Le worker intégré devient un **runner comme les autres** (ADR 0013).

| Tâche | Contenu | Critère de sortie | État |
|---|---|---|---|
| AR3.1 | **ADR 0023 « Exécution par runners uniquement »** | ADR accepté | ✅ 2026-11-05 (objectif reformulé) |
| AR3.2 | **Agent d'exécution local** détenant `docker.sock`, sans accès base ni clé, branché sur `runner-gw` ; mode par défaut de la topologie par rôles | `worker-exec` n'a plus de variable de base de données ni de volume `keys` | ✅ 2026-11-05 |
| AR3.3 | **Interface d'exécuteur** (`Executor`) : Docker (actuel), pool préchauffé, Kubernetes Jobs ; sélection par étiquette d'action / de runner | Un runner Kubernetes passe la même suite de conformité que Docker | 🟡 2026-11-05 (deux exécuteurs, pas de sélection) |
| AR3.4 | **Pool de conteneurs préchauffés** pour les actions de confiance (rechargement de code par exécution, pas de réutilisation d'état entre locataires) | O3b ≥ ×1,8 sur le banc dédié, **ou** causes mesurées et documentées | ⬜ décision documentée |
| AR3.5 | **Suite de conformité des exécuteurs** : isolation réseau, utilisateur, FS lecture seule, limites, délais, annulation, taille de sortie | Même suite exécutée sur chaque exécuteur | 🟡 2026-11-05 |
| AR3.6 | **Profil renforcé** (gVisor/Kata) intégré à la sélection d'exécuteur | Essai sur hôte disposant du runtime — sinon « non exécuté » | ⬜ non exécutable ici |
| AR3.7 | **Mise à jour de l'agent** : version annoncée au `jobs/request`, plateforme qui refuse une version trop ancienne avec message explicite (comble « pas de mise à jour automatique » de l'ADR 0013) | Test de compatibilité agent N-1 | ✅ 2026-11-05 |

**Sortie :** C1 et C2 clos pour l'exécution ; l'hôte n'est exposé qu'à un agent minimal ; M1 traité ou chiffré.

### AR4 — Orchestrateur scalable (L)
Passer d'un balayage global à un traitement proportionnel aux événements.

| Tâche | Contenu | Critère de sortie | État |
|---|---|---|---|
| AR4.1 | **Preuve multi-instances** : 2 puis 4 orchestrateurs, charge de runs parallèles, vérification des invariants (une avance par run à la fois, aucune transition perdue) | Test déterministe + chaos (kill -9 d'une instance) | ✅ 2026-11-05 |
| AR4.2 | **Sortir les effets externes du tick** : livraison de callbacks et d'alertes dans un rôle distinct, déjà couvert par `worker-aux` | Un endpoint de callback lent ne retarde plus `advance` (mesure) | ✅ 2026-11-05 (orchestrateur sans clé ni réseau sortant, testé et mesuré en conteneur ; gain de latence du tick non mesuré) |
| AR4.3 | **Réveil par événement** : `LISTEN/NOTIFY` à la création de tâche, à la fin d'étape et à l'échéance ; le polling reste comme filet de sécurité (période allongée) | Latence moyenne d'avancement réduite ; charge SQL au repos réduite (mesures avant/après) | ⬜ décision documentée |
| AR4.4 | **Sélection ciblée des runs** : n'avancer que les runs « à traiter » (date de prochain réveil, drapeau de travail en attente) au lieu de tous les `running` | Coût du tick indépendant du nombre de runs en attente d'approbation ou de minuterie | ✅ 2026-11-05 (tour de l'orchestrateur : 15 s → 3 ms avec 400 runs en attente ; cause = fsync d'un verrou de ligne par run et par tour) |
| AR4.5 | **Partage de charge** : répartition par `hash(run_id) % N` ou file de runs prêts, avec reprise si une instance disparaît | Débit linéaire jusqu'à N instances sur le banc, ou plafond identifié | ✅ 2026-11-05 (minimal ; débit non mesuré) |
| AR4.6 | **Concurrence intra-worker** : plusieurs exécutions simultanées par process `worker-exec`/agent (limite configurable, pas de dépassement de bail) | Débit par agent augmenté sans doublon | 🟡 2026-11-05 réglage livré ; **gain ×1,23 seulement sur cette machine** (O3b non atteint) |

**Sortie :** M2 clos ; temps d'avancement indépendant du volume de runs dormants.

### AR5 — Données : haute disponibilité, historique, équité (L–XL)

| Tâche | Contenu | Critère de sortie | État |
|---|---|---|---|
| AR5.1 | **Haute disponibilité PostgreSQL** : documentation et compose de référence avec réplica et bascule (Patroni ou service managé), reconnexion tolérante dans tous les rôles | Basculement pendant une charge : reprise automatique, 0 perte/doublon (chaos) | ⬜ non exécutable ici |
| AR5.2 | **Lectures sur réplica** pour les listes lourdes de l'interface (tolérance de retard documentée) ; jamais pour le moteur | Routeur de lecture explicite, test de retard simulé | ⬜ décision documentée |
| AR5.3 | **Partitionnement et archivage** de `StepRun`, événements d'exécution et audit (par mois) ; `prune_data` adapté | Suppression par détachement de partition ; restauration testée | ⬜ décision documentée |
| AR5.4 | **Équité entre espaces** (existant : `ORCH_MAX_CONCURRENT_PER_WORKSPACE`, plafond de tâches simultanées par espace — à auditer avant de concevoir) : quotas de runs actifs et de tâches en file par espace, ordonnancement équitable (round-robin pondéré) dans `claim_next` et l'orchestrateur | Un espace saturant n'augmente pas de plus de X % la latence d'un autre (mesure) | 🟡 2026-11-05 (famine corrigée et testée ; quota de file et S13 non faits) |
| AR5.5 | **Sauvegarde continue** (archivage des journaux de transactions) en complément du dump planifié ; procédure de restauration à un instant donné | `restore-test.sh` étendu (RPO/RTO consignés) | ✅ 2026-11-05 (mécanisme prouvé sur conteneurs jetables ; purge, volumétrie, stockage distant non faits) |
| AR5.6 | **Politique de migration** : *expand/contract* obligatoire, migrations longues hors transaction, vérification CI de verrous (pas de `ALTER` bloquant sur table volumineuse) | Linter de migrations en CI | ✅ 2026-11-05 (analyse syntaxique ; limites dans TASKS) |

**Sortie :** M3 clos ; PostgreSQL n'est plus un point de défaillance silencieux ; RPO/RTO chiffrés.

### AR6 — Livraison et exploitation (L)

| Tâche | Contenu | Critère de sortie | État |
|---|---|---|---|
| AR6.1 | **Mise à jour progressive documentée et testée** : ordre (migrations *expand* → `runner-gw`/agents → workers → orchestrateurs → API → *contract*), drain des workers, test « mise à jour pendant une charge » | Script `scripts/rolling-upgrade-test.sh` : 0 perte, 0 erreur 5xx > seuil | ✅ 2026-11-05 (0 perte, 0 doublon, 0 erreur UI avec garde de santé ; **sans** garde : coupure de 9-10 s d'`api-ui`) |
| AR6.2 | **Versionnement des interfaces** : API `/api/v1`, protocole worker v1, protocole runner (en-tête de version), schéma d'événements ; politique de dépréciation | Document + tests de compatibilité | ✅ 2026-11-05 (opérations seulement) |
| AR6.3 | **Manifestes Kubernetes / chart Helm** pour la topologie par rôles ; sondes, budgets d'interruption, limites de ressources | Déploiement sur un cluster local de test (kind/k3d) — sinon « non exécuté » | 🟡 2026-11-05 (écrit, vérifié structurellement, jamais appliqué) |
| AR6.4 | **Mise à l'échelle automatique** des `worker-exec` sur la profondeur de file (métrique exposée par la plateforme) | Courbe de réaction mesurée | 🟡 2026-11-05 (signaux exposés ; pas d'autoscaler) |
| AR6.5 | **Observabilité** : traces distribuées (OpenTelemetry) du déclencheur au résultat d'étape, `run_id` corrélé dans les logs, métriques par rôle, tableaux de bord et alertes d'exploitation livrés | Une trace complète pour un run de 3 étapes | 🟡 2026-11-05 (corrélation et métriques par rôle ; pas de traces) |
| AR6.6 | **Chaîne de livraison** : images signées, SBOM par image (déjà présent globalement), version affichée par rôle, canal de publication (candidate → stable) | Vérification de signature documentée | 🟡 2026-11-05 (étiquettes et version exposées ; ni signature ni SBOM par image) |
| AR6.7 | **Documentation d'exploitation par topologie** : compacte (une machine), par rôles (compose), Kubernetes ; tailles de référence tirées du banc AR0.3 | `INSTALL.md` et `UPGRADE.md` mis à jour | 🟡 2026-11-05 (sans tailles de référence) |

**Sortie :** M6 clos ; une mise à jour de composant est une procédure rejouée, pas une promesse.

### AR7 — Extractions facultatives — **clôturé par l'ADR 0024 (2026-11-05) : aucune extraction, critères de réouverture écrits**
À n'engager que si un critère chiffré l'exige.

| Extraction | Déclencheur | Condition | État |
|---|---|---|---|
| **Passerelle IA** (`ai/` ≈ 7 000 lignes) en service autonome, API interne authentifiée | Cycle de publication de l'IA nettement plus rapide que le noyau, ou exigence de réseau sortant distinct | Contrat d'API interne versionné ; secrets des fournisseurs détenus par elle seule | ⬜ |
| **Courtier de secrets** (prolonge l'ADR 0019) : seul détenteur de la clé, délivre des valeurs déchiffrées par tâche | Exigence de séparation forte clé/plateforme | Latence ajoutée mesurée ; mode dégradé défini | ⬜ |
| **Service de déclencheurs** (interrogation, planification) | Volume d'interrogations perturbant le noyau, malgré `worker-aux` | Écrit dans la base du noyau via l'API interne uniquement (pas d'accès direct aux tables) | ⬜ |

**Ne seront pas extraits :** `tenancy`, `catalog`, `workflows`, `runs/engine`, `triggers` (noyau transactionnel).

---

## 5. Cibles de topologie

| Topologie | Public | Rôles | Remarques |
|---|---|---|---|
| **Compacte** (actuelle) | Évaluation, petite équipe, une machine | api, orchestrator, worker, web, db | Reste supportée et testée ; comportement par défaut inchangé |
| **Par rôles** (AR2–AR3) | Production sur 1–3 hôtes | api-ui, api-ingest, runner-gw, orchestrator ×N, worker-exec/agent ×N, worker-aux, worker-ai, web, pgbouncer, db | Compose avec profils |
| **Haute disponibilité** (AR5–AR6) | Production exigeante | idem + réplica PostgreSQL, stockage objet, mise à l'échelle automatique | Kubernetes ou compose multi-hôtes |

## 6. Indicateurs de réussite (fixés avant mesure, à confirmer en AR0.6)

| Indicateur | Cible proposée | Jalon |
|---|---|---|
| Perte ou doublon d'exécution sous panne de n'importe quel rôle | 0 | AR0.4, AR2.6 |
| Interface disponible pendant une rafale de webhooks et 100 runners en long-poll | p95 `GET /actions/` < 300 ms | AR1.3, AR2.3 |
| Panne de `worker-ai`, `worker-aux` ou `api-ingest` | Aucun impact sur l'exécution des workflows sans l'étape concernée | AR2 |
| Mise à jour progressive pendant charge | 0 perte, 0 doublon, erreurs 5xx sous seuil | AR6.1 |
| Passage à l'échelle des exécutions (1→4 agents) | ≥ ×1,8 sur hôte dédié | AR3.4, AR4.6 |
| Débit moteur seul avec N orchestrateurs | Linéaire jusqu'à un plafond identifié | AR4.5 |
| Bascule PostgreSQL | Reprise automatique, RPO/RTO consignés | AR5.1, AR5.5 |
| Privilèges par rôle | Aucun rôle ne détient à la fois `docker.sock` et la clé du coffre | AR3.2 |

## 7. Risques et parades

| Risque | Parade |
|---|---|
| Régression de la garantie « zéro perte/doublon » en modifiant l'orchestrateur | AR0.4 avant tout changement ; invariants testés à chaque jalon |
| Complexité d'exploitation accrue | Topologie compacte maintenue par défaut ; documentation par topologie (AR6.7) |
| PgBouncer en mode transaction incompatible avec des usages de session | Inventaire et test en AR1.2 avant adoption |
| `LISTEN/NOTIFY` perdu lors d'une reconnexion | Polling conservé comme filet de sécurité (AR4.3) |
| Pool préchauffé = fuite d'état entre locataires | Pas de réutilisation d'état ; suite de conformité AR3.5 ; réservé aux actions de confiance |
| Dépendances très récentes (Django 6.1, TypeScript 7, Vite 8) | Épinglage conservé ; compatibilité N/N-1 testée en AR0.5 |
| Dérive entre topologies | `prod-smoke.sh` exécuté sur chacune en CI |
| Mesures faussées par un hôte saturé (précédent O3b) | Banc dédié obligatoire (AR0.3) ; conditions de mesure consignées avant exécution |

## 8. Hors périmètre (décisions explicites)
- Broker de messages externe (AMQP/Kafka) : l'ADR 0003 tient tant que le plafond reste l'exécution des actions ; à rouvrir si AR4 montre PostgreSQL saturé *par la file elle-même*.
- Base de données par service, maillage de services (*service mesh*).
- Découpage par module Django.
- Multi-région active/active.

## 9. Gouvernance de la feuille de route
- **ADR à écrire** : 0022 (rôles de déploiement), 0023 (exécution par runners uniquement), 0024 (limites de débit et cache partagés), 0025 (haute disponibilité PostgreSQL et partitionnement), 0026 (versionnement des interfaces et politique de mise à jour).
- Chaque tâche reçoit un identifiant dans `docs/TASKS.md` (préfixe `AR`) ; l'état de ce document est mis à jour à la clôture d'un jalon, avec renvoi à l'entrée de `SESSION_LOG.md` qui contient la preuve.
- Un jalon n'est « fait » que si son critère de sortie a été **exécuté** ; sinon « non exécuté » avec la raison (hôte dédié absent, cluster absent, etc.).
- Revue de cette feuille de route à la clôture de AR2 et de AR4 : les extractions AR7 ne sont décidées qu'à ce moment, sur les mesures obtenues.
