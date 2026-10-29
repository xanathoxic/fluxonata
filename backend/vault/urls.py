from django.urls import path

from . import platform_views as pv
from . import views

urlpatterns = [
    path("workspaces/<slug:workspace>/secrets/", views.SecretListView.as_view()),
    path("workspaces/<slug:workspace>/secrets/<slug:name>/", views.SecretDetailView.as_view()),
    path("workspaces/<slug:workspace>/secrets/<slug:name>/test/", views.SecretTestView.as_view()),
    path("workspaces/<slug:workspace>/vault/", views.WorkspaceVaultView.as_view()),
    path("platform/vault/", pv.VaultConfigView.as_view()),
    path("platform/vault/test/", pv.VaultTestView.as_view()),
    path("platform/vault/grants/", pv.VaultGrantsView.as_view()),
    path("platform/vault/grants/<slug:workspace>/", pv.VaultGrantsView.as_view()),
    path("workspaces/<slug:workspace>/connections/", views.ConnectionListCreateView.as_view()),
    path("workspaces/<slug:workspace>/connections/<int:pk>/", views.ConnectionDetailView.as_view()),
    path("workspaces/<slug:workspace>/connections/<int:pk>/test/", views.ConnectionTestView.as_view()),
    path("workspaces/<slug:workspace>/connections/<int:pk>/oauth/start/", views.ConnectionOAuthStartView.as_view()),
    path("oauth/callback/", views.OAuthCallbackView.as_view()),
]
