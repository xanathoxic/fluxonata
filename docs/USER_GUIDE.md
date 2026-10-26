# Guide utilisateur illustré

Toutes les captures proviennent du jeu de **données fictives** de démonstration (`scripts/screenshots.sh`) ; aucun secret ni donnée réelle.
Trois profils utilisent la même application : **utilisateur métier**, **concepteur**, **administrateur/développeur**. La complexité avancée n'apparaît qu'à la demande.

## 1. Se connecter
![Connexion](img/01-connexion.png)
Identifiant et mot de passe (ou « SSO de l'organisation » si votre administrateur l'a activé). Un message d'erreur distingue : identifiants incorrects, trop de tentatives, problème technique.

## 2. Accueil
> **Menu de gauche repliable** : le bouton à gauche du logo replie le menu en une colonne d'icônes (le contenu gagne la place) et le déplie. Chaque icône garde son nom (info-bulle et lecteurs d'écran) et les sous-menus de « Bibliothèques » restent atteignables ; le choix est mémorisé. Sur téléphone, le bouton « Menu » reste utilisé.

![Accueil](img/02-accueil.png)
L'accueil s'adapte à votre rôle : **À traiter** (approbations en attente, décisions à prendre), **Démarrer en 5 minutes** (guide intégré), **Exécutions récentes**.
En haut de chaque écran : l'**espace** et l'**environnement** (Recette / Production) sont toujours visibles, en toutes lettres ; la production affiche en plus un bandeau d'avertissement.

## 3. Trouver une automatisation (utilisateur métier)
![Catalogue](img/03-catalogue.png)
Le catalogue ne contient que des **automatisations terminées** : des workflows qu'une personne autorisée à publier a choisi de proposer (bouton **« Publier au catalogue »** dans la barre d'actions d'un workflow qui a une version publiée ; **« Retirer du catalogue »** pour l'enlever). Les actions, qui ne sont que des briques, restent dans Bibliothèques → Actions (où un concepteur peut en lancer une pour un essai). Recherche par nom, description ou dossier ; la fiche indique les informations à fournir, la version et sa date, puis **Lancer**. Un workflow déprécié disparaît du catalogue.

