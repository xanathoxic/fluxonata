# Plan de tests d'automatisation

## 1. Principe : des essais qui ne peuvent rien casser

Chaque test s'exécute dans un **espace de recette dédié**, sur l'environnement **Recette**, avec des **données fictives** et des
ressources **jetables ou simulées**. Ce qui le rend inoffensif ne dépend pas de la bonne volonté de celui qui teste : ce sont des
protections de la plate-forme.

| Protection | Effet sur les tests |
|---|---|
| Espace et environnement séparés | Les workflows de test n'existent que dans l'espace `tests-auto` (environnement Recette) ; la production exige une **promotion** relue par une autre personne : rien ici n'est promu. |
| Mode test par défaut | Les étapes *Requête HTTP*, *SQL*, *LDAP*, *SFTP* et *Agent IA* sont créées en **mode test** : sortie simulée, rien n'est envoyé. On ne passe en « Réel » que là où le scénario le dit, vers une ressource jetable. |
| Actions de démonstration | Les actions utilisées (`Statistiques de texte`, `Attente simulée`, `Envoyer une notification (simulé)`, `Transformation JSON`, `Créer un rapport`, `Appel HTTP (service de test local)`) ne contactent **aucun service réel** : la notification n'envoie rien, l'appel HTTP ne vise que le service de test local. |
| Bac à sable | Les scripts et actions tournent sans réseau, sans fichiers ni secrets, avec limites de temps et de mémoire. |
| Réseau fermé | Les scripts et les actions n'ont **aucun accès réseau** (seule l'action `Appel HTTP (service de test local)` atteint le service de test, rien d'autre). Les étapes *Requête HTTP*, *SQL*… ne vont que vers une **connexion déclarée** par un administrateur, jamais vers une adresse interne sauf hôte autorisé par l'opérateur (`ORCH_EGRESS_ALLOWLIST`, uniquement le temps de T05 et T06). Aucune connexion vers un service réel n'est créée dans ces tests. |
| Pas de secret dans un prompt | Aucun prompt, jeu d'essai ou fichier de ce répertoire ne contient de secret. Les mots de passe des ressources jetables sont générés au lancement et se saisissent dans une **connexion**, jamais dans un workflow. |
| L'IA ne publie pas | Tout ce que l'assistant produit est un **brouillon** ; un script écrit par l'IA doit être relu avant publication ; l'assistant n'active jamais l'auto-approbation et ne supprime jamais une approbation. |
| Approbations humaines | Les étapes d'approbation attendent une **personne** (ici `claire`, approbatrice de la démo) ; elles ne sont jamais auto-approuvées. |

**Règles du testeur** (à respecter même si une protection existe) :
1. Toujours vérifier le sélecteur d'environnement en haut de l'écran : **Recette**, jamais Production.
2. Ne jamais saisir de véritable adresse, compte, jeton ou mot de passe d'un service réel. Les « e-mails » des tests sont fictifs
   (`@exemple.test`).
3. Ne pas modifier `ORCH_EGRESS_ALLOWLIST` sauf pour les tests T05 et T06, et **le remettre à sa valeur d'origine** ensuite.
4. Un test qui « marche trop bien » (une étape réelle s'exécute alors qu'elle aurait dû être simulée) est un **échec** du test : arrêter et le signaler.

## 2. Prérequis

