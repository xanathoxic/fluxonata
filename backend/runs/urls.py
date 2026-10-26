from django.urls import path

from . import run_views, runner_api, views
from . import runner_admin as ra

urlpatterns = [
    path("platform/runners/", ra.PlatformRunnersView.as_view()),
    path("platform/runners/<uuid:pk>/", ra.PlatformRunnerDetailView.as_view()),
    path("platform/runners/<uuid:pk>/rotate-token/", ra.PlatformRunnerRotateView.as_view()),
    path("workspaces/<slug:workspace>/runners/", ra.WorkspaceRunnersView.as_view()),
    path("workspaces/<slug:workspace>/runners/<uuid:pk>/", ra.WorkspaceRunnerDetailView.as_view()),
    path("workspaces/<slug:workspace>/runners/<uuid:pk>/rotate-token/", ra.WorkspaceRunnerRotateView.as_view()),
    path("workspaces/<slug:workspace>/runner-settings/", ra.WorkspaceRunnerSettingsView.as_view()),
    path("runner/me/", runner_api.RunnerMeView.as_view()),
    path("runner/jobs/request/", runner_api.JobRequestView.as_view()),
    path("runner/jobs/<uuid:pk>/heartbeat/", runner_api.JobHeartbeatView.as_view()),
    path("runner/jobs/<uuid:pk>/complete/", runner_api.JobCompleteView.as_view()),
    path("workspaces/<slug:workspace>/executions/<uuid:pk>/artifacts/", views.ExecutionArtifactsView.as_view()),
    path("workspaces/<slug:workspace>/workflow-runs/<uuid:pk>/artifacts/", views.RunArtifactsView.as_view()),
    path("workspaces/<slug:workspace>/artifacts/<uuid:pk>/download/", views.ArtifactDownloadView.as_view()),
    path("workspaces/<slug:workspace>/executions/", views.ExecutionListCreateView.as_view()),
    path("workspaces/<slug:workspace>/executions/<uuid:pk>/", views.ExecutionDetailView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/runs/", run_views.StartWorkflowRunView.as_view()),
    path("workspaces/<slug:workspace>/workflow-runs/", run_views.WorkflowRunListView.as_view()),
    path("workspaces/<slug:workspace>/workflow-runs/<uuid:pk>/", run_views.WorkflowRunDetailView.as_view()),
    path("workspaces/<slug:workspace>/workflow-runs/<uuid:pk>/events/", run_views.RunEventsView.as_view()),
    path("workspaces/<slug:workspace>/workflow-runs/<uuid:pk>/cancel/", run_views.CancelRunView.as_view()),
    path(
        "workspaces/<slug:workspace>/workflow-runs/<uuid:pk>/steps/<slug:node>/decision/",
        run_views.StepDecisionView.as_view(),
    ),
    path("workspaces/<slug:workspace>/approvals/", run_views.ApprovalListView.as_view()),
    path("workspaces/<slug:workspace>/approvals/<int:pk>/decide/", run_views.ApprovalDecideView.as_view()),
]
