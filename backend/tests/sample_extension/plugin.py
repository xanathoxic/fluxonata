MANIFEST = {
    "id": "exemple",
    "name": "Extension d'exemple",
    "version": "1.0.0",
    "api": 1,
    "license": "Apache-2.0",
    "description": "Pour les tests.",
    "menu": [
        {
            "id": "accueil",
            "label": "Exemple",
            "path": "/",
            "group": "Utiliser",
            "icon": "puzzle",
            "permission": "view",
        },
        {
            "id": "reglages",
            "label": "Réglages exemple",
            "path": "/reglages",
            "group": "Superviser",
            "permission": "administer",
        },
    ],
    "ui": {"module": "index.js"},
}
