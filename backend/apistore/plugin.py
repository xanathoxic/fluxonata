"""Manifeste de l'extension « API store » (contrat des extensions : docs/EXTENSIONS.md)."""

MANIFEST = {
    "id": "apistore",
    "name": "API store",
    "version": "0.1.0",
    "api": 1,
    "license": "Apache-2.0",
    "description": "Produits d'API regroupant des workflows, plans et quotas, souscriptions, clés et audit d'usage.",
    "menu": [
        {
            "id": "catalogue",
            "label": "API store",
            "path": "/",
            "group": "Intégrer",
            "icon": "puzzle",
            "permission": "view",
        },
    ],
    "ui": {"module": "index.js"},
}
