# Journal des modifications

Changements notables de Fluxonata, du plus récent au plus ancien.

## v0.4.11 — 2026-11-06

**Fluxonata 0.4.11 : liste double (disponibles / sélectionnés, mise en ordre) pour les cases multiples et les contrôles « liste à cocher » des formulaires**

- Formulaires : liste double (disponibles / sélectionnés, mise en ordre) pour les cases multiples et les contrôles « liste à cocher »

## v0.4.10 — 2026-11-06

**Fluxonata 0.4.10 : les zones « une ligne par élément » du concepteur de formulaires acceptent la saisie (ajout de colonnes d'un tableau et d'options d'une liste)**

- Formulaires : zones « une ligne par élément » utilisables (colonnes d'un tableau, options d'une liste)

## v0.4.9 — 2026-11-06

**Fluxonata 0.4.9 : listes dynamiques de formulaire dépendantes d'autres champs et connexion choisie parmi une liste fermée, règles « lecture seule », champs masqués qui libèrent leur place, ETag exposé par l'étape HTTP**

- Formulaires : listes dépendantes d'autres champs (attente des champs lus, purge, chemin composé)
- Formulaires : la connexion d'une liste dynamique peut dépendre d'un champ, parmi une liste fermée
- Étape HTTP : l'ETag de la réponse est exposé (steps.<id>.output.headers.etag)
- Règles de formulaire : effet « lecture seule » et éditeur du « Quand » pour une condition simple
- Fixtures des règles : retour au format d'origine (un espace d'indentation)
- Formulaires : un champ masqué libère sa place, ceux du dessous remontent (colonne par colonne)
- Formulaires : « remplir le champ » s'applique aussi quand la même règle masque le champ

## v0.4.8 — 2026-11-06

**Fluxonata 0.4.8 : listes dynamiques de formulaire mises en forme depuis la réponse d'un service (objet, filtre, libellé calculé, tri, liste dépendante) dans une fenêtre à onglets, déclencheurs et listes dans le guide de l'assistant externe**

- Formulaires : mettre en forme la réponse d'un service pour les listes dynamiques (ADR 0026)
- MCP de conception : déclencheurs (list_triggers) et listes dynamiques de formulaire dans le guide
- Banc MCP de conception : 23 outils attendus (list_triggers, seul nom autorisé contenant « trigger »)
- Formulaires : liste dynamique d'un champ à choix simple dans une fenêtre à onglets

## v0.4.7 — 2026-11-06

**Fluxonata 0.4.7 : l'assistant externe (MCP de conception) connaît les étapes natives, expressions, intégrations et erreurs, et lit le catalogue d'intégrations**

- MCP de conception : étapes natives, expressions, intégrations et erreurs dans le guide
- Banc MCP de conception : 22 outils attendus (list_integrations ajouté)

## v0.4.6 — 2026-11-06

**Fluxonata 0.4.6 : l'éditeur ne montre plus la dernière exécution d'une étape (fuite possible de secrets), valeurs sensibles masquées dans les aperçus**

- Concepteur : retrait de « Dernière exécution » (fuite possible de secrets), masquage des valeurs sensibles dans les aperçus, curseur blanc de l'éditeur de code

## v0.4.5 — 2026-11-05

**Fluxonata 0.4.5 : constructeur de formulaires et contrôles personnalisés, étape e-mail, refonte UX, lots d'architecture, assistant externe (MCP de conception) avec jetons personnels**

