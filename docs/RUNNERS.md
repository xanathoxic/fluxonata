# Runners : exécuter les actions hors de la plate-forme

Un **runner** est un agent installé sur une machine (la vôtre, un serveur, un poste dédié…) qui exécute des actions pour la
plate-forme, comme un runner GitLab. Il peut être **partagé** (il sert tous les espaces qui l'acceptent) ou **dédié** à un
ou plusieurs espaces (il n'exécute que leurs actions). Les workers intégrés à la plate-forme continuent d'exister : ce sont
des runners « de confiance » qui forment le pool partagé par défaut.

## Fonctionnement
```
  Runner (machine quelconque)                         Plate-forme
  ───────────────────────────                         ───────────
  POST /api/v1/runner/jobs/request/   ── (HTTPS) ──▶  réserve la prochaine tâche autorisée (bail + n° de tentative)
                                      ◀────────────   spec : code de l'action, entrées, secrets de CETTE tâche
  exécute dans un conteneur jetable durci
  POST …/jobs/<id>/heartbeat/         ── toutes les ~10 s ▶  prolonge le bail ; répond « annuler ? »
  POST …/jobs/<id>/complete/          ──────────────▶  masque les secrets, valide le contrat de sortie, extrait les
                                                       artefacts, enregistre (refusé si la tentative n'est plus la bonne)
```
- **Le runner appelle, la plate-forme n'appelle jamais le runner** : aucun port à ouvrir sur la machine du runner, il traverse
  les pare-feu et le NAT comme n'importe quel client HTTPS.
- **Aucun accès à la base ni au coffre-fort.** Les secrets d'une tâche sont déchiffrés **côté plate-forme**, remis au seul
  runner qui a réservé la tâche, via HTTPS, et jamais conservés par le runner.
- **La plate-forme ne fait pas confiance au résultat** : elle masque les secrets, valide la sortie contre le contrat et
  limite les tailles (journaux 64 Ko, résultat 1 Mo) — exactement comme pour un worker intégré (`runs/finalize.py`).
- **Reprise sur panne** : si le runner disparaît, le bail expire, la tâche est remise en file (ou marquée « issue inconnue »
  pour une action à effet externe non idempotente) et la réponse tardive de l'ancienne tentative est ignorée (jeton de barrière).

## Créer et démarrer un runner
1. **Plate-forme → Runners → Nouveau runner** (administrateur de plate-forme), ou **Administration → Runners** (administrateur d'un
   espace : un runner **dédié à cet espace**). Choisissez nom, portée, étiquettes, langages, parallélisme.
2. Copiez le **jeton** (`orr_…`) : il n'est affiché **qu'une fois** ; seul son hachage SHA-256 est conservé.
3. Sur la machine (Docker requis), **de préférence une machine dédiée** (voir « Trois niveaux d'isolation ») :
   ```bash
   ORCH_URL=https://runners.exemple.org:8443 ORCH_RUNNER_TOKEN_FILE=/etc/fluxonata-agent/runner_token python3 runner/agent.py   # directement
   scripts/runner.sh orr_…                                                                                           # depuis le dépôt (pile locale, développement)
   ```
   Le jeton se lit dans un **fichier protégé** (`ORCH_RUNNER_TOKEN_FILE`, droits 0400) : ne le placez ni dans une ligne de commande, ni dans `docker run -e` (visibles par `ps` et `docker inspect`).
   En conteneur, utilisez le kit autonome **`infra/agent/`** (Compose, secret de fichier, images épinglées, sonde de santé) décrit plus bas.
4. L'écran Runners affiche « En ligne », la version de l'agent, sa plate-forme et son dernier contact.

## Portées : partagé, dédié, et espace « exclusif »
| | Qui l'exécute |
|---|---|
| Runner **partagé** | les espaces dont « runners partagés » est autorisé (par défaut : tous) |
| Runner **dédié** | uniquement les espaces auxquels il est dédié |
| Workers **intégrés** | comme un runner partagé (étiquettes : `ORCH_WORKER_TAGS`) |
| **Étapes IA** | toujours sur la plate-forme (elles utilisent les secrets du fournisseur d'IA) : jamais sur un runner |

Pour **réserver** l'exécution d'un espace à vos runners (isolation, conformité, matériel), un administrateur de l'espace décoche
**« Autoriser les runners partagés… »** (Administration → Runners). Ses actions ne partent alors plus que sur ses runners
dédiés ; **sans runner dédié en ligne, elles attendent** — la Santé de l'espace l'affiche en erreur.
Un runner dédié à **plusieurs** espaces n'est modifiable que par la plate-forme (les administrateurs d'espace le voient en lecture seule).

## Étiquettes et langages
- Un runner déclare des **étiquettes** (`linux`, `gpu`…) et les **langages** qu'il peut exécuter (python, node, typescript, shell, powershell).
- Une version d'action peut exiger des étiquettes (`required_tags`, à la création de la version) : elle ne part que sur un runner qui les porte toutes.
- Un runner ne reçoit jamais plus de tâches que son parallélisme ; il peut être **mis en pause** (plus aucune nouvelle tâche, la tâche en cours se termine).

## Sécurité
- Jeton **par runner**, hachage SHA-256, régénérable (l'ancien cesse de fonctionner aussitôt) ; supprimer un runner révoque son accès immédiatement.
- Un jeton de runner n'ouvre **rien d'autre** que le protocole du runner (ni l'API des espaces, ni l'administration) ; une session de personne n'ouvre pas non plus le protocole.
- Un runner ne voit **que ses tâches** (« inconnue » et « celle d'un autre » reçoivent la même réponse 404) et uniquement les secrets de la tâche qu'il exécute.
- Limite de débit **par runner** (`ORCH_RUNNER_RATE`, 1500/min par défaut) ; tailles bornées ; messages d'erreur tronqués.
- Tout est **audité** (`runner.create|update|rotate_token|delete`, `workspace.runner_settings`, `secret.inject`).
- **Si un runner est compromis**, l'attaquant obtient le code et les secrets des tâches qu'il reçoit — **pas** ceux des autres espaces ni du coffre-fort (ils ne lui sont jamais transmis). D'où les runners **dédiés** pour les espaces sensibles. **Cela suppose que son démon de conteneurs ne soit pas celui du plan de contrôle** : voir ci-dessous.
- **Limites à connaître** : l'agent doit pouvoir lancer des conteneurs (socket Docker) : un accès au socket équivaut à root sur l'hôte, **dédiez la machine** (ou utilisez Docker rootless — « Trois niveaux d'isolation ») ; les conteneurs d'actions partagent le noyau de l'hôte (voir « Durcissement et limites » dans [INSTALL.md](INSTALL.md), `ORCH_HARDENED`, gVisor) ; **utilisez HTTPS** : en HTTP le jeton et les secrets des tâches circulent en clair (l'agent avertit hors boucle locale).

## Trois niveaux d'isolation
Retirer d'un conteneur les variables de base, la clé du coffre et les volumes **ne suffit pas** tant que ce conteneur peut commander le démon Docker qui héberge le plan de contrôle : un accès en écriture à `/var/run/docker.sock` permet de démarrer un conteneur qui monte les volumes de la plate-forme (clés, artefacts, base) ou le système de fichiers de l'hôte. L'invariant **I8** (`docs/ARCHITECTURE.md`) — *la compromission de l'agent ou de son démon n'atteint ni la base, ni les clés, ni les volumes, ni les conteneurs du plan de contrôle* — n'est donc vrai que si le runtime de l'agent est réellement séparé.

| Niveau | Mise en place | I8 | Quand |
|---|---|---|---|
| **1. Démonstration / confiance forte** | agent sur le **même démon Docker** que le plan de contrôle (`infra/docker-compose.agent.yml`, profil `agent`) ; option **explicite** `ORCH_ACCEPT_SHARED_DOCKER_AGENT=oui` exigée | **faux** | essais, démonstration, exploitant et clients tous de confiance. **Pas** pour un multi-tenant sensible |
| **2. Production isolée** | plan de contrôle **sans aucun socket d'exécution** (`infra/docker-compose.remote-agents.yml`) + agent sur une **machine, une VM ou un démon rootless distincts** (`infra/agent/`), qui n'appelle que `runner-edge` en HTTPS | **vrai** si les conditions ci-dessous sont réunies | mode recommandé en multi-tenant |
| **3. Client sensible** | comme le niveau 2, avec un agent (ou un groupe d'agents) **dédié au client** : runner « dédié » à ses espaces, espace sans runners partagés, réseau et stockage séparés | vrai | données ou exigences d'un client particulier |

**Conditions du niveau 2 sur une seule machine** (démon rootless) : socket distinct, stockage distinct, utilisateur système distinct, aucune visibilité sur les conteneurs du plan de contrôle, aucun accès à ses volumes ni à ses répertoires de clés, de base ou d'artefacts, et **aucun partage du `/var/run/docker.sock` du plan de contrôle**. Un simple conteneur montant le socket principal n'est **pas** une isolation. Une machine ou une VM distincte reste préférable : un noyau partagé (conteneurs d'action, démon rootless) est une frontière plus faible qu'un hyperviseur.

**Ce que l'isolation ne supprime pas** : un agent compromis voit les secrets **des tâches qu'il reçoit** (code, entrées, variables injectées) ; il ne voit ni le coffre complet, ni les autres espaces, ni la base. Les runners dédiés limitent l'exposition aux espaces qui lui sont confiés.

## Installer un agent sur une machine distincte (niveau 2)
Rien du plan de contrôle ne doit être copié sur la machine de l'agent : ni `.env.prod`, ni identifiants PostgreSQL, ni clé du coffre, ni volumes. L'agent reçoit **seulement** l'adresse HTTPS de `runner-edge`, son jeton (fichier), éventuellement une autorité de certification, son répertoire de travail, sa concurrence, les images d'exécution autorisées et son socket Docker local.

**Côté plan de contrôle** (une fois) :
```bash
ORCH_RUNNER_TLS_DIR=/etc/fluxonata/runner-tls docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.roles.yml \
    -f infra/docker-compose.remote-agents.yml --env-file infra/.env.prod up -d --build
```
`/etc/fluxonata/runner-tls` contient `fullchain.pem` et `privkey.pem` (TLS obligatoire : le front n'écoute pas en clair). `runner-edge` ne sert que `/api/v1/runner/` (tout le reste : 404), borne les corps (4 Mo), limite le débit par adresse, n'écrit jamais l'en-tête `Authorization`, un paramètre de requête ni un corps dans ses journaux, et ne voit que `runner-gw` (réseau interne dédié : ni `api-ui`, ni `api-ingest`, ni PostgreSQL). Dans cette topologie aucun service n'a de socket Docker.

**Créer le runner** : Plate-forme → Runners → *Nouveau runner* (partagé ou dédié, étiquettes, langages, parallélisme). Le jeton `orr_…` n'est affiché qu'une fois.

**Sur la machine de l'agent** :
```bash
git clone … && cd fluxonata/infra/agent               # ou copiez seulement infra/agent/, runner/ et workers/
cp agent.env.example agent.env                         # ORCH_URL, images ÉPINGLÉES (scripts/pin-images.sh), socket, répertoire de travail
printf %s "orr_…" | ./agent-ctl.sh init                # jeton → secrets/runner_token (0400), lu sur l'entrée standard, jamais affiché
./agent-ctl.sh up
./agent-ctl.sh health                                  # plate-forme joignable, jeton accepté, démon Docker
```
L'agent **refuse de démarrer** si une image d'exécution n'est pas référencée par digest (`name@sha256:…`, `ORCH_REQUIRE_IMAGE_DIGEST=1` par défaut dans le kit ; code de sortie 4) et ne reçoit aucune option Docker arbitraire : les conteneurs d'action sont non privilégiés, sans capacité, sans réseau (ou le réseau de test), système de fichiers en lecture seule.

| Opération | Commande |
|---|---|
| **Rotation du jeton** | rotation dans l'interface (l'ancien jeton cesse **aussitôt** d'attribuer des tâches), puis `printf %s "orr_…" \| ./agent-ctl.sh rotate` |
| **Mise à jour** | `./agent-ctl.sh update` (reconstruit l'image, recrée l'agent ; la plate-forme peut exiger une version minimale : `ORCH_MIN_AGENT_VERSION`, l'agent trop ancien s'arrête avec le code 3) |
| **Révocation** | supprimer le runner dans l'interface (effet immédiat) ; `./agent-ctl.sh revoke` retire le jeton de la machine |
| **Santé** | `./agent-ctl.sh health` ; sonde Compose `agent.py --healthcheck` ; écran Runners |
| **Nettoyage** | `./agent-ctl.sh clean` (conteneurs d'action étiquetés `fluxonata.sandbox`, répertoires `orch-run-*` et `orch-env-*`) ; au démarrage, l'agent supprime lui-même les restes d'un arrêt brutal (conteneurs de **son** étiquette, répertoires de travail de plus d'une heure) |

### Docker rootless (même machine ou machine dédiée)
Un démon rootless d'un **compte système dédié** a son propre socket (`/run/user/<uid>/docker.sock`), son propre stockage et ne voit rien du démon principal. Compte dédié, `loginctl enable-linger`, `dockerd-rootless-setuptool.sh install`, puis soit `infra/agent/fluxonata-agent.service` (agent sans conteneur, jeton transmis par `LoadCredential`), soit le kit Compose avec `AGENT_DOCKER_SOCKET=/run/user/<uid>/docker.sock` et `AGENT_USER=<uid>:<gid>`. Particularités d'un démon rootless : pas de port privilégié, limites mémoire/CPU/processus dépendantes de la délégation cgroup v2 de l'hôte. **Cette procédure n'a pas été exécutée ici** (Docker rootless non installé sur la machine de développement) : seuls ses fichiers de configuration sont vérifiés par les tests.

## Différences entre un worker intégré et un agent
Le contrat de l'étape **Script Python** ne change pas : mêmes entrées JSON (dictionnaire, liste, `null`), mêmes sorties structurées, `print` et `stderr` reportés au journal (le résultat est l'enveloppe, jamais la sortie standard), exception → erreur `ACTION_ERROR` avec trace, délai → `TIMEOUT`, annulation → `CANCELLED`, secrets injectés en variables d'environnement, dépendances = **celles de l'image** (pas de `pip install` à l'exécution : un module absent donne une `ModuleNotFoundError` dans le journal). Différences autorisées : latence de prise de tâche (long-poll, heartbeat) ; image fournie par la configuration **de l'agent** (`ORCH_PYTHON_IMAGE`) — gardez la même sur worker et agent ; **artefacts** : ils voyagent dans le résultat JSON (au plus 10 fichiers, résultat limité à 1 Mo avant stockage), donc les mêmes limites sur les deux ; **entrées** : JSON uniquement, il n'y a pas d'artefact d'entrée pour un script. Un transfert d'artefacts hors résultat n'est pas implémenté : aucun besoin mesuré.

## Exploitation
- **Mise à l'échelle** : ajoutez des runners (ou augmentez le parallélisme) ; la plate-forme garde son équité entre espaces et son plafond par espace.
- **Long-poll** : une requête d'agent attend jusqu'à 20 s (`ORCH_RUNNER_LONG_POLL_SECONDS`) : servez l'API avec des workers **à threads** (ex. `gunicorn --worker-class gthread --threads 16`) pour que des requêtes en attente n'occupent pas tous les processus.
- **Hors ligne** : un runner sans contact depuis plus de 60 s (`ORCH_RUNNER_ONLINE_SECONDS`) s'affiche « Hors ligne » ; ses tâches en cours sont reprises à l'expiration du bail.
- **Diagnostic** : journal de l'agent (`2` = jeton refusé, `3` = plate-forme injoignable), écran Runners (état, dernier contact, adresse IP), Santé de l'espace, audit.

## Ce qui n'est pas (encore) fait
Pas de mise à jour automatique de l'agent ; pas de quotas par runner ni de planification par priorité de runner ; pas d'enregistrement par jeton d'inscription réutilisable (chaque runner est créé dans l'interface, son jeton lui est propre) ; pas d'exécuteur Kubernetes ; les tests couvrent l'agent avec l'exécuteur sans isolation, Docker étant couvert par les tests du bac à sable et par un essai de bout en bout (agent en conteneur, tâche exécutée dans un conteneur d'action).
