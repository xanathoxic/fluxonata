# Déploiement Kubernetes — plan de contrôle (AR6.3)

**Statut : écrit et vérifié STRUCTURELLEMENT seulement** (`kubectl kustomize` + `backend/tests/test_k8s_manifests.py`). **Aucun cluster n'était disponible : rien n'a été appliqué, aucun pod démarré,
aucune NetworkPolicy exercée.** Traitez ces fichiers comme un point de départ à valider sur votre cluster, pas comme une installation éprouvée.

## Ce qui est couvert
`api-ui`, `api-ingest` (sans sortie réseau), `runner-gw`, `orchestrator` (sans clé ni sortie réseau), `worker-aux`, `pgbouncer`, Ingress par préfixe, PDB, HPA CPU des API, NetworkPolicy,
Job de migration. Même répartition des privilèges que `docs/ARCHITECTURE.md` § 2 bis.

## Ce qui NE l'est PAS (volontairement)
- **Le plan d'exécution des actions** (`worker-exec`) : il exige le socket Docker, impossible et indésirable dans un pod. Sur Kubernetes, les actions s'exécutent par des **runners distants** (`runner/agent.py`, protocole HTTP initié par le runner, `docs/RUNNERS.md`) installés sur des hôtes Docker, qui se connectent à `runner-gw`. Un exécuteur natif Kubernetes (Jobs) n'existe pas (AR3.3, non fait).
- **PostgreSQL** (service managé ou opérateur) et sa haute disponibilité (AR5.1) ; l'interface statique (image `fluxonata-web`) ; l'autoscaling sur la profondeur de file (AR6.4) ; TLS ; observabilité.

## Mise en route (à valider)
1. Créer le namespace, les Secrets (`secrets.example.yaml` : AUCUNE valeur réelle dans Git), le Secret `fluxonata-db-url` (clés `url` pour le pooler, `host` pour le Job).
2. `kubectl apply -f base/migrate-job.yaml` (image à jour) et attendre `Complete`.
3. `kubectl apply -k infra/k8s`.
4. Créer un runner (interface : Administration → Runners), installer l'agent sur un hôte Docker avec son jeton.
**Mise à jour** : migrations d'abord (Job), puis `kubectl set image` rôle par rôle dans l'ordre de `docs/VERSIONING.md` § 3 ; les Deployments d'API ont `maxUnavailable: 0` et un PDB.
