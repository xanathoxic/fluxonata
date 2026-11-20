# API store (extension `apistore`)

Extension **optionnelle** (ADR 0030), livrée avec le code mais **inactive par défaut** : elle n'est active que si `ORCH_EXTENSIONS=apistore` est défini avant le démarrage, puis `manage.py migrate`.
Sans cela, aucune table, aucune route et aucune entrée de menu n'existent. **Procédure complète** (développement, production, désactivation, rôle `ui`) : [docs/INSTALL.md](INSTALL.md), « Activer l'API store ».

## Principes
- Un **produit** regroupe des workflows ; un **plan** fixe les environnements autorisés, les quotas (par minute, par jour ; vide = illimité) et le mode d'approbation (automatique ou par une personne).
- Une **souscription** relie un consommateur à un plan. Les workflows s'exécutent sous le **compte de service du produit**, choisi par un administrateur (jamais par le demandeur : sinon il s'octroierait des droits) ; la gouvernance habituelle (promotion en production…) s'applique.
- Un produit ne se publie qu'avec un compte de service, au moins un plan et au moins un workflow, tous publiés.
- Une **clé** (`fxs_…`) appartient à une souscription : seul son hachage SHA-256 est conservé, la valeur n'est montrée qu'une fois, durée de vie choisie à l'émission (1 à 365 jours, 90 par défaut) ou **illimitée** — cette dernière option est réservée aux administrateurs, car une clé sans expiration ne se périme jamais : à révoquer explicitement.
- Le demandeur ne peut pas approuver sa propre demande. Révoquer une souscription révoque ses clés, définitivement : la souscription passe dans l'**archive** (avec les demandes refusées). Depuis l'archive, un administrateur peut la **réactiver** (une révoquée seulement ; aucune ancienne clé ne revit, il faut en émettre une nouvelle) ou la **supprimer** (l'historique d'usage est conservé, la suppression est auditée).

