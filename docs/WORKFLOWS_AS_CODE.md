# Workflows as code (lien avec Git)

Les workflows d'un espace s'exportent en fichiers JSON (un fichier par workflow, lisibles en revue) et se réimportent comme
**brouillons** : rien n'est publié ni lancé par l'import. Le lien avec Git passe donc par vos outils habituels (dépôt, demande de
fusion, CI) **ou** par l'intégration Git native de la plateforme (GitHub et GitLab, voir plus bas).

## Commandes

```bash
export FLUXONATA_TOKEN=...   # jeton de service ; lecture pour exporter, édition pour importer
scripts/workflows_sync.py export --url https://flux.exemple.org --workspace demo --out workflows/
scripts/workflows_sync.py import --url https://flux.exemple.org --workspace demo --from workflows/ --dry-run
scripts/workflows_sync.py import --url https://flux.exemple.org --workspace demo --from workflows/ --overwrite
```

Le jeton n'est jamais passé en argument (il apparaîtrait dans la liste des processus et l'historique du shell).

## Règles de l'import
- Un identifiant existant n'est remplacé qu'avec `--overwrite` ; son numéro de révision de brouillon avance, donc un éditeur ouvert
  voit un conflit au lieu de perdre son travail en silence.
- Les données épinglées (`pins`) ne sont pas exportées.
- Les graphes sont contrôlés (types d'étapes, taille, identifiants) ; un fichier refusé est signalé et le code retour est non nul.
- `--dry-run` indique ce qui serait créé, remplacé ou refusé, sans rien écrire.

## Schéma de CI proposé
1. Demande de fusion : `import --dry-run` sur un espace de recette (contrôle des fichiers).
2. Après fusion : `import --overwrite` sur la recette ; les concepteurs vérifient, **publient** puis **demandent la promotion**
   en production (la promotion reste soumise à revue par une autre personne : aucune CI ne la contourne).

## Intégration Git native (GitHub, GitLab)
Administration > **Git** : fournisseur, dépôt (`propriétaire/nom` ou `groupe/projet`), branche principale, dossier du dépôt (par défaut
`workflows`) et une connexion « Service HTTP » qui porte le jeton (adresse = celle de l'API : `https://api.github.com`,
`https://gitlab.com/api/v4` ou votre instance). Le jeton n'a besoin que de lire le dépôt et d'écrire des branches / demandes de fusion.

- **Proposer les modifications** : la plateforme compare vos brouillons aux fichiers du dépôt (empreinte Git, sans les télécharger),
  écrit **seulement ce qui a changé** (`<identifiant>.json`, JSON stable pour des différences lisibles) sur une **nouvelle branche**
  `fluxonata/sync-…` et ouvre la demande de fusion. Rien n'est jamais écrit sur la branche principale et rien n'est supprimé du dépôt
  (les fichiers sans workflow correspondant sont seulement signalés).
- **Aperçu de l'import** puis **Importer en brouillons** : lit la branche principale, contrôle chaque fichier (format, identifiant égal au
  nom du fichier, structure du graphe, dossiers privés) et crée ou remplace des **brouillons** (remplacement seulement avec la case
  « Remplacer »). Rien n'est publié ni lancé ; la promotion en production reste soumise à revue.
- Les appels sont faits par un worker, jamais par le processus web, et passent tous par la même protection que les autres sorties réseau
  (adresses publiques seulement, pas de redirection). Une seule opération à la fois par espace ; une opération interrompue est
  marquée échouée, jamais rejouée.
- Les workflows sont exportés avec **les droits de la personne qui lance l'opération** : les dossiers privés qu'elle ne voit pas ne
  sont pas envoyés.

## Limites
Pas de synchronisation automatique ni de webhook entrant (on lance l'opération à la main ou par l'API), pas de résolution de conflit de contenu (le brouillon est remplacé en
entier), connexions et secrets non exportés (ils sont propres à chaque instance : les workflows y renvoient par leur nom).
