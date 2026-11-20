# Déploiement en cluster actif/actif

Ce document décrit comment faire tourner **plusieurs instances de l'application en parallèle derrière un répartiteur de charge**, ce qui est prévu par la conception, ce qui est à régler, et ce qui **n'a pas été vérifié**.
Il complète [DIMENSIONNEMENT.md](DIMENSIONNEMENT.md) (quoi multiplier, quels plafonds) et [VERSIONING.md](VERSIONING.md) (mise à jour sans coupure).

Légende : **Vérifié** (essai réel) · **Déduit** (lecture de la configuration livrée) · **Non vérifié** (jamais exécuté).

## 1. Ce qui est actif/actif, et ce qui ne l'est pas

| Composant | Actif/actif ? | Condition |
|---|---|---|
| API (`api`, `api-ui`, `api-ingest`, `runner-gw`) | oui, sans affinité de session | `ORCH_CACHE=db`, `ORCH_NUM_PROXIES` réglé (§3) |
| Orchestrateur | oui | état en base, verrou de ligne, bail et jeton de clôture ; **vérifié** à 2 et 4 instances ([DIMENSIONNEMENT.md](DIMENSIONNEMENT.md) §2.2) |
| Workers | oui | chacun a besoin du socket Docker de **son** hôte |
| Interface statique (nginx `web`) | oui | image identique, sans état |
| Artefacts | oui en mode `s3` seulement | en mode `fs`, un volume local ne se partage pas entre hôtes |
| **PostgreSQL** | **non** | point unique : ni réplica ni bascule dans le dépôt ([DIMENSIONNEMENT.md](DIMENSIONNEMENT.md) §2.5) |
| Répartiteur de charge | à fournir | non livré (hors nginx de la pile) |

Un cluster actif/actif de l'application seule ne supprime donc pas le point unique de la base : pour la haute disponibilité complète, la base doit elle-même être managée ou répliquée (hors du dépôt, non testé).

## 2. Ce qui rend les instances interchangeables (déduit de la conception)

- **Sessions en base** : une requête peut tomber sur n'importe quelle instance. Le jeton CSRF vit dans un cookie, sans état serveur.
- **Limites de débit strictes** (connexion, webhooks, formulaires publics, IA, runners) dans un cache partagé : `ORCH_CACHE=db` (valeur des compose de production et par rôles). **Vérifié** : avec `locmem` sur 2 processus, 18 requêtes passent là où la limite en autorise 10 ; avec `db`, 10.
- **Limite générale par utilisateur** : locale à chaque processus (`ORCH_USER_THROTTLE_CACHE=local`), donc multipliée par le nombre de processus. C'est un garde-fou, pas une protection d'authentification ; `ORCH_USER_THROTTLE_CACHE=default` la rend exacte au prix de requêtes SQL supplémentaires.
- **Planificateur, déclencheurs, interrogations** : les tâches périodiques prennent leurs lignes avec `SELECT … FOR UPDATE SKIP LOCKED` : deux instances ne traitent pas la même ligne.
- **Santé** : `/api/v1/health/live` (le processus répond) et `/api/v1/health/ready` (la base répond) sont faits pour les sondes du répartiteur : retirer une instance dont `ready` échoue.
- **Arrêt propre** : les services de la pile par rôles ont 30 s de grâce à l'arrêt.

## 3. Réglages obligatoires pour un cluster

1. **`ORCH_CACHE=db`** (ou `redis://…`, non exécuté) : jamais `locmem` avec plus d'un processus.
2. **`ORCH_NUM_PROXIES`** : nombre de proxys de confiance entre le client et l'API (nginx livré seul : `1` ; répartiteur devant nginx : `2`).
   Sans lui, l'en-tête `X-Forwarded-For` envoyé par le client est pris tel quel : un client peut changer d'adresse à chaque requête et **contourner les limites de débit par adresse** (connexion comprise). Valeur invalide : le démarrage échoue. Détail : [INSTALL.md](INSTALL.md).
   **Mesuré** (`scripts/cluster-test.sh`, 2 instances derrière nginx) : sans réglage, 14 connexions ratées avec un `X-Forwarded-For` différent à chaque essai donnent 14 réponses 401 (aucune limite) ; avec `ORCH_NUM_PROXIES=1`, le 11e essai est refusé (429), comme la limite de 10 par minute le prévoit.
   Pour vérifier votre propre installation, refaites cet essai : la limite doit continuer de s'appliquer à la même adresse réelle.
