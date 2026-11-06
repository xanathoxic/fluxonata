# Connecter un assistant (MCP de conception)

Le **MCP de conception** laisse un assistant externe (Claude Code, ou tout client MCP compatible) lire ce que contient un espace et y **préparer** des
brouillons et des propositions : workflows, formulaires, actions, skills IA. Il ne publie, ne lance et n'active **rien** : une personne relit et décide.
Décision et limites : `docs/DECISIONS/0025-mcp-de-conception.md`. Le MCP d'**exécution** (lancer des workflows déjà publiés) reste séparé : `/mcp/`.

## 1. Créer un jeton de conception

> **Libre-service (recommandé).** Toute personne ayant un rôle dans l'espace ouvre **Mon compte → Assistant IA (MCP) → Générer un jeton**, choisit la durée (1 h à 7 jours) et, si son rôle le permet, l'autorisation de créer des brouillons. Une fenêtre affiche **une seule fois** la commande Claude Code (Linux) ou le bloc de configuration Claude Desktop. Le jeton agit au nom de la personne, avec ses droits ; la section ci-dessous (compte de service) reste utile pour une automatisation. Voir l'amendement du 2026-11-05 de l'ADR 0025.
Administration → jetons de service : créez un compte de service avec le rôle **Concepteur** puis un jeton aux portées **`view` et `edit` seulement**, avec une
échéance courte. Le serveur **refuse** (403) tout jeton qui porte aussi `run` ou `publish` sur ce profil. Le jeton se comporte comme la personne qui le détient :
même rôle, mêmes dossiers privés, aucun droit en plus.

## 2. Déclarer le serveur dans le client
Adresse : `https://<votre-hôte>/api/v1/workspaces/<espace>/mcp/design/` — `<espace>` est l'**identifiant** (slug) de l'espace, pas son nom affiché (l'espace « system » s'appelle `sys`) (HTTP en flux, en-tête `Authorization: Bearer <jeton>`). Avec Claude Code :

```bash
claude mcp add --transport http fluxonata-conception https://<votre-hôte>/api/v1/workspaces/<espace>/mcp/design/ --header "Authorization: Bearer <jeton>"
```

> La syntaxe de cette commande est celle de la documentation de Claude Code ; vérifiez-la avec `claude mcp add --help` sur votre version. **Cette procédure n'a pas été
> exécutée avec Claude Code lui-même** (voir « Ce qui a été vérifié »). En déploiement par rôles, cette route est servie par `api-ui`.

Exemple de demande : « Crée un workflow qui compte les voyelles d'un texte saisi dans un formulaire. Cherche d'abord ce qui existe, propose ce qui manque. »

## 2 bis. Parler à l'assistant sans connaître les outils
L'utilisateur n'a pas à nommer d'outil. Trois mécanismes, du plus guidé au plus discret :
- **Invites guidées** (commandes du client). Dans Claude Code elles se présentent comme des commandes `/mcp__fluxonata-conception__<invite>` (nom à vérifier avec `/` dans votre version) : `concevoir_workflow` (argument : le besoin en langage courant), `creer_formulaire`, `proposer_action`, `faire_le_point`. Chacune pose la demande dans la bonne méthode : lire le guide, chercher avant de créer, décider action ou script, ne rien publier, résumer ce qui reste à relire. Elles n'exécutent rien.
- **Consignes du serveur** : à la connexion, le serveur annonce « AVANT TOUTE CRÉATION, appelez `get_authoring_guide` » ; le client les transmet au modèle.
- **Descriptions d'outils et refus** : chaque outil de création renvoie vers `get_authoring_guide`, et un refus se termine par « format exact : get_authoring_guide ».
Une demande en langage courant, sans invite, suffit donc aussi ; les invites donnent seulement le chemin le plus sûr.

