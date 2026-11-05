"""Pagination par défaut de l'API (AR8.1).

`LimitOffsetPagination` de DRF n'a pas de plafond : un client pouvait demander `limit=1000000` et lire toute une table en une requête.
Le plafond (500) est assez haut pour qu'un écran qui lit une liste entière (`apiAll` du front) coûte peu de requêtes — 3 pour
1 100 workflows au lieu de 11 avec des pages de 100 — et assez bas pour borner le travail d'une requête.
"""

from rest_framework.pagination import LimitOffsetPagination

MAX_PAGE = 500


class BoundedLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 50  # = PAGE_SIZE
    max_limit = MAX_PAGE
