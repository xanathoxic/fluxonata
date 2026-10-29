# T03 — Appel du service de test, erreurs et reprise

**Objectif** : vérifier une action à réseau restreint, une branche d'erreur et une nouvelle tentative, contre le **service de test
local** de la pile (jamais Internet). **Impact hors plate-forme** : aucun : `testsvc` n'est joignable que depuis le réseau
interne de la pile, et l'action n'accepte qu'un **chemin** (la machine visée est fixe).

## Prérequis
- `scripts/doctor.sh` indique `testsvc` en bonne santé. Aucune connexion ni secret à créer.
- Le service de test répond : `/health` (200), `/echo?…` (200, renvoie la requête et l'heure du serveur), tout autre chemin (404).

## Prompt
```text
Crée un workflow « Contrôle du service de test ».
Information demandée au lancement : « chemin » (chemin à interroger sur le service de test, par exemple « /health »), avec la valeur par défaut « /health ».
Ce qu'il doit faire :
1. Appeler l'action « Appel HTTP (service de test local) » avec le chemin demandé.
2. Si l'appel réussit, produire un rapport « Service sain » avec l'action « Créer un rapport (artefact) » contenant le code reçu.
3. S'il échoue, attendre 5 secondes (étape d'attente), puis réessayer une seule fois.
   Si la deuxième tentative échoue aussi, produire un rapport « Service en défaut » puis terminer en échec avec le message « Service de test indisponible ».
Sorties : « code » (le code HTTP reçu, ou 0 si échec) et « sain » (vrai ou faux).
```

## Plan attendu
- Étapes : **action** d'appel → (succès) rapport ; (sortie d'erreur) **attente 5 s** → **seconde** action d'appel → (succès) rapport ; (erreur) rapport « en défaut » → **exception**.
- L'appel utilise l'action du catalogue (pas une tâche script qui ouvrirait un socket : un script n'a pas de réseau).
- Une seule nouvelle tentative, pas de boucle.

## Données d'essai
| Cas | `chemin` | Attendu |
|---|---|---|
| A | `/health` | `code = 200`, `sain = true`, rapport « Service sain » |
| B | `/echo?x=1` | `code = 200`, corps contenant `"x": ["1"]` |
| C | `/introuvable` | 404 → échec de l'action → attente ≈ 5 s → **deuxième** tentative → échec → rapport « Service en défaut » → exécution en **échec**, message « Service de test indisponible » |
| D | `http://exemple.test/` | **refus immédiat** par l'action (« Chemin non autorisé ») : un chemin doit commencer par `/` ; la même séquence d'échec s'applique |

## Vérifications
- [ ] Cas A et B : une seule exécution d'action d'appel ; la sortie contient `status = 200`.
- [ ] Cas C : **exactement deux** tentatives, séparées d'environ 5 secondes (voir la chronologie de l'exécution) ; statut final **échec** avec le message prévu.
- [ ] Cas D : l'action refuse **avant** tout appel réseau (aucune adresse extérieure n'est contactée).
- [ ] Aucun secret n'apparaît (il n'y en a pas : c'est aussi ce que l'on vérifie dans les sorties et l'audit).
- [ ] L'onglet « Utilisée par » de l'action `Appel HTTP (service de test local)` liste le workflow une fois publié.

## Nettoyage
Déprécier le workflow. Il sert de base au test [T08](T08-modifier-avec-assistant.md).