| Prérequis | Comment |
|---|---|
| Pile de développement lancée | `scripts/dev-up.sh` puis `scripts/doctor.sh` (le service de test local `testsvc` fait partie de la pile) |
| Utilisateurs | en démonstration (`dev-up.sh`) : espace `demo`, `alice` (concepteur), `bob` (lecteur), `claire` (approbatrice), `admin` ; mot de passe `demo-fluxonata`. **Sur une plate-forme vierge** : créer ces rôles à la main (concepteur, lecteur, approbatrice) dans l'espace de recette |
| Actions et skills | **à installer sur une plate-forme vierge** (voir §2 bis) : les scénarios appellent des actions du catalogue et deux skills IA qui n'existent pas encore |
| Espace de recette | Créer l'espace `tests-auto` (Plate-forme → Espaces) et y ajouter alice (concepteur) et claire (approbatrice), ou utiliser l'espace `demo` **tant que ce n'est pas une instance partagée** |
| Assistant IA activé | Administration → Assistant IA, avec un fournisseur configuré. **Modèle local recommandé** (mode *local strict* : aucune donnée ne quitte l'infrastructure). Budget quotidien suffisant (1 000 000 jetons par jour par défaut, modifiable dans Administration → Assistant IA) |
| Docker | pour les tests T05 et T06 (bases jetables) |

## 2 bis. Installer les actions et les skills (plate-forme vierge)

Sur la démonstration les actions existent déjà ; sur une plate-forme vierge, les importer **avant** de générer quoi que ce soit
(l'assistant ne réutilise que ce qui existe dans l'espace). Tout est dans [`ressources/`](ressources/) :

| Ressource | Contenu | Utilisée par |
|---|---|---|
| `ressources/paquet/` — paquet d'actions `tests-automatisation` 1.0.0 | `Statistiques de texte`, `Attente simulée`, `Envoyer une notification (simulé)`, `Transformation JSON`, `Créer un rapport (artefact)`, `Appel HTTP (service de test local)` — **aucun effet réel**, aucun secret, non signé | T01 à T09 |
| `ressources/skills/classer-message.json` | skill IA : classe un message, estime l'urgence, propose une réponse | T01 (variante IA), T10 |
| `ressources/skills/resumer-alerte.json` | skill IA : résume une alerte pour l'approbateur | T02 (variante IA) |

**Par le script** (jeton de service de l'espace de recette, droit de publier ; rien n'est publié ni lancé, relançable sans danger) :
```bash
export FLUXONATA_TOKEN='…'     # jamais en argument
python3 outils/installer_ressources.py --url http://127.0.0.1:5173 --workspace tests-auto --dry-run   # voir ce qui serait installé
python3 outils/installer_ressources.py --url http://127.0.0.1:5173 --workspace tests-auto
```
**Sans le script** : `python3 outils/construire_paquet.py` écrit l'archive ; importez-la dans Bibliothèques → Actions → **Paquets**
(aperçu, puis import) ; créez les skills dans Bibliothèques → **Skills IA** → Nouveau skill (contenu des fichiers `.json`).
Les skills n'appellent aucun modèle par eux-mêmes : ils servent quand un workflow a une **étape IA**, avec le fournisseur par défaut de l'espace.

L'action `Appel HTTP (service de test local)` suppose que le service de test (`testsvc`, présent dans les piles de développement et de production fournies) est démarré : `scripts/doctor.sh` le vérifie. Sinon T03 et T09 ne sont pas jouables (les autres tests le restent).

Vérification : les actions apparaissent dans Bibliothèques → Actions (contrat « ✔ Complet »), les skills dans Skills IA ; l'import est
couvert par un test automatique du dépôt (`backend/tests/test_examples_resources.py`).

## 3. Comment dérouler un test

1. Ouvrir le fichier du scénario.
2. **Workflows → Générer avec l'IA**, coller le *prompt* du scénario. L'assistant propose d'abord un **plan** : le relire, comparer avec « Plan attendu », corriger si besoin, puis **Valider et générer**.
3. Ouvrir le brouillon : l'**onglet Test** et la **validation** ne doivent rien signaler de bloquant ; relire (« J'ai relu ce code ») les scripts écrits par l'IA.
4. Publier le workflow **dans la Recette** et le lancer avec les **données d'essai** du scénario (ou enregistrer celles-ci comme *jeu de test* de l'action/workflow quand c'est possible).
5. Comparer avec « Résultat attendu » et cocher les « Vérifications ». Noter l'écart éventuel : *prompt trop vague*, *plan mal compris*, *défaut de la plate-forme*.
6. Appliquer le « Nettoyage » (supprimer ou déprécier les workflows de test, désactiver les déclencheurs, arrêter les ressources jetables).

Un test est **réussi** quand toutes ses vérifications sont vraies. Les tests sont indépendants : on peut en jouer un seul.

## 4. Catalogue des tests

