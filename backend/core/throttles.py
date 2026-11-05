"""Limitation de débit (AR1.1).

- Portées strictes (`ScopedRateThrottle` de DRF : login, webhook, form, form_lookup, ai ; `RunnerThrottle`) : cache `default`,
  partagé entre processus quand `ORCH_CACHE` vaut `db` ou `redis://…` (voir fluxonata/cacheconf.py).
- Limite générale par utilisateur (`user`) : cache `local` par défaut, donc **approximative** : au plus « débit × nombre de processus
  de l'API ». C'est un garde-fou contre un client emballé, pas une protection d'authentification ; la garder locale évite d'ajouter
  deux requêtes SQL à chaque appel. `ORCH_USER_THROTTLE_CACHE=default` la rend exacte (au prix de ces requêtes).

Limite connue : le compteur de DRF est lu puis réécrit sans verrou ; sous forte concurrence sur la même clé, quelques requêtes
peuvent passer au-delà du plafond (sous-comptage), jamais l'inverse.
"""

from django.conf import settings
from django.core.cache import caches
from rest_framework.throttling import UserRateThrottle


class LocalUserRateThrottle(UserRateThrottle):
    def __init__(self):
        self.cache = caches[getattr(settings, "ORCH_USER_THROTTLE_CACHE", "local")]
        super().__init__()
