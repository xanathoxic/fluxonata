"""Nombre de proxys de confiance devant l'API (adresse du client, limites de débit).

Les limites de débit par adresse (connexion, webhooks, formulaires publics) lisent l'adresse du client via DRF (`NUM_PROXIES`).

    ORCH_NUM_PROXIES non défini   comportement historique de DRF : l'en-tête `X-Forwarded-For` reçu est pris en entier. Un client peut
                                  y écrire n'importe quelle valeur et obtenir un compteur neuf à chaque requête : la limite par adresse
                                  est alors contournable.
    ORCH_NUM_PROXIES=N            l'adresse retenue est la N-ième en partant de la fin de `X-Forwarded-For`, c'est-à-dire celle que le
                                  proxy de confiance le plus proche de l'API a ajoutée. Les valeurs écrites par le client sont ignorées.
                                  N = nombre de proxys entre le client et l'API (nginx livré seul : 1 ; un répartiteur TLS devant
                                  nginx : 2). Un N trop grand fait retomber sur une adresse choisie par le client, un N trop petit
                                  regroupe tous les clients sous l'adresse d'un proxy.
    ORCH_NUM_PROXIES=0            aucun proxy : seule l'adresse de la connexion TCP compte (API exposée directement).

Cette valeur n'a aucun effet sur `Referer`, `Host` ni `X-Forwarded-Proto` (voir `ORCH_BEHIND_TLS_PROXY`).
"""

from django.core.exceptions import ImproperlyConfigured

MAX_PROXIES = 10


def num_proxies(raw: str | None) -> int | None:
    """`None` (variable absente ou vide) = comportement historique ; sinon un entier de 0 à 10, sinon erreur de configuration."""
    text = (raw or "").strip()
    if not text:
        return None
    if not text.isascii() or not text.isdigit() or not 0 <= int(text) <= MAX_PROXIES:
        raise ImproperlyConfigured(f"ORCH_NUM_PROXIES doit être un entier de 0 à {MAX_PROXIES} (reçu : {text[:20]!r}).")
    return int(text)
