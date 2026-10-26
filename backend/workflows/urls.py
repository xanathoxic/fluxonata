from django.urls import path

from . import catalog_views as cat
from . import views
from . import views_extra as x

urlpatterns = [
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/delete/", x.WorkflowDeleteView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/meta/", x.WorkflowMetaView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/dependencies/", x.DependenciesView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/audit/", x.WorkflowAuditView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/test-script/", x.ScriptTestView.as_view()),
    path("workspaces/<slug:workspace>/catalog/", cat.CatalogListView.as_view()),
    path("workspaces/<slug:workspace>/catalog/<slug:slug>/", cat.CatalogDetailView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/catalog/", cat.CatalogPublishView.as_view()),
    path("workspaces/<slug:workspace>/favorites/", x.FavoritesView.as_view()),
    path("workspaces/<slug:workspace>/favorites/<str:kind>/<slug:slug>/", x.FavoriteDetailView.as_view()),
    path("workspaces/<slug:workspace>/workflows/", views.WorkflowListCreateView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/", views.WorkflowDetailView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/draft/", views.DraftView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/contract/", views.ContractView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/validate/", views.ValidateView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/publish/", views.PublishView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/versions/", views.VersionListView.as_view()),
]
