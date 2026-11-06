from django.urls import path, re_path

from . import catalog_views as cat
from . import mcp, mcp_design, views
from . import views_comments as com
from . import views_debug as dbg
from . import views_extra as x
from . import views_git as git
from . import views_templates as tpl

urlpatterns = [
    # Les clients MCP enregistrent l'adresse telle qu'on la leur donne et en retirent parfois le « / » final : sans lui, une redirection
    # transformerait le POST en GET (405, ou 500 avec DEBUG). Les deux routes MCP acceptent donc les deux formes.
    re_path(r"^workspaces/(?P<workspace>[-a-zA-Z0-9_]+)/mcp/?$", mcp.McpServerView.as_view()),
    re_path(r"^workspaces/(?P<workspace>[-a-zA-Z0-9_]+)/mcp/design/?$", mcp_design.McpDesignView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/comments/", com.CommentListCreateView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/comments/<int:pk>/", com.CommentDetailView.as_view()),
    path("workspaces/<slug:workspace>/git/", git.GitConfigView.as_view()),
    path("workspaces/<slug:workspace>/git/jobs/", git.GitJobsView.as_view()),
    path("workspaces/<slug:workspace>/git/jobs/<int:pk>/", git.GitJobDetailView.as_view()),
    path("workspaces/<slug:workspace>/workflow-templates/", tpl.TemplateListView.as_view()),
    path("workspaces/<slug:workspace>/workflow-templates/<int:pk>/", tpl.TemplateDeleteView.as_view()),
    path("workspaces/<slug:workspace>/workflow-templates/<str:slug>/use/", tpl.TemplateUseView.as_view()),
    path("workspaces/<slug:workspace>/workflows/import/", tpl.WorkflowImportView.as_view()),
    path("workspaces/<slug:workspace>/workflows/export-all/", tpl.ExportAllView.as_view()),
    path("workspaces/<slug:workspace>/workflows/import-all/", tpl.ImportAllView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/export/", tpl.WorkflowExportView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/save-as-template/", tpl.SaveAsTemplateView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/versions/diff/", dbg.VersionDiffView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/debug/eval/", dbg.DebugEvalView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/debug/test-step/", dbg.DebugTestStepView.as_view()),
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