| # | Test | Ce qu'on vérifie | Déclencheur | Ressources | Impact hors plate-forme |
|---|---|---|---|---|---|
| [T01](scenarios/T01-courriel-analyse.md) | **Réception d'un e-mail, analyse, décision** | extraction d'informations, classement, décision, réponse proposée | webhook signé (simule l'e-mail) | aucune | aucun |
| [T02](scenarios/T02-alerte-approbation.md) | **Alerte de supervision → approbation → rapport** | décision par gravité, **approbation humaine**, refus, rapport en artefact | webhook ou lancement manuel | aucune | aucun |
| [T03](scenarios/T03-service-http-erreurs.md) | **Appel du service de test, erreurs et reprise** | action réseau restreinte, branche d'erreur, nouvelle tentative, délai, chemin refusé | lancement manuel | service de test local | aucun (le service de test est interne à la pile) |
| [T04](scenarios/T04-lot-boucle.md) | **Traitement d'un lot avec un élément défectueux** | boucle bornée, parallélisme, tolérance à une erreur, agrégation | lancement manuel | aucune | aucun |
| [T05](scenarios/T05-base-de-donnees.md) | **Rapport depuis une base de données** | requête SQL en lecture seule, paramètres liés, transformation, rapport | planification (essai) | PostgreSQL jetable | aucun (base jetable, compte lecture seule) |
| [T06](scenarios/T06-vault-dynamique.md) | **Secrets externes et identifiants dynamiques** | Vault externe, bail temporaire, connexion sans mot de passe stocké | lancement manuel | Vault de développement + PostgreSQL jetable | aucun (conteneurs jetables) |
| [T07](scenarios/T07-agent-ia.md) | **Agent IA et outils simulés** | choix d'outils, validation humaine avant effet externe, plafond de tours | lancement manuel | modèle IA | aucun (outils simulés) |
| [T08](scenarios/T08-modifier-avec-assistant.md) | **Modifier un workflow en discutant** | proposition, application, annulation, jamais de suppression implicite | — | un workflow du test T03 | aucun |
| [T09](scenarios/T09-planification-sous-workflow.md) | **Planification et sous-workflow** | cron, fuseau, sous-workflow, désactivation | planification | aucune | aucun |
| [T10](scenarios/T10-garde-fous.md) | **Garde-fous (tests négatifs)** | injection de consigne dans une donnée, script qui tente une sortie réseau, secret dans un prompt, publication d'un script non relu | divers | aucune | aucun — **le test réussit quand la plate-forme refuse** |

**Ordre conseillé** : T03 → T01 → T02 → T04 → T08 → T09 → T10, puis T05 et T06 (qui demandent Docker), puis T07 (consomme le plus de jetons).

## 5. Données fictives communes

Les scénarios réutilisent ces valeurs, toutes inventées :

| Donnée | Valeur |
|---|---|
| Expéditeur d'e-mail | `lea.martin@exemple.test` |
| Destinataire / équipe | `equipe-support@exemple.test` |
| Hôtes | `serveur-app-01`, `serveur-bdd-02` (n'existent pas) |
| Service de test local | `/health`, `/echo?x=1` (répondent), `/introuvable` (renvoie 404) |

## 6. Ce que ces tests ne prouvent pas

- Ils vérifient le **comportement de la plate-forme** et la **qualité de ce que produit l'assistant** sur des demandes réalistes ;
  un modèle peut se tromper d'une exécution à l'autre : rejouer un test deux fois avant de conclure.
- Le courrier réel (IMAP), Slack, Jira, etc. ne sont **pas** contactés. Les intégrations restent validées contre un faux serveur
  (voir `docs/INTEGRATIONS.md`) : les essayer pour de vrai demande un compte de recette du service concerné, hors de ce répertoire.

## 7. État de vérification de ces scénarios

- **Vérifié à l'écriture** : le paquet d'actions et les skills s'installent sur un espace **vierge** (test automatique) ; les actions citées existent dans la démonstration (`Statistiques de texte`, `Attente simulée`, `Envoyer une notification (simulé)`, `Transformation JSON`, `Créer un rapport (artefact)`, `Appel HTTP (service de test local)`), le comportement du service de test (`/health`, `/echo`, 404), la signature de l'outil `envoyer_webhook.py` (même calcul que `docs/API.md`) et les valeurs attendues des requêtes de T05.
- **Non exécuté** : les prompts n'ont **pas** été rejoués de bout en bout avec un modèle lors de la rédaction. Les plans et les résultats attendus décrivent ce qu'une génération correcte doit produire ; un écart est une information (prompt à préciser ou défaut à corriger), à noter dans la fiche du test.
