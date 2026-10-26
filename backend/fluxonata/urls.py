from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from core import folder_api, health

urlpatterns = [
    path("api/v1/health/live/", health.live),
    path("api/v1/health/ready/", health.ready),
    path("api/v1/metrics/", health.MetricsView.as_view()),
    path("api/v1/workspaces/<slug:workspace>/system/health/", health.SystemHealthView.as_view()),
    path("api/v1/workspaces/<slug:workspace>/folders/", folder_api.FoldersView.as_view()),
    path("api/v1/", include("tenancy.urls")),
    path("api/v1/", include("catalog.urls")),
    path("api/v1/", include("workflows.urls")),
    path("api/v1/", include("runs.urls")),
    path("api/v1/", include("triggers.urls")),
    path("api/v1/", include("vault.urls")),
    path("api/v1/", include("bundles.urls")),
    path("api/v1/", include("ai.urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
]
