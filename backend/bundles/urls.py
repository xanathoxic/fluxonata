from django.urls import path

from . import views

urlpatterns = [
    path("workspaces/<slug:workspace>/packages/trusted-keys/", views.TrustedKeysView.as_view()),
    path("workspaces/<slug:workspace>/packages/trusted-keys/<int:pk>/", views.TrustedKeyDeleteView.as_view()),
    path("workspaces/<slug:workspace>/packages/", views.PackageListView.as_view()),
    path("workspaces/<slug:workspace>/packages/export/", views.ExportView.as_view()),
    path("workspaces/<slug:workspace>/packages/import/", views.ImportView.as_view()),
    path(
        "workspaces/<slug:workspace>/packages/<slug:slug>/versions/<str:version>/download/",
        views.DownloadView.as_view(),
    ),
]
