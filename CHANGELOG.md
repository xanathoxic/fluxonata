# Journal des modifications

Changements notables de Fluxonata, du plus récent au plus ancien.

## 2026-10-29

**Tests d'automatisation : actions et skills à importer sur une plate-forme vierge (paquet, 2 skills, installateur)**

- Tests d'automatisation : actions et skills à importer sur une plate-forme vierge (paquet, 2 skills, installateur, test)

## 2026-10-29

**Exemples : tests d'automatisation encadrés (plan, 10 scénarios avec prompts, outil de webhook signé)**

- Exemples : tests d'automatisation encadrés (plan, 10 scénarios avec prompts, outil de webhook signé)

## v0.4.1 — 2026-10-29

**Fluxonata 0.4.1 : jeux de test des actions, fiche d'action à onglets, code en lecture seule, saisie JSON des objets et listes**

- Fiche d'une action : onglets Contrat, Code, Versions, Utilisée par, Gestion ; code en lecture seule dans l'éditeur, différences colorées
- Fiche d'une action : onglets Gestion et Utilisée par habillés (doublon supprimé)
- Jeux de test d'une action : enregistrer, modifier, supprimer, proposés au lancement d'un essai
- Jeux de test : lint
- Formulaires : objets et listes saisis en JSON (jeux de test, lancement d'un essai, lancement d'un workflow)
- Formulaires : objets et listes dans un éditeur JSON (coloration, erreurs en ligne, mise en forme)

## v0.4.0 — 2026-10-29

**Fluxonata 0.4.0 : coffre externe HashiCorp Vault, tags, refonte de l'interface, aide en bulles, éditeur de script**

- Retire du suivi Git les sauvegardes locales et des captures d'essai ; les ignore
- Coffre externe (HashiCorp Vault) : AppRole, KV v2 en lecture et écriture, références avec autorisations par espace, secrets dynamiques avec baux ; sélection du fournisseur par secret et par connexion
- Coffre externe : tests contre un vrai Vault et un vrai PostgreSQL (AppRole, KV v2, identifiants dynamiques utilisés pour se connecter, révocation)
- Coffre externe : webhooks signés, rotate_keys, interface Vault (plate-forme, secrets, connexions), ADR 0019, documentation

## v0.3.1 — 2026-10-28

**Fluxonata 0.3.1 : fichiers binaires, Premier arrivé, dossiers privés, Git natif, Exploitation et Mes exécutions refaites, disponibilité des formulaires publics, catalogue avec saisie sur la fiche**

