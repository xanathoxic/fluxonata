"""Invites guidées du MCP de conception (ADR 0025) : l'utilisateur n'a pas à connaître le nom d'un outil.

Dans Claude Code elles apparaissent comme des commandes (`/mcp__<serveur>__<invite>`) ; elles ne font RIEN toutes seules : elles posent
la demande de la personne dans la bonne méthode (lire le guide, chercher avant de créer, décider action ou script, ne rien publier,
résumer ce qui reste à relire). Aucune donnée n'est lue ni écrite par ce module."""

MAX_NEED = 4000

METHOD = (
    "Méthode à suivre :\n"
    "1. Appelle get_authoring_guide (formats exacts et méthode).\n"
    "2. Cherche ce qui existe déjà (search_actions, list_skills, list_forms, list_workflows) et RÉUTILISE avant de créer.\n"
    "3. Service externe, base, annuaire, fichier distant, e-mail : étape native (`http`, `sql`, `ldap`, `sftp`, `smtp`) avec une connexion de list_connections (pour Slack, Jira, GitHub… : list_integrations) ; prévois la branche `error` des appels réseau ; une action ou un script n'a AUCUN réseau. Pour le reste, décide action réutilisable ou script (rubrique « decision » du guide : tiens compte de `used_by` et des `hints`).\n"
    "4. Crée les brouillons (formulaire, puis workflow), corrige les problèmes renvoyés, rattache le formulaire.\n"
    "4 bis. ESSAIE : start_action_test sur le code de chaque action que tu proposes, puis start_dry_run sur le workflow (un jeu d'essai réaliste par cas métier important) ; lis get_dry_run et corrige jusqu'à zéro problème.\n"
    "5. Ne publie, ne lance et n'active rien : tu ne le peux pas, une personne le fera après relecture.\n"
    "6. Termine par un récapitulatif court : ce qui existait et que tu as réutilisé ; ce que tu as créé et pourquoi une action plutôt qu'un script ; "
    "ce qu'une personne doit relire, activer ou publier ; tes hypothèses (règles métier, taux, seuils : ne les invente pas, demande-les si tu peux, sinon liste-les) ; ce que l'essai a couvert et ce qu'il ne couvre pas."
)


def _need(args: dict) -> str:
    value = args.get("besoin")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("L'argument « besoin » est obligatoire : décrivez ce que vous voulez automatiser.")
    return value.strip()[:MAX_NEED]


PROMPTS = {
    "concevoir_workflow": {
        "description": "Concevoir un workflow complet (formulaire, étapes, actions manquantes) à partir d'un besoin, en brouillon.",
        "arguments": [
            {"name": "besoin", "description": "Ce que vous voulez automatiser, en langage courant.", "required": True}
        ],
        "build": lambda a: (
            f"Je veux automatiser le besoin suivant :\n\n{_need(a)}\n\nConçois le workflow dans cet espace.\n\n{METHOD}"
        ),
    },
    "creer_formulaire": {
        "description": "Créer un formulaire (champs, listes, conditions) en brouillon.",
        "arguments": [
            {
                "name": "besoin",
                "description": "Les informations à collecter et les règles (champs obligatoires, conditions).",
                "required": True,
            }
        ],
        "build": lambda a: (
            f"Je veux un formulaire pour ceci :\n\n{_need(a)}\n\nLis d'abord get_authoring_guide (topic=form), regarde list_forms pour ne pas dupliquer un formulaire existant, "
            "puis crée-le en brouillon avec create_form. Ne publie rien. Termine en résumant les champs et les conditions, et ce qu'une personne doit relire."
        ),
    },
    "proposer_action": {
        "description": "Proposer une action réutilisable (code et contrat) après avoir vérifié qu'elle n'existe pas.",
        "arguments": [{"name": "besoin", "description": "Ce que l'action doit faire.", "required": True}],
        "build": lambda a: (
            f"J'ai besoin d'une action pour ceci :\n\n{_need(a)}\n\nLis d'abord get_authoring_guide (topic=decision puis topic=action). Cherche avec search_actions : si une action existante convient, dis-le et "
            "n'en crée pas. Sinon propose-la avec propose_action (contrat complet), sans rien activer, et TESTE son code avec start_action_test (cas nominal, limite, erreur) jusqu'à ce qu'il passe. Termine en expliquant pourquoi une action plutôt qu'un script."
        ),
    },
    "faire_le_point": {
        "description": "Faire le point sur ce que les assistants ont préparé : ce qui attend une relecture ou une activation.",
        "arguments": [],
        "build": lambda a: (
            "Fais le point sur ce que des assistants ont préparé dans cet espace (étiquette « assistant-externe ») : liste les workflows, formulaires, actions et skills concernés "
            "(list_workflows, list_forms, search_actions, list_skills), signale ce qui attend une relecture ou une activation et les problèmes de validation restants. Ne modifie rien."
        ),
    },
}


def list_prompts() -> list[dict]:
    return [{"name": n, "description": p["description"], "arguments": p["arguments"]} for n, p in PROMPTS.items()]


def get_prompt(name, args) -> dict:
    """Le message à proposer au modèle. Lève ValueError (argument manquant) ou KeyError (invite inconnue)."""
    prompt = PROMPTS[name]
    text = prompt["build"](args if isinstance(args, dict) else {})
    return {
        "description": prompt["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }
