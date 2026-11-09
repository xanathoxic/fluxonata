# Mise à niveau vers la v0.4.5 ou plus : migration `tenancy/0014` (jetons personnels)

Ce document est autonome : il suffit, avec `docs/INSTALL.md` §7 (sauvegarde, mise à jour, retour arrière), pour passer d'une installation existante à la v0.4.5 ou plus.
Les commandes s'exécutent dans le dossier du dépôt, avec la variable `$C` de `docs/INSTALL.md` §7.2 (installation de type production) ; pour le déploiement par rôles ou Kubernetes, voir la fin de ce document :
```bash
C="docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod"
```
**Place dans la mise à jour** (`docs/INSTALL.md` §7.2) : étape 1 sauvegarde, étape 2 récupération de la nouvelle version, **puis la procédure « Appliquer » ci-dessous**, puis l'étape 3 (`$C up -d --build`) et l'étape 4 (vérifications).

**Qui est concerné.** Une installation **existante** qui passe d'une version **antérieure à la v0.4.5** (jusqu'à la v0.4.4) à la v0.4.5 ou plus (la migration est dans toutes les versions publiées depuis la v0.4.5).
Pas concernées : une installation **neuve** (tables vides, aucun trafic : aucun risque de verrouillage mesuré) et une installation qui a déjà appliqué `0014`
(`$C exec -T api python manage.py showmigrations tenancy | grep 0014` affiche `[X]`). La migration est publiée : elle ne sera pas modifiée.

**Ce qu'elle fait** (une seule transaction PostgreSQL, donc tout ou rien) : ajoute la colonne `user_id` à `tenancy_apitoken`, rend `service_account_id` facultatif (en recréant sa clé étrangère),
ajoute la contrainte `api_token_one_owner` (un jeton appartient soit à une personne, soit à un compte de service) et crée un index.