- E2E : délai d'attente des assertions porté à 15 s, attentes plus larges du test mobile (tests intermittents sous charge)
- Fichiers binaires : stockage par exécution, réponse HTTP enregistrée comme fichier, corps brut et multipart avec fichiers, lecture/écriture SFTP binaire, téléchargement
- Étape « Premier arrivé » : la première branche gagne, les autres sont annulées ou laissées terminer ; validation des lectures après la course
- Partage par dossier privé, Git natif (GitHub, GitLab), E2E et documentation
- Exploitation et Mes exécutions refaites ; formulaires publics à durée et nombre d'utilisations limités
- E2E : attente de l'approbation portée à 90 s (démarrage du conteneur sous charge)
- Liste des dossiers lue une seule fois par page ; E2E des dossiers : nettoyage retrouve le workflow par son nom
- Pages publiques : seul le formulaire est chargé (ni appel de session ni code de l'application) ; E2E : contrôle de la publication
- Catalogue : le formulaire de saisie est affiché sur la fiche (plus d'étape intermédiaire) ; délais E2E
- E2E : dossier privé ciblé par son nom (des dossiers privés d'anciens essais subsistent)

## v0.3.0 — 2026-10-28

**Fluxonata 0.3.0 : intégrations, étapes de données, déclencheurs, mise au point, exploitation**

- Assistant IA : suppression de notes et de variables sur demande explicite
- E2E dossiers : noms uniques, test rejouable sur une base de démonstration déjà utilisée
- Assistant IA : langage des expressions expliqué (pas de calcul), réponse honnête quand l'erreur signalée reste
- Éditeur : nœuds Début/Fin déplaçables, lignes ajustables à la souris et au clavier, enregistrés dans le brouillon
- Assistant IA : refus d'approbation sur la sortie d'erreur, étapes isolées signalées, variable non recréée, cas d'évaluation « achat »
- Assistant IA : plan compilé par la plateforme (génération), opérations explicites (modification et correction), cas d'évaluation élargis, consommation affichée
- Documentation : architecture de l'assistant IA (plan et opérations), consommation, ADR 0014
- Éditeur : une position enregistrée pour chaque nœud dès la première modification d'un brouillon sans positions
- Validation : entrées des sous-workflows contrôlées (types, obligatoires, références sans accolades) ; assistant : liaisons réservées retirées, fusion des entrées, réponse honnête ; avertissement plus clair
- Assistant IA : essai à blanc (scripts et décisions exécutés dans le bac à sable, reste simulé), génération suivie en direct (barre de progression et journal), avertissement « étape ignorée » exact
- Actions : contrat obligatoire (description, entrées et sorties décrites, absence déclarée) affiché sur la fiche ; assistant : refus d'approbation et branches, contrôle plus précis ; listes triées sur une clé unique
- Assistant IA : plan avant génération, suivi et annulation des générations (bouton « Générations »), correction en plusieurs passes, essai à blanc enrichi, nouvelles tentatives, contrôles plus justes
- Actions : 9 actions d'opérations utiles, chacune avec un mode test (simulation) et un contrat complet
- Liste des workflows : toujours rechargée à l'ouverture (compteurs de dossiers à jour)
- Expressions : default(valeur, repli) pour lire une sortie d'étape non garantie ; l'assistant protège mécaniquement ces lectures
- Choix multiple (switch) : moteur, validateur, essai à blanc, assistant et éditeur
- Expliquer l'erreur : diagnostic déterministe (chemin, références cassées, journaux) transmis à l'assistant, correction proposée
- Workflow d'erreur, relance d'une exécution, attente reprise par appel HTTP
- Lecture des listes paginées : détecte les doublons dus aux insertions concurrentes et relit
- Étape Requête HTTP par connexion (worker, SSRF, mode test par défaut), éditeur, essai à blanc, ADR 0018
- Reprendre à l'étape en échec
- Expressions : calculs et fonctions ; feuille de route vers la parité et au-delà
- Expressions : noms de variables non ambigus (ruff)
- Test : fonction inconnue plutôt que abs (désormais valide)
- Connexions typées : clé d'API, Basic, OAuth2 (identifiants client et autorisation par une personne)
- Étape HTTP : pagination et nouvelles tentatives (Retry-After)
- Intégrations déclaratives : 24 services, palette, modèles de connexion, documentation générée
- Transformation de données sans code, fonctions de date, assistant : étapes HTTP et transformation
- Débogage : aperçu d'expressions, données épinglées, test d'étape, comparaison et filtres d'exécutions, export CSV
- Formulaires publics : page sans connexion, plafond horaire, piège anti-robots, gestion dans Déclencheurs
- Déclencheurs : interrogation d'un service (nouveaux éléments), workflow de succès
- Déclencheurs : webhooks de fournisseurs (GitHub, GitLab, Jira, jeton) et e-mail entrant IMAP
- ruff
- Transformation : scinder un texte, découper une liste en lots
- Exécuter jusqu'à une étape (brouillon, développement) ; correction proposée ouverte dans l'assistant de l'éditeur
- Variables : valeur par environnement
- Historique des versions : comparaison détaillée (étapes, champs, liaisons) y compris avec le brouillon
- Modèles de workflows : galerie (8 modèles), modèles du workspace, export / import JSON
- Commentaires sur les étapes
- Exploitation : tableau de bord, alertes, conservation des exécutions
- Attente reprise par formulaire public
- Connexion SigV4 et intégration S3, réponses XML
- Étape Requête SQL en lecture seule (PostgreSQL)
- ruff
- Évaluations de l'assistant dans l'interface d'administration
- MCP : serveur (workflows exposés comme outils) et intégration client
- Agent IA borné : outils du catalogue, validation humaine, mode test
- Workflows as code : export/import en lot et script de synchronisation
- Boucle « Répéter tant que » bornée
- Boucle pour chaque : cadence limitée
- Sauvegarde planifiée avec rotation, modèles systemd
- Suggestions de chemins dans les champs d'expression
- Requête SQL : MySQL / MariaDB en lecture seule
- Étape Annuaire LDAP en lecture seule
- Étape Fichier SFTP (liste, lecture, écriture, suppression)
- Déclencheur : dossier SFTP surveillé
- Étape HTTP : corps formulaire et multipart texte
- OIDC testé contre un vrai Keycloak ; en-tête Host avec port non standard
- Renouvellement des jetons avec chevauchement
- SCIM 2.0 Users : provisionnement et déprovisionnement
- Images d'exécution épinglées par empreinte (--write)
- Feuille de route : état final, OpenAPI et documentation des intégrations régénérés

## v0.2.0 — 2026-10-27

**Assistant IA fiable : tous les types d'étapes, contrôle et correction automatiques**

- Assistant IA de conversation dans l'éditeur de workflows
- Assistant IA de l'éditeur : création et reformulation de notes
- Éditeur : brouillons sans positions affichables, limite de demande à 6000 caractères
- Validation : étapes potentiellement ignorées, contrôles statiques des scripts, messages plus clairs
- Assistant IA : tous les types d'étapes, contrôle et correction automatiques, banc d'essai (T100-T102)

## v0.1.1 — 2026-10-26

**Journal des modifications et publication automatique des releases**

- Patch note automatique à chaque export public (journal des modifications, message de commit, release GitHub)
