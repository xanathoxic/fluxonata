# T09 — Planification et sous-workflow

**Objectif** : un workflow **planifié** en Recette appelle un **sous-workflow** ; vérifier le déclenchement, le fuseau horaire et
l'arrêt propre. **Impact hors plate-forme** : aucun : le sous-workflow est celui de T03 (service de test local).

## Prérequis
« Contrôle du service de test » (T03) **publié** dans la Recette.

## Prompt
```text
Crée un workflow « Contrôle planifié ».
Aucune information au lancement.
Ce qu'il doit faire :
1. Appeler le workflow publié « Contrôle du service de test » avec le chemin « /health » (sous-workflow) et attendre son résultat.
2. Si le sous-workflow réussit, produire un rapport « Contrôle planifié réussi » avec l'action « Créer un rapport (artefact) ».
   S'il échoue, produire un rapport « Contrôle planifié en échec » et terminer en échec avec le message « Contrôle planifié en échec ».
Sortie : « sain » (vrai ou faux).
```

## Planification (barre d'actions du workflow → Planifier)
- Expression cron : `* * * * *` (chaque minute), **fuseau `Europe/Paris`**, environnement **Recette**, chevauchements **interdits**.
- Laisser tourner **3 minutes**, puis **désactiver** la planification.

## Vérifications
- [ ] Le sous-workflow n'existe pas encore → la **validation** le signale (« workflow inconnu ») ; une fois T03 publié, elle passe.
- [ ] Trois exécutions (environ) apparaissent, déclenchées par la planification, avec l'heure locale du fuseau choisi.
- [ ] Chaque exécution montre l'exécution **enfant** du sous-workflow, liée à son parent.
- [ ] Arrêter le service de test (`docker compose -f infra/docker-compose.yml stop testsvc`) : les exécutions suivantes **échouent** proprement (rapport « en échec ») ; le redémarrer : elles réussissent de nouveau.
- [ ] Planification **désactivée** : plus aucune exécution ne démarre ; à la réactivation, observer le traitement des occurrences manquées (réglage de la planification) et noter le comportement.
- [ ] Aucune exécution n'a lieu en **Production** (la production exige une promotion relue).

## Nettoyage
Supprimer la planification, déprécier le workflow, redémarrer `testsvc` s'il a été arrêté.
