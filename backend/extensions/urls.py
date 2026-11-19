import importlib.util

from django.conf import settings
from django.urls import include, path

from . import views


def plugin_routes():
    """Les routes propres aux extensions : `plugins/<id>/…`, toujours dans le rôle privé `ui` (une extension n'ouvre aucune route publique)."""
    return [
        path(f"plugins/{e.manifest.id}/", include(f"{e.module}.urls"))
        for e in settings.EXTENSIONS
        if importlib.util.find_spec(f"{e.module}.urls") is not None
    ]


urlpatterns = [
    path("workspaces/<slug:workspace>/extensions/", views.ExtensionsView.as_view()),
    path("workspaces/<slug:workspace>/extensions/<slug:ext_id>/ui/<path:file>", views.ExtensionAssetView.as_view()),
    *plugin_routes(),
]
