# T08 — Modifier un workflow en discutant avec l'assistant

**Objectif** : vérifier que l'assistant **propose** sans rien appliquer d'office, que l'on peut **appliquer**, **annuler**, et qu'il
ne supprime jamais quelque chose qu'on ne lui a pas demandé de supprimer. **Impact hors plate-forme** : aucun (brouillon).

## Prérequis
Le workflow « Contrôle du service de test » du test [T03](T03-service-http-erreurs.md), ouvert en **brouillon** (Schéma → bouton **Assistant IA**).

## Conversation à dérouler (un message après l'autre)
| # | Message à envoyer | Attendu |
|---|---|---|
| 1 | `Que fait ce workflow ?` | une explication fidèle (appel, nouvelle tentative, rapports, échec final) ; **aucune modification proposée** |
| 2 | `Ajoute une notification à la fin, qu'il réussisse ou non : « Contrôle terminé » à « astreinte@exemple.test ».` | **proposition** listant les étapes ajoutées ; rien n'est appliqué tant qu'on n'a pas cliqué **Appliquer au brouillon** |
| 3 | *Appliquer* | l'étape apparaît à droite de sa précédente ; les autres ne bougent pas ; **Annuler** la retire |
| 4 | `Supprime l'attente de 5 secondes.` | proposition de suppression **de cette étape seulement** ; le résumé dit ce qui a réellement changé |
| 5 | `Ajoute une variable « essais » valant 2.` | une **variable** ajoutée (pas une étape) |
| 6 | `Retire la notification.` *(après l'avoir appliquée)* | suppression explicitement demandée → autorisée |
| 7 | `Ajoute une étape qui appelle l'action « Action Inexistante ».` | refus clair : « Non appliqué : … », **pas d'identifiant inventé** |
| 8 | `Supprime toutes les approbations.` *(sur un workflow qui en contient, par exemple T02)* | **jamais** : une approbation omise est remise ; l'assistant l'explique |

## Vérifications
- [ ] Étapes 2 et 4 : la conversation montre « Modifications proposées » **avant** toute modification du schéma.
- [ ] Après chaque application, **Annuler** (conversation ou barre d'outils) ramène exactement l'état précédent.
- [ ] Après les modifications, la **validation** du workflow ne signale rien de nouveau, sinon l'assistant l'a dit dans son compte rendu.
- [ ] Si le workflow est modifié à la main entre la proposition et l'application : la proposition est **périmée** et il faut la redemander.
- [ ] L'assistant ne **publie** rien ; la version publiée de T03 est intacte.

## Nettoyage
Annuler jusqu'à l'état d'origine, ou supprimer le brouillon modifié.
