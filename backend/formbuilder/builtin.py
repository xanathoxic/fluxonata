"""Starter templates shipped with the platform. A template is a definition to copy into a form of the library; the keys
are generic and meant to be re-bound to the inputs of the workflow it is attached to."""


def _f(key, label, type="text", **kw):
    return {"key": key, "label": label, "type": type, **kw}


def _opts(*pairs):
    return [{"value": v, "label": label} for v, label in pairs]


TEMPLATES = [
    {
        "id": "contact",
        "name": "Demande de contact",
        "description": "Nom, adresse e-mail, sujet et message : le formulaire de base d'une demande.",
        "definition": {
            "version": 1,
            "sections": [
                {
                    "id": "s1",
                    "title": "Votre demande",
                    "fields": [
                        _f("nom", "Nom", required=True),
                        _f("email", "Adresse e-mail", "email", required=True),
                        _f("sujet", "Sujet", required=True),
                        _f("message", "Message", "textarea", required=True),
                    ],
                }
            ],
        },
    },
    {
        "id": "incident",
        "name": "Déclaration d'incident",
        "description": "Gravité, description et, si critique, impact : le champ d'impact n'apparaît que pour une gravité critique.",
        "definition": {
            "version": 1,
            "sections": [
                {
                    "id": "s1",
                    "title": "Incident",
                    "fields": [
                        _f("titre", "Titre", required=True),
                        _f(
                            "gravite",
                            "Gravité",
                            "radio",
                            required=True,
                            options=_opts(("mineure", "Mineure"), ("majeure", "Majeure"), ("critique", "Critique")),
                        ),
                        _f(
                            "impact",
                            "Impact sur les utilisateurs",
                            "textarea",
                            visible_if="values.gravite == 'critique'",
                        ),
                        _f("description", "Description", "textarea", required=True),
                        _f("date", "Date de constatation", "date"),
                    ],
                }
            ],
        },
    },
    {
        "id": "acces",
        "name": "Demande d'accès",
        "description": "Application, niveau d'accès demandé et justification.",
        "definition": {
            "version": 1,
            "sections": [
                {
                    "id": "s1",
                    "title": "Accès demandé",
                    "fields": [
                        _f(
                            "application",
                            "Application",
                            "select",
                            required=True,
                            options=_opts(("crm", "CRM"), ("erp", "ERP"), ("wiki", "Wiki")),
                        ),
                        _f(
                            "niveau",
                            "Niveau d'accès",
                            "radio",
                            required=True,
                            options=_opts(
                                ("lecture", "Lecture"), ("ecriture", "Écriture"), ("admin", "Administration")
                            ),
                        ),
                        _f("justification", "Justification", "textarea", required=True),
                        _f("duree", "Durée (jours)", "number", min=1, max=365),
                    ],
                }
            ],
        },
    },
    {
        "id": "commande",
        "name": "Bon de commande",
        "description": "Lignes de commande dans un tableau et une adresse de livraison.",
        "definition": {
            "version": 1,
            "sections": [
                {
                    "id": "s1",
                    "title": "Commande",
                    "fields": [
                        _f(
                            "lignes",
                            "Lignes",
                            "datagrid",
                            required=True,
                            columns=[
                                {"key": "reference", "label": "Référence", "type": "text"},
                                {"key": "quantite", "label": "Quantité", "type": "number"},
                            ],
                        ),
                        _f("livraison", "Adresse de livraison", "textarea"),
                    ],
                }
            ],
        },
    },
]
