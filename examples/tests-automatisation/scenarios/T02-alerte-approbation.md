# T02 — Alerte de supervision → approbation humaine → rapport

**Objectif** : une alerte est triée par gravité ; une alerte critique exige l'accord d'un humain avant l'action corrective.
**Impact hors plate-forme** : aucun. L'« action corrective » est la notification simulée ; l'approbation est faite par `claire`.

## Prompt
```text
Crée un workflow « Traitement d'une alerte ».
Informations demandées au lancement : « hote » (nom du serveur), « gravite » (« info », « avertissement » ou « critique »), « message » (texte de l'alerte).
Ce qu'il doit faire :
1. Si « gravite » vaut « info », ne rien faire d'autre que produire un rapport « Alerte ignorée » avec l'action « Créer un rapport (artefact) ».
2. Si « gravite » vaut « avertissement », envoyer une notification « Alerte à surveiller » à « astreinte@exemple.test » avec l'action « Envoyer une notification (simulé) », puis produire le rapport.
3. Si « gravite » vaut « critique », demander l'approbation d'un approbateur avec le texte « Redémarrer le service sur l'hôte demandé ? ».
   - Si c'est approuvé : notification « Redémarrage autorisé » à « astreinte@exemple.test », puis rapport « Alerte critique traitée ».
   - Si c'est refusé : rapport « Redémarrage refusé » et le workflow se termine en échec avec le message « Redémarrage refusé par l'approbateur ».
Sorties : « decision » (« ignoree », « surveillee », « approuvee » ou « refusee »).
Ne contacte aucun service externe.
```

## Plan attendu
- Entrée `gravite` en liste de choix (ou contrôle équivalent) ; **trois** branches (switch ou décisions).
- Une étape **Approbation** avec le texte demandé ; sa branche « refusé » aboutit à une **exception** (étape d'échec) avec le message demandé, pas à un silence.
- Le rapport est produit dans les trois branches.

## Données d'essai
| Cas | `hote` | `gravite` | `message` | Attendu |
|---|---|---|---|---|
| A | `serveur-app-01` | `info` | `Disque à 40 %` | `decision = ignoree`, aucune notification, aucune demande d'approbation |
| B | `serveur-app-01` | `avertissement` | `Disque à 85 %` | `decision = surveillee`, 1 notification |
| C1 | `serveur-bdd-02` | `critique` | `Service arrêté` | **attente** d'une approbation ; `claire` **approuve** → `approuvee`, notification « Redémarrage autorisé » |
| C2 | `serveur-bdd-02` | `critique` | `Service arrêté` | `claire` **refuse** → rapport « Redémarrage refusé » + exécution en **échec** avec le message prévu |

## Vérifications
- [ ] En C1/C2, rien n'avance avant la décision de `claire` (l'exécution est « en attente d'approbation »).
- [ ] L'auteur du lancement (`alice`) ne peut pas approuver sa propre demande si la politique de l'espace l'interdit ; sinon noter le comportement.
- [ ] Depuis un téléphone (affichage mobile), la demande est lisible : hôte, gravité, message.
- [ ] En C2, la branche d'échec est bien celle de l'exception : statut final « échec », message exact.
- [ ] L'audit de l'espace contient la décision (qui, quand, commentaire).

## Nettoyage
Déprécier le workflow ; les exécutions de test restent dans l'historique (sans conséquence).