3. **Le répartiteur doit envoyer** `Host`, `X-Forwarded-For` (en **ajoutant** l'adresse du client, sans la remplacer par une valeur fournie par le client) et `X-Forwarded-Proto: https`, avec `ORCH_BEHIND_TLS_PROXY=1` et `ORCH_TRUSTED_ORIGINS` renseigné.
4. **`ORCH_ARTIFACTS_STORE=s3`** dès que l'API ou les workers sont sur plusieurs hôtes.
5. **Une seule base** : tous les hôtes pointent `ORCH_DB_HOST` vers le même PostgreSQL (ou son point d'entrée stable) ; vérifier la limite de connexions ([DIMENSIONNEMENT.md](DIMENSIONNEMENT.md) §4).
6. **Clés** : les instances d'un même cluster partagent les mêmes clés de chiffrement et de signature (`ORCH_KEY_DIR`) ; des clés différentes rendraient les secrets du coffre illisibles d'une instance à l'autre. À distribuer par votre outil de secrets, jamais par Git.
7. Même **version** de l'application sur toutes les instances, sauf pendant une mise à jour progressive ([VERSIONING.md](VERSIONING.md)).

## 4. Topologies

**A. Un hôte, plusieurs réplicas** : pile par rôles (`infra/docker-compose.roles.yml`), `docker compose … up -d --scale api-ui=N --scale api-ingest=N`. Le nginx de la pile résout les noms de service à la demande (résolveur Docker) ; la répartition entre réplicas repose sur le DNS de Docker. **Déduit, non vérifié ici.** Ce nginx reste unique : il n'apporte pas de haute disponibilité.

**B. Plusieurs hôtes derrière un répartiteur** : sur chaque hôte, la pile (nginx, API, orchestrateur, workers) ; devant, un répartiteur TLS (nginx, HAProxy, équilibreur de votre fournisseur) qui répartit vers les nginx des hôtes. Base partagée, artefacts en `s3`, `ORCH_NUM_PROXIES=2`. **Non vérifié.**

**C. Kubernetes** : `infra/k8s/` (voir [DIMENSIONNEMENT.md](DIMENSIONNEMENT.md) §2.6), l'Ingress est le répartiteur. **Jamais appliqué sur un cluster.**

**Vérifié sur éléments jetables** (`scripts/cluster-test.sh`) : deux instances de l'API (image construite depuis le commit), un répartiteur nginx (tourniquet), PostgreSQL et cache partagé en base, sur un seul hôte. C'est la forme du schéma B, sans second hôte, sans nginx de pile, sans PgBouncer, sans TLS.

Exemple minimal de répartiteur nginx pour B (à adapter ; **non exécuté tel quel**) :

```nginx
upstream fluxonata { server hote1:8080 max_fails=2 fail_timeout=10s; server hote2:8080 max_fails=2 fail_timeout=10s; }
server {
    listen 443 ssl;
    location / {
        proxy_pass http://fluxonata;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;   # attentes longues des runners (/api/v1/runner/)
    }
}
```

## 5. Vérifications à faire après la mise en place

`scripts/cluster-test.sh` rejoue les points ci-dessous sur des éléments jetables (≈ 3 à 6 min, Docker requis, refuse une machine saturée) ; il ne teste pas votre installation.

- Deux requêtes successives de la même session aboutissent sur deux instances différentes sans déconnexion (journaux d'accès).
- La limite de connexion (10 par minute par défaut) s'applique à l'adresse réelle, y compris avec un `X-Forwarded-For` falsifié.
- Arrêter une instance (`docker stop`) : le répartiteur n'envoie plus de trafic après l'échec des sondes, aucune exécution n'est perdue (les baux expirent et un autre orchestrateur reprend).
- `/api/v1/metrics/` : `fluxonata_service_instances{kind}` reflète le nombre d'instances attendu.

## 6. Résultats et limites

**Mesuré le 2026-11-20** (`scripts/cluster-test.sh`, un seul hôte, 2 instances, 2 processus gunicorn chacune) :

| Vérification | Résultat |
|---|---|
| Répartition (20 requêtes de santé) | 10 sur l'instance A, 10 sur l'instance B |
| Session ouverte via le répartiteur | valable sur les deux : 20 lectures, 10 et 10, aucun échec |
| Limite de connexion partagée | le 11e essai est refusé alors que les essais alternent entre les deux instances |
| Limite sans `ORCH_NUM_PROXIES` | contournée par un `X-Forwarded-For` falsifié (14 réponses 401 sur 14) |
| Limite avec `ORCH_NUM_PROXIES=1` | appliquée malgré l'en-tête falsifié (refus dès le 11e essai) |
| Instance A arrêtée | 20 requêtes sur 20 réussies par l'instance B, session conservée |
| Instance A redémarrée | elle reçoit de nouveau du trafic (9 requêtes sur 20) |

**Ce que cet essai ne démontre pas**
1. Deux **hôtes** distincts, un répartiteur à deux étages (TLS puis nginx de la pile), `ORCH_NUM_PROXIES=2`.
2. La répartition par le DNS de Docker avec `--scale` (l'essai nomme les deux instances dans le répartiteur).
3. PgBouncer, l'orchestrateur et les workers dans le cluster : seule la couche API a été exercée.
4. Une bascule **sous charge** : 20 requêtes séquentielles, pas de requête en cours au moment de l'arrêt (une requête en vol sur l'instance arrêtée échoue).
5. Le **gain de débit** avec le nombre d'instances ([DIMENSIONNEMENT.md](DIMENSIONNEMENT.md) §7).
6. La **haute disponibilité de PostgreSQL**, des artefacts S3 sur deux vrais hôtes, de Kubernetes appliqué.
7. Les flux longs (long-poll des runners) à travers deux répartiteurs en série : délais à valider.