- Étape « Envoyer un e-mail » (SMTP) : connexion TLS obligatoire, mode test par défaut, jamais rejouée, tests contre un serveur local
- Relevé e-mail : peu de commandes IMAP (en-têtes en un seul FETCH, corps par lots de 10 limités à 512 Ko), échéance par relevé, délai dépassé signalé clairement
- Constructeur de formulaires (1/6) : définition JSON versionnée, liaison aux entrées du workflow, visibilité et validation côté serveur, publication
- Constructeur de formulaires (2/6) : listes dynamiques exécutées par le worker (connexion du coffre, core.netsec, cache 60 s, file bornée, message neutre en cas d'échec)
- Constructeur de formulaires (3/6) : formulaires dans les paquets (export de la définition publiée, import désactivé et non publié, liaison au workflow vérifiée, paquets piégés refusés)
- Constructeur de formulaires (4/6) : éditeur visuel (sections, 11 types de champ, règles d'affichage, listes dynamiques, aperçu, brouillon/publication), page publique, export de formulaires dans les paquets ; E2E + axe
- Constructeur de formulaires (5/6) : ADR 0020, tâche T155, journal, guide utilisateur, OpenAPI, état du projet
- Concepteur de formulaires pour le formulaire du catalogue : input_form dans le graphe du workflow (validé à la publication), éditeur partagé dans l'onglet « Formulaire d'entrée », catalogue et page Lancer, listes dynamiques authentifiées par le worker ; tests et E2E
- Constructeur de formulaires : icônes partout (sélecteur Simple / Par étapes / Concepteur, types de champ, actions, onglets, lignes de tableau), confirmation avant de quitter le concepteur, correction du débordement du panneau de propriétés
- Bibliothèque de formulaires (phase 1) : entrée « Formulaires » du menu, formulaires et modèles versionnés (dossiers, étiquettes, publication), rattachement par version figée aux workflows et aux formulaires publics, paquets, migration des définitions existantes ; correction du contraste du badge du filtre de catalogue
- Page d'un formulaire : onglet de l'éditeur renommé (plus de doublon), ligne « Entrées de référence » mise en page
- Formulaires (phase 2, serveur) : placement sur grille de 12 colonnes (validation, ordre de lecture ligne puis colonne), blocs de contenu (titre, texte, séparateur), style fermé avec couleur d'accent contrôlée (contraste)
- Formulaires (phase 2) : mise en page libre sur grille de 12 colonnes (glisser-déposer, clavier, saisie numérique), blocs de contenu, style du formulaire avec aperçu, rendu sur la page publique, le catalogue et la page Lancer ; tests unitaires et E2E
- Formulaires : CSS personnalisé (serveur) — sous-ensemble strict analysé et réécrit sous forme canonique, cantonné à la racine du formulaire (pas d'url(), @import, position fixe, !important ni échappement), point de contrôle en direct
- Éditeur de formulaires : barre d'outils à icônes (annuler/rétablir, Ajouter, onglets, pastille de validité), palette avec recherche, style en rangées d'icônes, onglet CSS personnalisé avec aperçu et contrôle ligne par ligne ; tests et E2E
- Style des formulaires : barre horizontale de petites icônes, chacune ouvrant un menu contextuel d'options en icônes seules (infobulles, clavier, Échap)
- Éditeur de formulaires : l'éditeur occupe la hauteur de la fenêtre, seul le formulaire défile (barre d'outils, palette, propriétés, menu de placement et barre de style toujours accessibles) ; en-tête compact à actions en icônes ; test E2E de défilement
- Concepteur CSS sans code : cibles × états × écrans, sélection par clic dans l'aperçu, inspecteur à icônes (typographie, couleurs, bordure, espacement, taille, ombres, disposition, effets), thèmes, menu des règles, CSS généré en direct et passage au code ; Échap ferme les menus même sans focus ; tests unitaires et E2E
- Concepteur CSS : une session expirée ou une panne réseau n'est plus présentée comme « CSS refusé » (message clair, réglages conservés dans la page, bouton Réessayer) ; test E2E
- Menus contextuels : ils se replient vers la gauche près du bord droit au lieu de faire défiler la page horizontalement (menu des thèmes) ; test qui reproduit le débordement
- Formulaires : un champ remplit sa case (la limite de 560 px de .field ne s'applique plus aux formulaires conçus) ; test pleine largeur et demi-largeur
- Formulaires : hauteur des champs (cartouche et rendu à la hauteur réelle, un texte long vaut deux champs courts, poignée du bas, Maj + flèches) ; panneau de placement numérique retiré ; tests serveur, unitaires et E2E
- Formulaires : moteur de règles (serveur) — conditions riches (égal, contient, commence/finit par, dans la liste, vide, comparaisons, ignorer la casse, comparaison à un autre champ, groupes ET/OU imbriqués) et effets (afficher, masquer, obligatoire, facultatif, textes, limiter les choix) appliqués à la soumission ; cas d'évaluation partagés
- Formulaires : éditeur de conditions en fenêtre centrée ; une règle lit n'importe quel autre champ (côte à côte, dessous) avec refus des boucles et évaluation dans l'ordre des dépendances ; tests serveur, unitaires et E2E
- Formulaires : effet « Remplir le champ » dans les règles (valeur définie dans la règle, champs insérables, imposée par le serveur) ; tests serveur, unitaires et E2E
- Formulaires : valeur composée dans les conditions (plusieurs champs concaténés comme opérande de gauche) ; tests serveur, unitaires, cas partagés et E2E
- Formulaires : champs calculés (formules entre champs, langage fermé identique serveur et navigateur, valeur imposée par le serveur) ; éditeur dans la fenêtre des conditions (onglet Calcul) ; tests serveur, unitaires, cas partagés et E2E
- Formulaires : le bouton « Définir un calcul » est retiré des propriétés ; le calcul se résume et s'ouvre depuis « Conditions, règles et calcul »
- Contrôles personnalisés : bibliothèque versionnée (groupe, lignes répétées, liste à cocher avec détails), concepteur à icônes, palette de l'éditeur de formulaires, validation serveur par ligne, count()/sum_col() ; « Formulaires » et « Contrôles » rangés dans Bibliothèques ; tests serveur, unitaires, cas partagés et E2E
- Contrôles : choix à détails (liste déroulante ou radio, champs propres à chaque option), détails par case, affichage en tableau avec totaux, contrôles imbriqués sur 3 niveaux ; la clé d'un champ renommée ne le désélectionne plus ; tests serveur, unitaires, cas partagés et E2E
- Contrôles : menu déroulant avec recherche (cases par groupes, options désactivées, texte d'invite) pour la liste à cocher et le choix ; tests serveur, cas partagés et E2E
- Contrôles : le concepteur s'ouvre aussi pour un contrôle sans sous-champs (page blanche corrigée)
- Contrôles : fenêtre « Options du contrôle » (arborescence groupes → options, ajout en masse, propriétés et détails de l'option) à la place de l'éditeur étroit des réglages ; tests unitaires et E2E
- Contrôles : réglages rangés en cartes (contrôle et type compacts, options, affichage), bouton des options visible sans défiler ; description, icône et étiquettes repliées
- Contrôles lus depuis un service : options à la volée (groupes, options désactivées, paramètres tirés du formulaire) et valeur préremplie validée par le contrôle ; appels par le worker, essai sur une réponse d'exemple ; correctif des tests d'isolation form-lookup ; tests serveur, unitaires, cas partagés et E2E
- Démo : API fictive locale pour les contrôles lus depuis un service (lancée par dev-up.sh)
- Plate-forme → Réseau : adresses internes autorisables depuis l'interface (administrateurs de plate-forme, confirmation, audit, jamais métadonnées ni lien local) en plus de ORCH_EGRESS_ALLOWLIST ; tests serveur et E2E
- La lecture d'un service appartient au formulaire, pas au contrôle : source et préremplissage passent au champ du formulaire (le contrôle déclare seulement « options fournies par le formulaire »), essai sur réponse d'exemple dans les propriétés du champ, options d'exemple dans l'aperçu du contrôle ; tests serveur, cas partagés et E2E
- Options d'un même groupe rapprochées quand options fixes et options d'un service se mélangent ; démo complète d'un formulaire lu depuis une API (workflow demo-marques-api)
- Données depuis un service : édition dans une fenêtre centrée (onglets Options et Valeur préremplie, appliquée à la validation) comme les conditions ; résumé dans les propriétés du champ ; E2E
- Données depuis un service : champs d'une même rangée alignés par le bas (libellés sur deux lignes)
- Formulaire : plus de bouton de rechargement ; le chargement d'un service est automatique ou piloté par « Conditions et règles » (effet « Charger les options / la valeur préremplie ») ; tests serveur, cas partagés, unitaires et E2E
- Fenêtre « Service » plus ergonomique : sections à icônes (Appel, Dans la réponse, Essai), libellés courts, aide dans des info-bulles, tout sur un écran
- Concepteur de contrôle refait : sections (Général, Type, Options/Lignes, Champs) une à la fois, options éditées en place, cartes de choix à radios natifs, interrupteurs et info-bulles, en-tête à boutons libellés ; E2E et accessibilité sur chaque section
- Type d'un contrôle verrouillé après la création : refus côté serveur, section « Type » remplacée par un rappel en lecture seule dans Général, avertissement dans la fenêtre de création
- Éditeur de tableau pour les lignes répétées en mode tableau : bandeau de colonnes, réglages de la colonne, aperçu réel, retour à l'éditeur complet ; briques communes du concepteur extraites ; E2E et accessibilité
- Liste de choix : un seul type à la création (un ou plusieurs choix), modèle d'option dessiné une fois (racine option → champs enfants), options réduites à valeur/libellé/groupe ; anciens champs propres signalés et retirables ; E2E et accessibilité
- Concepteur de contrôle : « Liste modifiable par le formulaire » et « Options fournies par le formulaire » s'excluent, et la seconde reste décochable pour corriger un ancien brouillon invalide
- Onglet Test d'un contrôle : options et valeur envoyées en JSON (lecture et vérification par le serveur), contrôle rempli avec ses enfants, exemples construits depuis la description ; E2E et accessibilité
- Options lues d'un service : chacune peut porter les valeurs de ses champs enfants (nom, prénom…), pré-remplies quand on la choisit ; onglet Test et exemples adaptés ; tests serveur et E2E
- Onglet Test : options envoyables même si le contrôle ne les reçoit pas encore, avertissement quand le modèle n'a aucun champ enfant, clés du JSON sans champ correspondant nommées ; E2E
- Menu déroulant avec recherche : un clic sur le texte d'une option la coche (le menu ne se refermait plus avant le clic) ; E2E sur un contrôle à options reçues avec champs enfants pré-remplis
- Section Documentation d'un contrôle : schéma JSON, exemple, champs, options reçues et interactions avec le formulaire et le workflow, générés depuis le brouillon en direct ; vitest, E2E et accessibilité
- Documentation du contrôle en volet latéral masquable (grands écrans), mise à jour pendant la conception ; tableaux de la documentation focalisables ; E2E
- Documentation en volet : plus de gros bouton dans le menu de gauche, une petite icône discrète en haut à droite pour la réafficher
- Lignes répétées en tableau : les champs d'une ligne restent en colonnes sous les en-têtes, même dans une zone étroite ; E2E à deux largeurs
- Correspondance des entrées du workflow et des champs du formulaire (appliquée au lancement et aux formulaires publics, figée à la publication) ; onglet Formulaire d'entrée refait : bandeau, onglets Correspondance / Aperçu, fenêtre de choix ; tests serveur, vitest et E2E
- UX — lot 0 : mesures de base (textes < 12 px, cibles < 24 px, saut de la barre d'onglets, largeur du canevas), enregistrées sans être bloquantes
- UX — P-2 : commande purge_e2e pour lister puis supprimer, sur demande, les objets laissés par les tests de bout en bout
- UX — lot 1 : fondations visuelles (jetons de taille et de couleur, espacement des mots, pastilles réservées au statut, étiquettes monochromes, icônes SVG, rôles en français)
- UX — lot 2 : composants partagés (PageTabs unique, SegmentedControl, Tooltip, ConfirmDialog unique, Toast) ; l'annulation d'une exécution passe par la fenêtre de confirmation
- UX — lot 3 : gabarit d'éditeur commun (EditorShell, StatusLine, onglets sans saut), enregistrement automatique du formulaire et du contrôle (useAutosave), onglet Utilisation du contrôle, nomenclature des onglets
- UX — cohérence des en-têtes : fiches Action et Skill IA dans le même gabarit que formulaire, contrôle et workflow (action principale puis « Plus » à droite)
- UX — boutons d'action des listes alignés sur la liste des actions (dans la barre de recherche) ; arborescence de dossiers pour les contrôles
- Rôle Catalogue : groupes d'utilisateurs et droits par automatisation, lancement depuis le catalogue, page Mes exécutions limitée (entrées masquées, progression), relance pré-remplie ; menu et routes réduits
- Déconnexion : la page de connexion s'affiche aussitôt, sans recharger (la requête « me » reste attachée à l'écran)
- E2E catalogue : tolère les exécutions d'un essai précédent
- UX — lot 4 (partie) : canevas d'abord (palette repliée à la sélection, inspecteur 340 px), fenêtre de publication du workflow, seuils bloquants dans les mesures
- UX — lot 5 (partie) : Apparence (Simple / Avancé), fenêtre de publication du formulaire avec changements et utilisation, aperçu seul sur téléphone
- UX — lot 5 (suite) : inspecteur en groupes repliables, barre d'outils de placement à place fixe (aucun décalage à la sélection), Déplacer / Redimensionner / Supprimer séparés
- UX — lot 6 (partie) : Aperçu & test avec mode développeur, colonne Utilisation des contrôles, ponctuation
- UX — lot 7 (partie) : menu par intention, recherche globale Ctrl+K, dates lisibles
- UX — lot 8 : en-tête mobile sur une ligne, espace et environnement dans le tiroir, liseré d'environnement, arbre de dossiers replié, tableaux en cartes, KPI sur 2 colonnes
- UX — reprise des lots 4 à 7 : connexions (Tester / Plus), accueil réordonné et guide masquable, cartes de catalogue homogènes, aperçu du formulaire en largeur, Début/Fin étiquetés
- Architecture — lot 1 : garde-fous de CI (graphe d'imports, migrations, contrat d'API, compatibilité)
- Architecture — lot 2 : rôles de déploiement et privilèges par rôle
- Architecture — lot 3 : moteur et orchestrateur (réveils, équité, arrêt propre)
- Architecture — lot 4 : exécution par agents et stockage S3 des artefacts
- Architecture — lot 5 : requêtes N+1 des listes et livraison atomique des webhooks
- Architecture — lot 6 : exploitation (PITR, mises à jour, bancs) et documentation
- AR8.1 — pagination plafonnée à 500 et lecture complète des listes par pages de 500
- Exécutions — page en onglets, inspecteur de variables et agencement automatique
- État « proposed » pour les actions et les skills IA, et panneau Propositions
- MCP de conception : outils de lecture et de brouillon, essai à blanc et test d'action (ADR 0025)
- Jetons personnels en libre-service pour connecter un assistant (Mon compte)
- Interface — retouches des connexions, déclencheurs, onglet Git et éditeur de schéma
- Test du refus d'un secret : motif de clé privée scindé pour le scanner de secrets

## v0.4.4 — 2026-10-30

**Fluxonata 0.4.4 : paramètres liste et objet saisis en JSON dans l'éditeur, texte JSON déjà enregistré interprété comme liste**

- Paramètres liste/objet d'une action : saisis en JSON dans l'éditeur, texte JSON déjà enregistré interprété comme liste, erreur claire à la validation

## v0.4.3 — 2026-10-29

**Fluxonata 0.4.3 : correctifs IMAP et réseau (IPv4 d'abord), essai à blanc de l'assistant d'édition exécuté par le worker, suivi des jobs IA sans limitation**

- Issues : IMAP (attribut ssl_context, erreurs distinguées, serveur TLS de test), adresses IPv4 d'abord et essayées une à une, essai à blanc de l'assistant d'édition exécuté par le worker (job), cause du bac à sable indisponible affichée
- Assistant IA : le suivi d'un job n'est plus limité comme un appel au modèle (message « Requête ralentie » pendant une modification)

## 2026-10-29

**Documentation : mise à niveau d'une installation de production (Docker Compose) pas à pas**

- Documentation : mise à niveau d'une installation de production (Docker Compose) pas à pas, sauvegarde, vérification, retour arrière

## v0.4.2 — 2026-10-29

**Fluxonata 0.4.2 : assistants IA de création d'actions et de skills, tests d'automatisation encadrés**

- Assistants IA de création d'actions et de skills : proposition contrôlée, révision, relecture obligatoire côté serveur
- E2E : la fiche d'une action a des onglets (Versions)

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
