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
3. Sur la machine (Docker requis) :
   ```bash
   ORCH_URL=https://fluxonata.exemple.org ORCH_RUNNER_TOKEN=orr_… python3 runner/agent.py      # directement
   scripts/runner.sh orr_…                                                                       # depuis le dépôt (pile locale)
   ```
   En conteneur : `docker build -f runner/Dockerfile -t fluxonata-runner .`, puis
   ```bash
   sudo mkdir -p /var/lib/fluxonata-runner && sudo chown 10001 /var/lib/fluxonata-runner
   docker run -d --name fluxonata-runner --restart unless-stopped --group-add "$(stat -c %g /var/run/docker.sock)" \
     -e ORCH_URL=https://fluxonata.exemple.org -e ORCH_RUNNER_TOKEN=orr_… \
     -e ORCH_WORK_DIR=/var/lib/fluxonata-runner -v /var/lib/fluxonata-runner:/var/lib/fluxonata-runner \
     -v /var/run/docker.sock:/var/run/docker.sock fluxonata-runner
   ```
   (le répertoire de travail doit avoir le **même chemin** dans le conteneur et sur l'hôte : le démon Docker de l'hôte le monte
   dans les conteneurs d'actions.)
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
- **Si un runner est compromis**, l'attaquant obtient le code et les secrets des tâches qu'il reçoit — **pas** ceux des autres espaces ni du coffre-fort. D'où les runners **dédiés** pour les espaces sensibles.
- **Limites à connaître** : l'agent doit pouvoir lancer des conteneurs (socket Docker) : un accès au socket équivaut à root sur l'hôte, **dédiez la machine** (ou utilisez Docker rootless) ; les conteneurs d'actions partagent le noyau de l'hôte (voir « Durcissement et limites » dans [INSTALL.md](INSTALL.md), `ORCH_HARDENED`, gVisor) ; **utilisez HTTPS** : en HTTP le jeton et les secrets des tâches circulent en clair (l'agent avertit hors boucle locale).

## Exploitation
- **Mise à l'échelle** : ajoutez des runners (ou augmentez le parallélisme) ; la plate-forme garde son équité entre espaces et son plafond par espace.
- **Long-poll** : une requête d'agent attend jusqu'à 20 s (`ORCH_RUNNER_LONG_POLL_SECONDS`) : servez l'API avec des workers **à threads** (ex. `gunicorn --worker-class gthread --threads 16`) pour que des requêtes en attente n'occupent pas tous les processus.
- **Hors ligne** : un runner sans contact depuis plus de 60 s (`ORCH_RUNNER_ONLINE_SECONDS`) s'affiche « Hors ligne » ; ses tâches en cours sont reprises à l'expiration du bail.
- **Diagnostic** : journal de l'agent (`2` = jeton refusé, `3` = plate-forme injoignable), écran Runners (état, dernier contact, adresse IP), Santé de l'espace, audit.

## Ce qui n'est pas (encore) fait
Pas de mise à jour automatique de l'agent ; pas de quotas par runner ni de planification par priorité de runner ; pas d'enregistrement par jeton d'inscription réutilisable (chaque runner est créé dans l'interface, son jeton lui est propre) ; pas d'exécuteur Kubernetes ; les tests couvrent l'agent avec l'exécuteur sans isolation, Docker étant couvert par les tests du bac à sable et par un essai de bout en bout (agent en conteneur, tâche exécutée dans un conteneur d'action).