![Fiche d'une automatisation](img/28-catalogue-fiche.png)

## 4. La lancer et lire le résultat
![Formulaire](img/04-formulaire-action.png)
Le formulaire est généré depuis le contrat de l'automatisation : champs obligatoires, aide, erreurs liées au champ concerné, bouton d'exemple.
![Résultat](img/05-resultat.png)
Le statut est écrit en toutes lettres (jamais seulement une couleur). La page se met à jour seule ; les journaux techniques restent repliés.

> **Panneaux latéraux** : les formulaires de création (connexion, planification, webhook, action, membre, jeton) et l'examen d'une demande s'ouvrent dans un panneau par-dessus la page. Échap ou le bouton « Fermer » le referme et le focus revient où vous étiez ; la page derrière est inactive tant qu'il est ouvert.

## 4 bis. Générer un brouillon de workflow avec l'IA (facultatif)
Si l'assistant IA est activé pour l'espace (Administration → Assistant IA), **Workflows → Générer avec l'IA** ouvre un panneau : décrivez en français ce que le workflow doit faire. L'assistant propose un **brouillon** en n'utilisant que les actions du catalogue de l'espace et, quand aucune action ne convient, des **tâches script**.
- Le résultat est **toujours un brouillon** : il n'est jamais publié automatiquement. Le compte rendu indique le nombre d'étapes proposées et les problèmes détectés par la validation.
- Un **script écrit par l'IA** est marqué « non relu » et **bloque la publication** tant que vous ne l'avez pas relu, testé (onglet Test) et confirmé (**J'ai relu ce code**) dans l'éditeur de script.
- En mode *local strict*, aucune donnée ne quitte l'infrastructure (modèle local compatible OpenAI).

## 5. Concevoir un workflow (concepteur)
![Workflows](img/06-workflows.png)
La liste des workflows est un **arbre de dossiers** (Démo / Infrastructure…) avec recherche et favoris (★). Un workflow s'ouvre sur une page à **onglets** : Résumé, Variables, Entrées/Sorties, Schéma, Formulaire d'entrée, Historique des versions, Audit, Exécutions. Une **barre d'actions** propose : Lancer, Valider, Planifier, Toutes les exécutions, Supprimer, Rechercher les utilisations / dépendances.
![Éditeur — schéma](img/07-editeur-schema.png)
L'onglet **Schéma** a une palette d'éléments à gauche (Tâche script, Décision, Attente, Interaction, Exception, Jonction, Pour chaque, Workflow, Étape IA, Note, puis les actions du catalogue), le schéma au centre et, à droite, les **propriétés** de l'élément sélectionné (Général, Entrées/Sorties, Script, Effets…). Double-cliquez ou appuyez sur Entrée sur une étape pour l'ouvrir.
![Éditeur — vue liste](img/08-editeur-liste.png)
La **vue liste** (bouton « Vue liste ») est l'équivalent entièrement utilisable au clavier du schéma.
**Palette et propriétés** : les boutons « Palette » et « Propriétés » de la barre d'outils masquent ou affichent chaque panneau (le schéma gagne la place libérée). La gouttière entre un panneau et le schéma se **glisse à la souris** pour le redimensionner ; au clavier, placez-vous dessus (Tab) puis utilisez les flèches gauche/droite (Maj : pas plus grand) ; double-clic ou Début : largeur par défaut. Les largeurs et l'état sont mémorisés dans votre navigateur. Les nœuds eux-mêmes se déplacent en direct pendant le glissement (un seul pas d'historique, « Annuler » les remet en place).

### Éditeur de code (tâche script)
La boîte de l'éditeur a trois **onglets** : *Code*, *Entrées et sorties* (variables liées, sorties typées) et *Test* (valeurs d'essai, journal, résultat).
Ajoutez une **Tâche script**, liez ses entrées à des variables (`{{ inputs.x }}`, `{{ vars.y }}`), déclarez ses sorties typées, puis « Ouvrir l'éditeur de script ».
![Éditeur de script](img/15-editeur-script.png)
![Résumé d'un workflow](img/16-workflow-resume.png)
- Python ou JavaScript ; `System.log("…")` écrit dans le journal ; les sorties déclarées sont les variables du script à la fin de l'exécution.
- **Tester le script** l'exécute en isolation (conteneur sans réseau, utilisateur non privilégié, système de fichiers en lecture seule) avec des valeurs d'essai ; journal et résultat apparaissent dans la console. Le test n'enchaîne pas le reste du workflow.
- Clavier : Tab indente, **Échap quitte l'éditeur** pour reprendre la navigation (puis Échap ferme la boîte), Ctrl+Espace propose les variables disponibles.

Le brouillon s'enregistre automatiquement ; en cas de modification ailleurs, vous choisissez entre recharger et écraser. Lancer un workflow ouvre un **assistant par étapes** généré par le « Formulaire d'entrée ». Étapes disponibles : action, script, condition, jonction, attente durable, approbation (interaction), sous-workflow, « pour chaque » (borné), exception, étape IA (facultative), note.

## 6. Suivre une exécution, comprendre une erreur
Après « Lancer », vous arrivez sur l'onglet **Suivi** du workflow (onglet dédié, à côté de Schéma) : chaque étape est colorée (icône **et** couleur) selon son état, avec une barre d'avancement (« 2/3 étapes terminées »), le résumé et un panneau pour le résultat et le journal de l'étape cliquée. Le bouton « Détail complet » ouvre la page de l'exécution (journaux, entrées/sorties, chronologie).
![Suivi d'une exécution sur le schéma](img/09-execution-en-attente-approbation.png)
**Plusieurs exécutions simultanées** : la bande « Suivre une exécution » liste les exécutions de ce workflow (les plus récentes d'abord, avec leur état, leur date et la personne qui l'a lancée). Chaque lancement vous mène à *son* exécution, sans la mélanger avec les autres. « Toutes les exécutions en cours » affiche sur chaque étape un compteur **×N** : le nombre d'exécutions qui s'y trouvent (limité aux 20 plus récentes, et à la même version du workflow). 
« En résumé » indique ce qui a réussi, ce qui a échoué et **que faire ensuite**. Une étape à effet externe dont l'issue est incertaine demande une décision humaine explicite (page de détail).

## 7. Approuver depuis un téléphone
![Approbation mobile](img/10-approbation-mobile.png)
La liste reste courte : une ligne par demande. **Examiner** ouvre un panneau avec le contexte, le commentaire et **Approuver / Refuser**.
![Examen d'une demande](img/20-approbation-examen.png)
La demande est compréhensible sans connaître le workflow : intitulé, demandeur, contexte. Vous ne pouvez pas approuver votre propre demande en production. Les promotions vers la production se revoient de la même façon.

## 8. Mes exécutions
![Mes exécutions](img/11-mes-executions.png)
Actions et workflows réunis, statut en toutes lettres, environnement explicite. Recherche, filtre par statut et par type.

## 9. Connexions
![Connexions](img/12-connexions.png)
« Nouvelle connexion » ouvre l'assistant en 4 étapes dans un panneau.
![Assistant de connexion](img/19-connexion-assistant.png)
Le secret est chiffré, **jamais réaffiché**, et testable sans être révélé.

## 10. Bibliothèques (Actions, Skills IA) et déclencheurs
Le menu **Bibliothèques** a deux sous-menus : **Actions** et **Skills IA**.

### Actions
![Bibliothèques — actions](img/13-bibliotheques.png)
Les actions se rangent **par dossiers**, avec le même arbre que les workflows : choisissez un dossier à gauche pour filtrer, et l'icône dossier d'une ligne la **déplace**. Les **dossiers se gèrent dans l'arbre** (workflows, actions et skills IA, avec les droits d'édition) : « Nouveau dossier » (dans le dossier sélectionné, ou à la racine), « Renommer / déplacer » (le contenu et les sous-dossiers suivent ; modifier le chemin complet déplace le dossier ailleurs) et « Supprimer » (réservé aux dossiers **vides** ; les sous-dossiers vides partent avec). Un dossier vide reste visible tant qu'on ne le supprime pas. Versions immuables, comparaison, retour à une version antérieure par **nouvelle** publication, dépendances inverses ; l'onglet **Paquets** gère les paquets signés importables/exportables.

### Skills IA
![Skills IA](img/23-skills-ia.png)
Un **skill** est une compétence IA réutilisable : des **instructions**, des **entrées typées** (référencées par `{{ inputs.nom }}`), le **format de la réponse** et un fournisseur par défaut. Il est **versionné** (version immuable, comme une action) et rangé dans des dossiers.
![Détail d'un skill](img/24-skill-detail.png)
Dans un workflow, l'**Étape IA** propose d'« utiliser un skill de la bibliothèque » ou de garder une **tâche libre**. Avec un skill, vous choisissez le fournisseur (ou celui du skill) et vous reliez ses entrées aux variables du workflow. La **version du skill est figée à la publication du workflow** : publier une nouvelle version du skill ne change pas un workflow déjà publié.
La gouvernance ne change pas : un skill ne contacte aucun modèle par lui-même ; l'appel ne se fait qu'à l'exécution d'une étape IA, soumis à la politique IA de l'espace (désactivée par défaut), et la réponse reste une donnée **non fiable** vérifiée contre le format du skill.

### Déclencheurs
![Déclencheurs](img/14-declencheurs.png)
Deux onglets : planifications (fuseau horaire, chevauchements, occurrences manquées) et webhooks signés. Les créations s'ouvrent dans un panneau.
![Nouvelle planification](img/21-planification.png)

## 11. Administration de la plate-forme
![Plate-forme](img/22-plateforme.png)
Réservée aux **administrateurs de la plate-forme** (menu « Plate-forme ») : créer, renommer, archiver ou restaurer un **espace**, voir l'état des **processus partagés** (workers, orchestrateur), gérer les autres administrateurs de la plate-forme et consulter leur journal. Ce rôle ne donne aucun accès aux données d'un espace dont on n'est pas membre (voir `docs/ADMIN.md`).

## 11 bis. Runners (exécuter sur vos machines)
![Runners](img/30-runners.png)
Plate-forme → Runners (ou Administration → Runners pour un runner dédié à votre espace) : « Nouveau runner » donne un jeton (affiché une fois) et les commandes pour démarrer l'agent sur une machine ou en conteneur. Un runner dédié n'exécute que les actions de son espace ; décochez « Autoriser les runners partagés » pour réserver l'exécution à vos runners. Le détail d'une exécution indique le runner qui l'a exécutée. Voir `docs/RUNNERS.md`.

## 11 ter. Utilisateurs et mot de passe
![Utilisateurs](img/29-utilisateurs.png)
Plate-forme → Utilisateurs : créer un compte, le placer dans un ou plusieurs espaces avec un rôle par espace, réinitialiser un mot de passe, désactiver ou réactiver. Un mot de passe provisoire est affiché une seule fois ; la personne le remplace à sa première connexion. Chacun change son mot de passe dans **Mon compte** (clic sur son nom, en haut à droite). Voir `docs/ADMIN.md`.

## 12. Administration d'un espace
![Santé](img/15-administration-sante.png)
Santé du système en langage clair, membres, jetons, politiques, audit, assistant IA.
![Production](img/16-environnement-production.png)
Le passage en production est explicite et protégé : **promotion** d'une version, revue par une autre personne.

## Voir la plate-forme à l'œuvre : la vitrine
Un scénario complet (skills IA, workflows imbriqués sur trois niveaux, boucles en parallèle, approbation, alerte avec compensation, rapport) se crée avec `scripts/vitrine.sh`. Il crée l'espace « Vitrine — Exploitation » (comptes `alice`, `bob`, `claire`, `admin`).
![Schéma de la vitrine](img/25-vitrine-schema.png)

## Accessibilité
Navigation clavier complète, focus visible, noms accessibles, erreurs associées aux champs, états non portés par la couleur seule, animations réduites si demandé. Vérifié par axe (WCAG 2.2 AA) et des parcours clavier automatisés ;
**pas de test avec lecteurs d'écran réels**.
