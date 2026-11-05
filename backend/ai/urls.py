from django.urls import path

from . import skills, views

urlpatterns = [
    path("workspaces/<slug:workspace>/ai/skills/", skills.SkillsView.as_view()),
    path("workspaces/<slug:workspace>/ai/skills/<slug:slug>/", skills.SkillDetailView.as_view()),
    path("workspaces/<slug:workspace>/ai/skills/<slug:slug>/versions/", skills.SkillVersionsView.as_view()),
    path("workspaces/<slug:workspace>/ai/skills/<slug:slug>/deprecate/", skills.SkillDeprecateView.as_view()),
    path("workspaces/<slug:workspace>/ai/skills/<slug:slug>/activate/", skills.SkillActivateView.as_view()),
    path("workspaces/<slug:workspace>/ai/skills/<slug:slug>/dependents/", skills.SkillDependentsView.as_view()),
    path("workspaces/<slug:workspace>/ai/policy/", views.PolicyView.as_view()),
    path("workspaces/<slug:workspace>/ai/providers/", views.ProviderListView.as_view()),
    path("workspaces/<slug:workspace>/ai/providers/<slug:name>/", views.ProviderDetailView.as_view()),
    path("workspaces/<slug:workspace>/ai/providers/<slug:name>/capabilities/", views.CapabilitiesView.as_view()),
    path("workspaces/<slug:workspace>/ai/usage/", views.UsageView.as_view()),
    path("workspaces/<slug:workspace>/ai/catalog-search/", views.CatalogSearchView.as_view()),
    path("workspaces/<slug:workspace>/ai/explain-error/", views.ExplainErrorView.as_view()),
    path("workspaces/<slug:workspace>/ai/draft-workflow/", views.DraftWorkflowView.as_view()),
    path("workspaces/<slug:workspace>/ai/outline-jobs/", views.OutlineJobView.as_view()),
    path("workspaces/<slug:workspace>/ai/draft-jobs/", views.DraftJobView.as_view()),
    path("workspaces/<slug:workspace>/ai/draft-jobs/<uuid:job_id>/", views.DraftJobDetailView.as_view()),
    path("workspaces/<slug:workspace>/ai/draft-jobs/<uuid:job_id>/cancel/", views.DraftJobCancelView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/ai/edit/", views.EditWorkflowView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/ai/edit-jobs/", views.EditWorkflowJobView.as_view()),
    path("workspaces/<slug:workspace>/ai/evals/", views.EvalRunsView.as_view()),
    path("workspaces/<slug:workspace>/ai/evals/<int:pk>/", views.EvalRunDetailView.as_view()),
    path("workspaces/<slug:workspace>/ai/propose-action/", views.ProposeActionView.as_view()),
    path("workspaces/<slug:workspace>/ai/propose-skill/", views.ProposeSkillView.as_view()),
    path("workspaces/<slug:workspace>/actions/<slug:slug>/ai/suggest-tests/", views.SuggestTestsView.as_view()),
]