## 3. Ce que l'assistant peut faire
| Outil | Effet |
|---|---|
| `get_authoring_guide` | **à lire en premier** : format exact d'un graphe, d'un formulaire, d'une action et d'un skill, avec des exemples validés par le serveur, et la méthode |
| `search_actions`, `get_action`, `list_skills`, `get_skill`, `list_forms`, `get_form`, `list_workflows`, `get_workflow`, `list_connections`, `list_integrations` (catalogue statique : services, opérations, paramètres) | lecture ; une connexion ne sort que par son nom, son libellé et son type |
| `create_workflow`, `update_workflow` | brouillon d'un workflow (révision obligatoire) ; renvoie les problèmes à corriger |
| `create_form`, `update_form`, `attach_form` | brouillon d'un formulaire ; rattachement au brouillon d'un workflow (référence si publié, copie sinon) |
| `propose_action`, `propose_skill` | **proposition** : inutilisable tant qu'une personne ne l'a pas relue et activée |
| `validate_workflow` | analyse statique, sans rien écrire |
| `start_dry_run`, `get_dry_run` | **essai à blanc** d'un brouillon : ses scripts et ses décisions s'exécutent dans le bac à sable isolé (actions, IA, approbations simulées d'après leur contrat) ; jeux d'essai fournis par l'assistant (`inputs`, `expected`, `mocks`) ; asynchrone, exécuté par le worker |
| `start_action_test` | **test du code** d'une action proposée dans le même bac à sable (réseau coupé, aucun secret, limites plafonnées, 5 cas) ; l'action reste « proposée » |

Aucun outil pour publier, promouvoir, lancer, activer, approuver, supprimer, ni pour les connexions et secrets : il n'existe pas.
Tout ce qui est écrit porte l'étiquette `assistant-externe`. Quota : 60 écritures par heure et par personne (`ORCH_MCP_DESIGN_WRITES_PER_HOUR`).

## 3 bis. Le bac à sable : pourquoi l'assistant ne devine plus l'exécution
- Les essais sont des **travaux en file** exécutés par le **worker** (rôle `ai`), jamais par l'API. Le réglage « essai à blanc » de la politique de l'espace les active ou les coupe.
- L'essai d'un workflow **ne prouve pas** le code des actions (simulées) ni les règles métier ; le test d'une action prouve son code sur les cas fournis, pas son usage réel (réseau, secrets).
- Le test de code exige un **runner isolé** (conteneur). Avec `ORCH_RUNNER=subprocess` il est refusé, sauf `ORCH_MCP_ACTION_TEST_UNISOLATED=1` (développement seulement).
- Décision et garde-fous : amendement du 2026-11-05 de l'ADR 0025.

## 4. Relire ce que l'assistant a préparé
- **Actions et skills** : bibliothèque → panneau « Propositions d'un assistant » (Relire, Activer, Rejeter). Une proposition ne s'exécute pas, ne figure dans aucun workflow
  publié et n'apparaît pas dans le catalogue tant qu'elle n'est pas activée ; l'activation est réservée à une personne connectée (jamais un jeton).
- **Workflows et formulaires** : ce sont des brouillons ordinaires ; filtrez sur l'étiquette `assistant-externe`. Leur publication suit le parcours habituel.
- **Audit** : `mcp.design.initialize` (client annoncé), `mcp.design.call`, `mcp.design.write` (ce qui a été touché), `mcp.design.refused` (outil inexistant demandé, jeton trop puissant), `mcp.design.error` (appel en erreur : message tronqué, jamais le contenu envoyé).

## Ce qui a été vérifié, et ce qui ne l'a pas été
- **Session Claude Code réelle (v2.1.318, 2026-11-05)** : connecté (18 outils), il a cherché ce qui existait (9 appels de lecture), proposé une action, créé un formulaire et un workflow en brouillon, rattaché le formulaire, puis s'est arrêté en disant ce qu'une personne devait faire (activer, publier). Rien n'a été publié, lancé ni activé. Il a **deviné** les formats du graphe et du formulaire d'après les messages d'erreur (trois essais), ce qui a motivé `get_authoring_guide`.
- Deux défauts n'apparaissaient qu'avec ce client : l'adresse enregistrée **sans `/` final** (500, redirection de POST) et le GET `Accept: text/event-stream` répondu **406** au lieu de 405. Corrigés, avec tests.
- `scripts/mcp-design-smoke.sh` joue un assistant avec le **client officiel du SDK MCP** (Streamable HTTP) contre la pile de dev : espace jetable, jetons de 2 heures, 18 outils, puis contrôle en base que rien n'a été publié, lancé ni activé, et suppression de l'espace. Le client de l'inspecteur MCP officiel (`tools/list --strict`, `tools/call`) passe aussi.
- **Seconde session réelle, avec le guide** (demande complexe : formulaire à champs conditionnels, workflow à script, décision, approbation, aiguillage) : **8 appels, 0 erreur**, guide lu en premier, contre 22 appels et plusieurs erreurs sans guide ; formulaire valide, workflow de 11 étapes dont la seule erreur de validation est « action proposée à relire » ; il a signalé ce qu'il n'avait pas pu faire (pas de champ « fichier », TVA fixée, e-mail du validateur non utilisé) au lieu de l'inventer.
- **Non vérifié** : d'autres modèles que celui essayé ; ChatGPT ; OAuth (connecteurs distants de Claude.ai et ChatGPT : ADR suivant).
