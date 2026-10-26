from django.urls import path

from . import views

urlpatterns = [
    path("workspaces/<slug:workspace>/secrets/", views.SecretListView.as_view()),
    path("workspaces/<slug:workspace>/secrets/<slug:name>/", views.SecretDetailView.as_view()),
    path("workspaces/<slug:workspace>/connections/", views.ConnectionListCreateView.as_view()),
    path("workspaces/<slug:workspace>/connections/<int:pk>/", views.ConnectionDetailView.as_view()),
    path("workspaces/<slug:workspace>/connections/<int:pk>/test/", views.ConnectionTestView.as_view()),
]
