# Extensions

Une **extension** est un module additionnel que l'on branche sur Fluxonata, ou non, **sans toucher au code de la plateforme**. Elle peut ajouter des entrées
au menu, une page d'interface et des routes d'API. Le cœur ne dépend d'aucune extension : sans elle, rien ne change ; une extension absente, refusée ou en panne
ne gêne jamais le démarrage ni le reste de l'application.

## Ce qu'une extension est, et ce qu'elle n'est pas

- C'est un **paquet Python** (une application Django) **installé par l'administrateur** sur le serveur, comme n'importe quelle dépendance. Il n'y a **aucun téléversement
  depuis l'interface** : une extension s'exécute dans le processus de l'API avec les mêmes accès que lui, elle doit donc être relue et installée comme du code de confiance.
- Elle est activée en la nommant dans `ORCH_EXTENSIONS` (liste séparée par des virgules ; vide par défaut). Un paquet introuvable, un manifeste fautif ou un doublon
  d'identifiant est **refusé avec sa raison** (visible des administrateurs de l'espace) ; le démarrage continue.
- Toute **autorisation reste côté serveur** : le menu n'affiche que ce que le rôle de la personne permet, et les routes de l'extension appliquent les mêmes droits que le reste.

## Structure d'un paquet

```
mon_extension/
  __init__.py
  plugin.py        # MANIFEST (obligatoire)
  urls.py          # routes d'API (facultatif), servies sous /api/v1/plugins/<id>/
  models.py        # tables propres, migrations propres (facultatif)
  ui/index.js      # page d'interface (facultatif)
```

## Le manifeste (`plugin.py`)

```python
MANIFEST = {
    "id": "mon-extension",          # minuscules, chiffres, tirets ; 2 à 40 caractères
    "name": "Mon extension",
    "version": "1.0.0",
    "api": 1,                        # contrat visé ; une autre valeur est refusée
    "license": "Apache-2.0",
    "description": "Ce qu'elle apporte.",
    "menu": [
        {"id": "accueil", "label": "Mon menu", "path": "/", "group": "Intégrer",
         "icon": "puzzle", "permission": "view"},
    ],
    "ui": {"module": "index.js"},   # fichier du dossier ui/ ; requis si le menu n'est pas vide
}
```

- `group` : un des groupes du menu de l'application (`Utiliser`, `Concevoir`, `Intégrer`, `Superviser`).
- `permission` : le droit d'espace nécessaire pour voir l'entrée (`view`, `edit`, `publish`, `run`, `approve`, `manage_connections`, `administer`, `catalog`).
- Tout champ inconnu, un chemin avec `..`, un fichier `ui` qui n'est pas un `.js` du paquet : manifeste refusé.

## La page d'interface

Le module `ui/index.js` est servi par la plateforme (même origine, donc compatible avec la politique de sécurité du navigateur) et chargé quand la personne ouvre l'entrée de menu
(`/x/<id>/…`). Il exporte **une fonction par défaut** :

```js
export default function mount(el, host) {
  el.textContent = `Bonjour ${host.user.username}, espace ${host.workspace.name}`;
  return () => { /* nettoyage facultatif */ };
}
```

`host` est **le seul accès** que l'interface prête à l'extension : `apiVersion`, `workspace` (`slug`, `name`), `user` (`username`), `path` (chemin demandé dans l'extension),
`api(chemin, { method, json })` (appelle l'API de la plateforme avec la session de la personne) et `navigate(adresse)`. Un module qui ne se charge pas, ou qui lève une erreur,
affiche un message dans son cadre sans casser le reste de la page.

## Routes d'API

Si le paquet a un `urls.py`, il est monté sous `/api/v1/plugins/<id>/`, **toujours dans le rôle privé `ui`** : une extension n'ouvre aucune route publique (webhooks, formulaires publics).
Utilisez `tenancy.permissions.get_workspace(request.user, espace, droit)` pour contrôler l'accès, comme le reste de l'API.

## Interfaces

- `GET /api/v1/workspaces/{espace}/extensions/` : les extensions et entrées de menu **permises à la personne** (et, pour les administrateurs, les extensions refusées avec la raison).
- `GET /api/v1/workspaces/{espace}/extensions/{id}/ui/{fichier}` : un fichier `.js`, `.css` ou `.map` du dossier `ui/` de l'extension (jamais en dehors du dossier).
