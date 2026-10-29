# T05 — Rapport depuis une base de données (lecture seule)

**Objectif** : lire des données par une requête **SQL paramétrée**, les transformer et produire un rapport, avec un compte qui **ne
peut que lire**. **Impact hors plate-forme** : aucun : la base est un **conteneur jetable** contenant des données fictives.

## 1. Ressource jetable
```bash
# mot de passe généré, jamais écrit dans un fichier ni dans un prompt
export PGPASS="$(openssl rand -hex 16)"; export PGRO_PASS="$(openssl rand -hex 16)"
docker run -d --name fluxonata-test-pg -e POSTGRES_PASSWORD="$PGPASS" -p 127.0.0.1::5432 postgres:16-alpine
until docker exec fluxonata-test-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
docker exec -i fluxonata-test-pg psql -U postgres -v ro="$PGRO_PASS" <<'SQL'
CREATE TABLE commandes (id serial PRIMARY KEY, client text, montant numeric(10,2), statut text, cree_le date);
INSERT INTO commandes (client, montant, statut, cree_le) VALUES
 ('Alpha', 120.50, 'payee', '2026-10-01'), ('Beta', 80.00, 'payee', '2026-10-02'),
 ('Gamma', 300.00, 'en_attente', '2026-10-03'), ('Alpha', 45.25, 'annulee', '2026-10-04'),
 ('Delta', 999.99, 'payee', '2026-10-05');
CREATE ROLE lecture LOGIN PASSWORD :'ro';
GRANT CONNECT ON DATABASE postgres TO lecture;
GRANT USAGE ON SCHEMA public TO lecture;
GRANT SELECT ON commandes TO lecture;
SQL
echo "port : $(docker port fluxonata-test-pg 5432/tcp | head -1 | sed 's/.*://')   mot de passe du compte « lecture » : $PGRO_PASS"
```
Pour que la plate-forme accepte une adresse locale, l'opérateur ajoute **temporairement** `127.0.0.1` à `ORCH_EGRESS_ALLOWLIST` dans `.env`,
puis relance la pile (`scripts/dev-down.sh && scripts/dev-up.sh`). **Le retirer à la fin.**

## 2. Connexion (Connexions → Nouvelle connexion → « Base PostgreSQL »)
Nom `base-test`, hôte `127.0.0.1`, port affiché ci-dessus, base `postgres`, utilisateur `lecture`, mot de passe : celui affiché (saisi **dans le formulaire**, stocké chiffré).
« Tester » doit réussir. Ne **jamais** utiliser le compte `postgres`.

## 3. Prompt
```text
Crée un workflow « Rapport des commandes ».
Informations demandées au lancement : « statut » (statut des commandes à lire : « payee », « en_attente » ou « annulee », par défaut « payee ») et « montant_min » (montant minimum, nombre, par défaut 0).
Ce qu'il doit faire :
1. Lire dans la base avec l'étape « Requête SQL » et la connexion « base-test » les commandes dont le statut est « statut » et le montant au moins « montant_min », triées par montant décroissant. La requête est un seul SELECT avec des paramètres liés. L'étape reste en mode test tant que je ne la passe pas en réel.
2. Calculer avec une tâche script le nombre de commandes, le total des montants et le plus gros client.
3. Produire un rapport « Commandes {statut} » avec l'action « Créer un rapport (artefact) » contenant ces trois valeurs.
Sorties : « nombre », « total » et « meilleur_client ».
```

## Données d'essai
| Cas | `statut` | `montant_min` | Mode de l'étape SQL | Attendu |
|---|---|---|---|---|
| A | `payee` | `0` | test | sortie **simulée** (`simulated = true`), aucune connexion à la base |
| B | `payee` | `0` | **réel** | `nombre = 3`, `total = 1200.49`, `meilleur_client = Delta` |
| C | `payee` | `100` | réel | `nombre = 2`, `total = 1120.49`, `meilleur_client = Delta` |
| D | `x'; DROP TABLE commandes; --` | `0` | réel | **aucun effet** : soit refus à la saisie (si le champ est une liste de choix), soit la valeur est un paramètre lié (0 ligne) ; la table existe toujours |

## Vérifications
- [ ] La requête générée est **un seul SELECT** avec `%(statut)s` / `%(montant_min)s` (jamais de valeur collée dans le texte).
- [ ] Cas D : `docker exec fluxonata-test-pg psql -U postgres -c '\dt'` montre toujours `commandes`.
- [ ] Tentative d'écriture : changer la requête en `DELETE FROM commandes` est **refusé** (une seule instruction `SELECT` autorisée), et le compte `lecture` n'aurait de toute façon pas le droit.
- [ ] Le mot de passe n'apparaît ni dans le workflow, ni dans les sorties, ni dans le journal d'exécution, ni dans l'audit.

## Nettoyage
```bash
docker rm -f fluxonata-test-pg; unset PGPASS PGRO_PASS
```
Supprimer la connexion `base-test`, **retirer `127.0.0.1` de `ORCH_EGRESS_ALLOWLIST`** et relancer la pile, déprécier le workflow.
