"""Rôles d'un process d'API (ADR 0022, AR2.3) : quelles routes un déploiement sert.

    ui         interface et API authentifiée (tout ce qui n'est ni `ingest` ni `runner-gw`), y compris connexion, SCIM, schéma, métriques
    ingest     points d'entrée PUBLICS : webhooks (`hooks/…`), formulaires publics (`forms/…`), reprise d'attente (`resume-forms/…`) — exposé à Internet
    runner-gw  protocole des runners distants (`runner/…`) : longues attentes, secrets livrés, artefacts écrits

`ORCH_ROLES` vide = les trois (comportement historique : un seul déploiement sert tout). Un rôle non servi répond 404 : ce n'est pas seulement une règle de
proxy, la route n'existe pas dans ce process. Les sondes `health/live` et `health/ready` sont servies par tous les rôles.

Le classement se fait sur le début de la route ; une route inconnue tombe dans `ui` (le rôle privé : la valeur par défaut qui échoue en sûreté).
"""

from django.core.exceptions import ImproperlyConfigured

API_ROLES = ("ui", "ingest", "runner-gw")
ALIASES = {"api-ui": "ui", "api-ingest": "ingest", "api-runner-gw": "runner-gw", "gw": "runner-gw"}
INGEST_PREFIXES = ("forms/", "resume-forms/", "hooks/")
RUNNER_PREFIXES = ("runner/",)


def parse_api_roles(value) -> tuple[str, ...]:
    items = [x.strip().lower() for x in str(value or "").split(",") if x.strip()]
    if not items:
        return API_ROLES
    out = []
    for item in items:
        role = ALIASES.get(item, item)
        if role not in API_ROLES:
            raise ImproperlyConfigured(
                f"ORCH_ROLES : rôle d'API inconnu « {item} ». Valeurs possibles : {', '.join(API_ROLES)} "
                "(ou api-ui, api-ingest), séparées par des virgules. Les rôles de worker (exec, aux, ai) se règlent sur `run_worker`."
            )
        if role not in out:
            out.append(role)
    return tuple(out)


def role_of(route: str) -> str:
    """Rôle qui sert la route (texte du motif, sans le préfixe `api/v1/`)."""
    if route.startswith(INGEST_PREFIXES):
        return "ingest"
    if route.startswith(RUNNER_PREFIXES):
        return "runner-gw"
    return "ui"