**Risque de verrouillage (mesuré, pas garanti).** Jusqu'à la fin de la migration, PostgreSQL retient un verrou exclusif sur `tenancy_apitoken` et sur `tenancy_serviceaccount`, et un verrou
qui interdit les écritures sur `auth_user` : pendant ce temps, l'authentification par jeton (lecture et mise à jour de `last_used_at`), les comptes de service et les écritures sur les utilisateurs
(la connexion met à jour `last_login`) **attendent** ; la simple lecture de `auth_user` n'est pas bloquée. Mesures sur données **synthétiques**, PostgreSQL 17, disque local, une exécution par taille :
0,08 s pour 10 000 jetons, 0,16 s pour 200 000, 5,1 s pour 1 000 000 (dont 2,8 s pour l'index). Ce ne sont ni des garanties ni des prévisions pour votre matériel et votre charge ;
au-delà de 1 million de lignes, rien n'a été mesuré. Le risque réel vient surtout de l'**attente du verrou** : une transaction longue qui touche déjà l'une de ces tables fait attendre la migration
(sans limite par défaut), et les requêtes qui arrivent ensuite attendent derrière elle (mesuré : la migration a attendu 14 s la fin d'une lecture, et pendant cette attente les requêtes de jeton ont dépassé un délai d'essai de 4 s).
Ce comportement existe quelle que soit la taille de la table. Les tables de jetons sont d'ordinaire petites, mais **comptez-les**, ne le supposez pas.

**Avant (préconditions).**
1. Sauvegarde complète et clés, copiées hors du serveur (`docs/INSTALL.md` §7.2, étape 1). Notez le commit déployé.
2. Nombre de jetons et transactions longues :
   ```bash
   $C exec -T db psql -U fluxonata -c "SELECT count(*) FROM tenancy_apitoken"
   $C exec -T db psql -U fluxonata -c "SELECT pid, state, now()-xact_start AS age, application_name FROM pg_stat_activity WHERE datname='fluxonata' AND xact_start IS NOT NULL AND now()-xact_start > interval '30 seconds'"
   ```
3. **Conditions d'arrêt** : n'appliquez pas la migration tant qu'une transaction de plus de 30 s (ou `idle in transaction`) est ouverte ; au-dessus de 1 million de lignes, répétez d'abord la mise à niveau sur une copie restaurée de la sauvegarde (`scripts/restore-test.sh` ou §7.2) et lisez la durée observée ; choisissez une période de faible activité.

**Appliquer** (la migration est lancée seule, avec une limite d'attente du verrou, avant le `up` habituel) :
```bash
$C build migrate
$C run --rm -e PGOPTIONS="-c lock_timeout=5000" migrate ; echo "code retour : $?"
```
`lock_timeout=5000` (5 s) fait **échouer** la migration plutôt que d'attendre sans fin ; `PGOPTIONS` n'est posé que sur ce lancement, jamais dans `infra/.env.prod` (partagé par les services qui passent par PgBouncer ;
le service `migrate` se connecte directement à `db`). Essai de cette commande telle quelle (Compose v5.0.2, base PostgreSQL jetable, données synthétiques) : avec une lecture longue ouverte sur `tenancy_apitoken`, elle a échoué par `canceling statement due to lock timeout` (code retour 1), la base est restée inchangée (ni colonne `user_id`, ni `0014` enregistrée), puis la même commande a réussi sans lecteur (code retour 0). Pendant l'attente du verrou, l'ancien conteneur `api` resté en service a eu une requête authentifiée qui a dépassé 3 s ; avant et après cette fenêtre d'environ 5 s ses réponses étaient normales : la limite borne la gêne à sa durée. `$C build migrate` n'a pas été exécuté dans cet essai.
Avec le code retour 0, poursuivez avec `$C up -d --build` (§7.2, étape 3) : l'ancien `api` est alors **recréé**, et `migrate` est relancé par Compose sans rien à faire (« No migrations to apply », vérifié).

**Ne sautez pas l'étape préalable.** Sans elle, `$C up -d --build` recrée les conteneurs applicatifs **avant** d'exécuter `migrate` (ordre observé : « Recreate », puis démarrage de `migrate`, puis démarrage du nouvel `api`),
et `migrate` s'exécute **sans** `lock_timeout` : l'ancien `api` est arrêté pendant toute la migration et, si une transaction longue tient un verrou, pendant toute son attente (observé : 26 s d'arrêt pour une lecture de 25 s, migration appliquée à la fin).
Pour Kubernetes, voir « Autres déploiements » ci-dessous.

**Si elle échoue.** Une erreur `canceling statement due to lock timeout` laisse la base **inchangée** (transaction annulée : ni colonne ni contrainte, vérifié) : attendez la fin de la transaction qui bloquait
(étape 2), puis relancez la même commande. Ne relevez pas la limite pour « forcer ». Pour toute autre erreur, gardez le message (`$C logs migrate`), ne relancez pas en boucle, et restaurez la sauvegarde si l'état de la base est douteux (§7.2, retour arrière).

**Après.**
```bash
$C exec -T api python manage.py showmigrations tenancy | grep 0014                      # [X] 0014_personal_api_tokens
$C exec -T db psql -U fluxonata -c "SELECT conname FROM pg_constraint WHERE conname='api_token_one_owner'"   # 1 ligne
$C exec -T db psql -U fluxonata -c "SELECT count(*) FROM tenancy_apitoken WHERE user_id IS NULL AND service_account_id IS NULL"   # 0
```
Puis un appel authentifié par un jeton de compte de service existant (réponse 200) et, dans « Mon compte », la création d'un jeton personnel. Les jetons de compte de service existants restent valides.

**Retour d'image.** Les jetons personnels créés après la mise à niveau sont refusés (HTTP 401, sans erreur serveur) par l'ancien code ; les jetons de compte de service restent acceptés (`scripts/compat-test.sh`, sonde « jeton personnel »).
Revenir au code précédent n'annule pas la migration.

**Ce que ne change pas une migration ultérieure.** Les verrous pris par `0014` le sont pendant son exécution : aucune migration écrite après elle ne peut les retirer. Pour les **futures** migrations,
la contrainte posée en deux temps (`ADD CONSTRAINT … NOT VALID`, puis `VALIDATE CONSTRAINT`, qui ne bloque pas les lectures et écritures) évite ce verrou long ; elle ne s'applique pas à `0014`.

## Autres déploiements
- **Déploiement par rôles** (`infra/docker-compose.roles.yml`) : le service `migrate` est celui du fichier de base ; la même commande avec les deux fichiers `-f` convient en principe. Non essayé.
- **Kubernetes** : le Job `infra/k8s/base/migrate-job.yaml` lance la même migration **sans limite d'attente du verrou**, et la procédure ci-dessus ne le couvre pas. Pour la même protection, ajoutez la variable d'environnement `PGOPTIONS=-c lock_timeout=5000` au conteneur du Job (non essayé), et appliquez les préconditions ci-dessus avant de lancer le Job.
