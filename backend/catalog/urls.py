from django.urls import path

from . import library as lib
from . import views

urlpatterns = [
    path("workspaces/<slug:workspace>/integrations/", views.IntegrationCatalogView.as_view()),
    path("workspaces/<slug:workspace>/library/actions/", lib.ActionCreateView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/versions/", lib.ActionVersionsView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/meta/", lib.ActionMetaView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/deprecate/", lib.ActionDeprecateView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/activate/", lib.ActionActivateView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/rollback/", lib.ActionRollbackView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/compare/", lib.ActionCompareView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/tests/", lib.ActionTestCasesView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/tests/<int:pk>/", lib.ActionTestCaseView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/dependents/", lib.ActionDependentsView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/rollback/", lib.WorkflowRollbackView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/compare/", lib.WorkflowCompareView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/dependents/", lib.WorkflowDependentsView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/deprecate/", lib.WorkflowDeprecateView.as_view()),
    path("workspaces/<slug:workspace>/actions/", views.ActionListView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/", views.ActionDetailView.as_view()),
]
