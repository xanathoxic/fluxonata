from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from core import folder_api, health

from .apiroles import API_ROLES, parse_api_roles, role_of

APP_URLCONFS = ("tenancy", "catalog", "workflows", "runs", "formbuilder", "triggers", "vault", "bundles", "ai")

PROBES = [  # servies par TOUS les rôles : une sonde de disponibilité doit répondre où qu'on la pose
    path("api/v1/health/live/", health.live),
    path("api/v1/health/ready/", health.ready),
]
UI_ONLY = [
    path("api/v1/metrics/", health.MetricsView.as_view()),
    path("api/v1/workspaces/<slug:workspace>/system/health/", health.SystemHealthView.as_view()),
    path("api/v1/workspaces/<slug:workspace>/folders/", folder_api.FoldersView.as_view()),
    path("api/v1/workspaces/<slug:workspace>/folder-access/", folder_api.FolderAccessView.as_view()),
]
SCHEMA = path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema")


def build_urlpatterns(roles=API_ROLES):
    """Toutes les routes pour les trois rôles (inclusions d'origine, ordre inchangé) ; sinon seulement celles des rôles demandés."""
    if set(roles) == set(API_ROLES):
        return [*PROBES, *UI_ONLY, *[path("api/v1/", include(f"{app}.urls")) for app in APP_URLCONFS], SCHEMA]
    import importlib

    wanted = []
    for app in APP_URLCONFS:
        for p in importlib.import_module(f"{app}.urls").urlpatterns:
            if role_of(str(p.pattern)) in roles:
                wanted.append(p)
    return [*PROBES, *(UI_ONLY + [SCHEMA] if "ui" in roles else []), path("api/v1/", include(wanted))]


try:
    _roles = parse_api_roles(settings.ORCH_ROLES)
except ImproperlyConfigured:
    # ORCH_ROLES est partagé avec les workers (« exec », « aux », « ai ») : un process qui n'est pas une API charge pourtant ces routes (contrôles système de
    # Django) et ne doit pas planter dessus. Pour une API, la valeur est vérifiée STRICTEMENT au démarrage, dans wsgi.py.
    _roles = API_ROLES
urlpatterns = build_urlpatterns(_roles)
