# T04 — Traitement d'un lot avec un élément défectueux

**Objectif** : une boucle bornée traite une liste ; **un élément défectueux ne doit pas faire perdre les autres** ; le bilan dit
ce qui a réussi et échoué. **Impact hors plate-forme** : aucun (calcul sur des textes fictifs, rapport en artefact).

## Prompt
```text
Crée un workflow « Analyse d'un lot de textes ».
Information demandée au lancement : « textes » (liste de textes à analyser, de 1 à 20 éléments).
Ce qu'il doit faire :
1. Pour chaque texte de la liste, avec au plus 3 traitements en parallèle, appeler l'action « Statistiques de texte ».
   Si l'analyse d'un texte échoue (par exemple un texte vide), l'élément est noté en erreur mais les autres continuent.
2. Calculer avec une tâche script : le nombre de textes réussis, le nombre de textes en erreur, et le total de mots des textes réussis.
3. Produire un rapport « Bilan du lot » avec l'action « Créer un rapport (artefact) » contenant ces trois valeurs.
4. Si au moins un texte est en erreur, terminer en échec avec le message « Lot partiellement traité » ; sinon terminer normalement.
Sorties : « reussis », « en_erreur » et « total_mots ».
```

## Plan attendu
- Étape **Pour chaque** (1 à 100 itérations, parallélisme ≤ 10 : ici bornée à 20 et 3), l'erreur d'un élément **n'arrête pas** la boucle.
- Le script d'agrégation lit les résultats de la boucle (pas `steps` directement).
- L'échec final vient d'une **exception** placée après le rapport (le rapport existe même en cas d'échec).

## Données d'essai
| Cas | `textes` | Attendu |
|---|---|---|
| A | `["un deux trois", "quatre cinq", "six"]` | `reussis = 3`, `en_erreur = 0`, `total_mots = 6`, exécution **réussie** |
| B | `["un deux trois", "", "six"]` | `reussis = 2`, `en_erreur = 1`, `total_mots = 4`, rapport produit, exécution en **échec** « Lot partiellement traité » |
| C | 21 textes | refus à la saisie : **plafond de 20 éléments** respecté (aucune exécution créée) |
| D | `[]` | refus à la saisie (au moins 1 élément) ou bilan à 0, sans plantage |

## Vérifications
- [ ] Cas B : les éléments 1 et 3 ont bien une sortie ; l'élément 2 est en erreur ; l'ordre des résultats suit celui de la liste.
- [ ] Jamais plus de 3 étapes simultanées (chronologie de l'exécution).
- [ ] Le rapport est téléchargeable dans les cas A **et** B.
- [ ] Rejouer le cas B depuis l'historique (« relancer ») redonne le même résultat.

## Nettoyage
Déprécier le workflow.
