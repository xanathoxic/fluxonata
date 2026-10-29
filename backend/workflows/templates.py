"""Starter workflows: complete drafts that need only a connection and a few values. Every request is in test mode."""


def _n(nid, ntype, name, **kw):
    return {"id": nid, "type": ntype, "name": name, **kw}


def _http(nid, name, integration, operation, params, **kw):
    return _n(
        nid, "http", name, integration=integration, operation=operation, params=params, connection="", mode="test", **kw
    )


def _e(a, b, label=None):
    return {"from": a, "to": b, **({"label": label} if label else {})}


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


STR = {"type": "string"}
TEMPLATES = [
    {
        "slug": "notifier-slack",
        "name": "Notifier un canal Slack",
        "category": "Messagerie",
        "description": "Envoie un message dans un canal Slack à chaque lancement (à relier à un déclencheur ou à un autre workflow).",
        "requires": ["slack"],
        "graph": {
            "inputs": _schema(
                {
                    "message": {**STR, "title": "Message"},
                    "canal": {**STR, "title": "Canal", "default": "#exploitation"},
                },
                ["message"],
            ),
            "nodes": [
                _http(
                    "slack",
                    "Envoyer sur Slack",
                    "slack",
                    "post_message",
                    {"channel": "{{ inputs.canal }}", "text": "{{ inputs.message }}"},
                )
            ],
            "edges": [],
            "outputs": {},
        },
    },
    {
        "slug": "ticket-depuis-formulaire",
        "name": "Créer un ticket Jira depuis un formulaire",
        "category": "Tickets",
        "description": "Crée un ticket Jira avec le titre et la description saisis, puis prévient l'équipe sur Slack avec la clé du ticket. À associer à un formulaire public.",
        "requires": ["jira", "slack"],
        "graph": {
            "inputs": _schema(
                {"titre": {**STR, "title": "Titre", "minLength": 3}, "description": {**STR, "title": "Description"}},
                ["titre"],
            ),
            "nodes": [
                _http(
                    "jira",
                    "Créer le ticket",
                    "jira",
                    "create_issue",
                    {
                        "fields": {
                            "project": {"key": "OPS"},
                            "summary": "{{ inputs.titre }}",
                            "description": "{{ inputs.description }}",
                            "issuetype": {"name": "Task"},
                        }
                    },
                ),
                _http(
                    "slack",
                    "Prévenir l'équipe",
                    "slack",
                    "post_message",
                    {
                        "channel": "#exploitation",
                        "text": "Nouveau ticket {{ steps.jira.output.body.key }} : {{ inputs.titre }}",
                    },
                ),
            ],
            "edges": [_e("jira", "slack")],
            "outputs": {"ticket": "{{ steps.jira.output.body.key }}"},
        },
    },
    {
        "slug": "rapport-tickets-ouverts",
        "name": "Rapport quotidien des tickets GitHub ouverts",
        "category": "Code",
        "description": "Compte les tickets ouverts d'un dépôt et publie le résultat sur Slack. À planifier chaque matin.",
        "requires": ["github", "slack"],
        "graph": {
            "inputs": _schema(
                {"proprietaire": {**STR, "title": "Propriétaire du dépôt"}, "depot": {**STR, "title": "Dépôt"}},
                ["proprietaire", "depot"],
            ),
            "nodes": [
                _http(
                    "liste",
                    "Lister les tickets ouverts",
                    "github",
                    "list_issues",
                    {"owner": "{{ inputs.proprietaire }}", "repo": "{{ inputs.depot }}", "state": "open"},
                ),
                _n(
                    "total",
                    "transform",
                    "Garder les 10 premiers",
                    op="limit",
                    items="steps.liste.output.body",
                    count=10,
                ),
                _http(
                    "slack",
                    "Publier le rapport",
                    "slack",
                    "post_message",
                    {
                        "channel": "#exploitation",
                        "text": "{{ len(steps.liste.output.body) }} ticket(s) ouvert(s) sur {{ inputs.depot }}",
                    },
                ),
            ],
            "edges": [_e("liste", "total"), _e("total", "slack")],
            "outputs": {"ouverts": "{{ len(steps.liste.output.body) }}"},
        },
    },
    {
        "slug": "approbation-puis-deploiement",
        "name": "Approbation puis déploiement Jenkins",
        "category": "Intégration continue",
        "description": "Demande une approbation humaine puis lance un job Jenkins ; un refus arrête le workflow avec un message clair.",
        "requires": ["jenkins"],
        "graph": {
            "inputs": _schema(
                {"job": {**STR, "title": "Job Jenkins"}, "version": {**STR, "title": "Version à déployer"}},
                ["job", "version"],
            ),
            "nodes": [
                _n(
                    "accord",
                    "approval",
                    "Valider le déploiement",
                    title="Déployer {{ inputs.version }} ?",
                    instructions="Vérifiez la recette avant d'approuver.",
                ),
                _http(
                    "build",
                    "Lancer le déploiement",
                    "jenkins",
                    "build_with_parameters",
                    {"job": "{{ inputs.job }}", "parameters": {"VERSION": "{{ inputs.version }}"}},
                ),
                _n("refus", "throw", "Déploiement refusé", code="REFUSE", message="Le déploiement a été refusé."),
            ],
            "edges": [_e("accord", "build"), _e("accord", "refus", "error")],
            "outputs": {},
        },
    },
    {
        "slug": "surveillance-avec-astreinte",
        "name": "Vérifier un service et alerter l'astreinte",
        "category": "Supervision",
        "description": "Interroge l'adresse de santé d'un service ; si elle ne répond pas correctement, ouvre un incident PagerDuty.",
        "requires": ["pagerduty"],
        "graph": {
            "inputs": _schema({"service": {**STR, "title": "Nom du service"}}, ["service"]),
            "nodes": [
                _n(
                    "sante",
                    "http",
                    "Interroger /health",
                    connection="",
                    method="GET",
                    path="/health",
                    mode="test",
                    fail_on_status=False,
                ),
                _n("ok", "condition", "Le service répond ?", expr="steps.sante.output.ok == true"),
                _http(
                    "incident",
                    "Ouvrir un incident",
                    "pagerduty",
                    "create_incident",
                    {
                        "from_email": "astreinte@exemple.org",
                        "incident": {
                            "type": "incident",
                            "title": "Service indisponible : {{ inputs.service }}",
                            "service": {"id": "PXXXXXX", "type": "service_reference"},
                        },
                    },
                ),
            ],
            "edges": [_e("sante", "ok"), _e("ok", "incident", "false")],
            "outputs": {},
        },
    },
    {
        "slug": "courriels-vers-tickets",
        "name": "Transformer chaque courriel en ticket",
        "category": "Tickets",
        "description": "À associer à un déclencheur « boîte e-mail » : chaque nouveau courriel crée un ticket Jira (objet, expéditeur, texte).",
        "requires": ["jira"],
        "graph": {
            "inputs": _schema({"item": {"type": "object", "title": "Courriel reçu"}}, ["item"]),
            "nodes": [
                _http(
                    "jira",
                    "Créer le ticket",
                    "jira",
                    "create_issue",
                    {
                        "fields": {
                            "project": {"key": "OPS"},
                            "summary": "{{ inputs.item.subject }}",
                            "description": "De : {{ inputs.item.from }}\n\n{{ inputs.item.text }}",
                            "issuetype": {"name": "Task"},
                        }
                    },
                ),
            ],
            "edges": [],
            "outputs": {"ticket": "{{ steps.jira.output.body.key }}"},
        },
    },
    {
        "slug": "liste-vers-lots",
        "name": "Traiter une liste de valeurs par lots",
        "category": "Données",
        "description": "Scinde un texte (une valeur par ligne), dédoublonne, trie puis découpe en lots : la base de tout traitement de masse.",
        "requires": [],
        "graph": {
            "inputs": _schema({"valeurs": {**STR, "title": "Valeurs (une par ligne)"}}, ["valeurs"]),
            "nodes": [
                _n("scinde", "transform", "Une valeur par ligne", op="split", text="inputs.valeurs", separator="\\n"),
                _n("unique", "transform", "Retirer les doublons", op="unique", items="steps.scinde.output.items"),
                _n("tri", "transform", "Trier", op="sort", items="steps.unique.output.items", by="item", order="asc"),
                _n("lots", "transform", "Lots de 10", op="chunk", items="steps.tri.output.items", size=10),
            ],
            "edges": [_e("scinde", "unique"), _e("unique", "tri"), _e("tri", "lots")],
            "outputs": {"lots": "{{ steps.lots.output.count }}", "valeurs": "{{ len(steps.tri.output.items) }}"},
        },
    },
    {
        "slug": "evenement-github-vers-notion",
        "name": "Consigner les tickets GitHub dans Notion",
        "category": "Documentation",
        "description": "À associer à un webhook GitHub : chaque événement crée une page Notion avec le titre du ticket.",
        "requires": ["notion"],
        "graph": {
            "inputs": _schema(
                {
                    "payload": {"type": "object", "title": "Événement GitHub"},
                    "event": {**STR, "title": "Type d'événement"},
                },
                ["payload"],
            ),
            "nodes": [
                _http(
                    "notion",
                    "Créer la page",
                    "notion",
                    "create_page",
                    {
                        "parent": {"database_id": "REMPLACER"},
                        "properties": {
                            "Name": {
                                "title": [
                                    {"text": {"content": "{{ default(inputs.payload.issue.title, 'Événement') }}"}}
                                ]
                            }
                        },
                    },
                ),
            ],
            "edges": [],
            "outputs": {},
        },
    },
]
INDEX = {t["slug"]: t for t in TEMPLATES}


def summary(t: dict) -> dict:
    return {
        "slug": t["slug"],
        "name": t["name"],
        "category": t["category"],
        "description": t["description"],
        "requires": t["requires"],
        "steps": len(t["graph"]["nodes"]),
    }