## Appels
```
POST /api/v1/plugins/apistore/v1/<espace>/<produit>/workflows/<workflow>/runs/
Authorization: Bearer fxs_…           Idempotency-Key: <facultatif>
{"environment": "test", "inputs": {…}}                      → 202 {"run_id", "status", "replayed"}
GET  /api/v1/plugins/apistore/v1/<espace>/<produit>/runs/<run_id>/   → état et sorties (exécutions de la souscription seulement)
   → {"run_id", "status", "outputs", "finished", "progress": {"steps_done", "steps_total", "current_step": {"name", "status", "status_label"} | null, "also_active", "workflow_status", "workflow_status_label"}}
```
Refus : 401 clé invalide/expirée/révoquée · 403 souscription inactive, environnement non autorisé, gouvernance · 404 workflow hors produit (même réponse qu'un produit ou un espace inconnu) · 429 quota.
Un rejeu avec la même `Idempotency-Key` renvoie l'exécution existante (200) sans consommer de quota ; la clé est propre à chaque souscription.

### Progression d'une exécution
`progress` dit où en est le workflow, calculé à la lecture depuis les étapes de l'exécution (pas celles de ses sous-exécutions) :
- `steps_done` : étapes **passées**, c'est-à-dire réussies ou **ignorées** (branche non prise) ; `steps_total` : nombre d'étapes du workflow. Avec des branches, toutes ne s'exécutent pas, mais `steps_done` rejoint `steps_total` à la fin.
- `current_step` : l'étape **active la plus ancienne** — `name` (le nom donné dans l'éditeur), `status` (`running`, `retry_wait`, `waiting`, `uncertain`) et son libellé français. `null` quand rien ne tourne ; pour une exécution **échouée**, c'est l'étape qui a échoué. `also_active` compte les autres étapes actives en parallèle.
- `workflow_status` / `workflow_status_label` : état global (déjà présent à la racine sous `status`).
- **Confidentialité** (choix du 2026-11-20, demandé par le propriétaire) : le **nom** d'une étape est visible des consommateurs ; ne mettez pas de donnée sensible dans les noms d'étapes d'un workflow exposé. **Jamais** exposés : entrées, sorties intermédiaires, messages d'erreur, identifiants de nœuds (sauf en repli si une étape n'a pas de nom). Test de non-fuite : `tests/test_apistore_progress.py`.

## Audit et confidentialité
Chaque appel (accepté ou refusé) crée un événement d'usage : souscription, workflow, environnement, issue, identifiant d'exécution. **Jamais** de valeur d'entrée, de sortie ni de clé.
Les actions de gestion (souscrire, approuver, refuser, suspendre, révoquer, émettre/révoquer une clé) sont inscrites à l'audit de la plate-forme.

## API de gestion (lot 4)
Préfixe `/api/v1/plugins/apistore/manage/<espace>/` ; droits vérifiés côté serveur.

| Route | Droit |
|---|---|
| `GET me/` | voir (ses propres droits : administrer, approuver, exécuter) |
| `GET products/` · `GET products/<slug>/` | voir (les brouillons et le compte d'exécution : administrer) |
| `POST products/` · `PATCH products/<slug>/` (nom, description, `status`, `run_as`, `workflows`) · `POST products/<slug>/plans/` | administrer |
| `GET subscriptions/` (ses propres souscriptions ; toutes pour administrer/approuver) · `POST subscriptions/` | voir · exécuter |
| `POST subscriptions/<id>/decision/` `{approve}` | approuver, jamais sa propre demande |
| `POST subscriptions/<id>/status/` `{status: active\|suspended\|revoked}` (`active` aussi depuis révoquée) · `DELETE subscriptions/<id>/` (archivée seulement) | administrer, personne connectée |
| `POST subscriptions/<id>/keys/` `{days}` ou `{unlimited: true}` (administrateur) · `POST …/keys/<id>/revoke/` · `GET …/usage/` | le demandeur ou un administrateur |

| `GET products/<slug>/docs/` · `GET products/<slug>/docs/openapi/` | voir (produit publié ; un brouillon : administrer) |

Décider, changer l'état et émettre ou révoquer une clé exigent une **personne connectée** : un jeton d'API ne le peut pas. La valeur d'une clé n'est renvoyée qu'à son émission.
Ces routes sont hors du contrat OpenAPI du cœur (extension optionnelle).

## Interface (lot 5)
Entrée de menu « API store » (groupe Intégrer) : **Catalogue** (produits publiés, plans, demande de souscription), **Souscriptions** (clés, usage sur 24 h et derniers appels), **Approbations** (demandes en attente) et **Administration** (produits, plans, workflows, compte de service, publication ; visible seulement si le serveur renvoie le compte d'exécution, donc aux administrateurs).
Le module `ui/index.js` n'insère jamais de HTML venu du serveur (`textContent` uniquement) ; la clé en clair s'affiche une fois, avec l'avertissement de la copier. L'interface ne décide d'aucun droit : un refus du serveur est affiché tel quel.

## Documentation générée d'un produit (lot 7)
Onglet **Documentation** de l'interface (et bouton « Documentation de l'API » sur chaque carte du catalogue) : pour un produit, une page lisible par ses consommateurs, **calculée à chaque lecture** depuis l'état réel — rien n'est saisi à la main, donc rien ne se périme.
- **Un endpoint par workflow publié du produit** : méthode et chemin, en-têtes, entrées (nom, type, obligatoire, valeurs permises, défaut, contraintes, description, tirés du schéma d'entrée de la **dernière version publiée**), corps d'exemple valide pour ce schéma, réponses 202/200, noms des sorties, schéma JSON complet repliable.
- **Suivi d'une exécution**, authentification, plans et quotas, états d'une exécution, tableau des erreurs (HTTP + `code`) et adresse MCP.
- **Exemples d'appel prêts à copier** en curl, Python (bibliothèque standard) et JavaScript (`fetch`). L'adresse du serveur est celle de la page ; la clé est un marqueur `fxs_VOTRE_CLE` : **aucune clé réelle n'apparaît jamais**.
- **Description OpenAPI 3.1 du produit** téléchargeable (importable dans un outil client) : même source que la page.
- Les workflows du produit sans version publiée sont signalés « pas encore appelables ». Un produit en brouillon n'est documenté que pour les administrateurs (aperçu avant publication).
- Un produit sans workflow (ou sans version publiée) l'annonce au lieu de ne montrer que le suivi d'exécution ; une erreur du serveur sans message est expliquée selon son code (404, refus, panne).
- **Limites** : le type des **sorties** n'est pas déclaré par la plateforme (seuls leurs noms le sont) ; les entrées sont décrites au premier niveau (le schéma complet est disponible pour les objets imbriqués) ; les exemples sont fabriqués depuis le schéma, pas depuis de vrais appels. Rendu dans un navigateur réel (E2E) **non exécuté** : vérifié par tests unitaires (serveur et interface).

## MCP (lot 6)
```
POST /api/v1/plugins/apistore/v1/<espace>/<produit>/mcp/        Authorization: Bearer fxs_…
```
JSON-RPC (`initialize`, `ping`, `tools/list`, `tools/call`) : les workflows du produit sont les outils. Même porte que l'appel REST (`service.invoke`) : plan, quotas, identité d'exécution, gouvernance et usage sont identiques.
L'environnement est **le premier du plan** (l'administrateur le choisit) ; un outil n'élargit jamais le plan. La réponse attend le résultat 25 s au plus, puis renvoie `{"status": "running", "run_id"}` (à suivre par `GET …/runs/<id>/`).
Le message d'erreur interne d'une exécution échouée n'est pas transmis au consommateur.

## Revue de sécurité (2026-11-19)
Vérifié par tests : seul le hachage d'une clé est stocké ; clé inconnue, expirée, révoquée ou souscription inactive refusées ; un produit ou un espace différent de celui de la clé répond comme un workflow absent ; les droits (administrer, approuver, exécuter) sont tous contrôlés côté serveur, décider/changer l'état/émettre une clé exigent une personne ; le demandeur n'approuve pas sa demande ; un membre ne voit ni les clés ni l'usage d'une souscription qui n'est pas la sienne (404) ; l'idempotence est propre à la souscription ; les quotas tiennent sous appels simultanés (verrou) ; aucun événement d'usage ne contient de valeur d'entrée ; l'interface n'injecte pas de HTML.
**Limites connues, non corrigées** :
- Les appels **refusés** d'une clé valide créent chacun un événement d'usage : un détenteur peut les multiplier et faire croître la table. Aucune purge n'existe encore (à prévoir : rétention ou agrégation).
- Une clé **inconnue** n'est ni limitée en débit ni consignée ; la valeur de 256 bits rend la devinette impraticable, mais un limiteur en amont (proxy) reste conseillé.
- Les sorties d'un workflow sont renvoyées au consommateur : n'exposez que des workflows dont les sorties sont destinées à l'extérieur.
- Non exécuté : parcours dans un navigateur réel, E2E, test avec un vrai client MCP.

Quotas mensuels et notifications : hors lots actuels.
