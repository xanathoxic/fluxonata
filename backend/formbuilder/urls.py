from django.urls import path

from . import control_views, views

urlpatterns = [
    path("workspaces/<slug:workspace>/form-library/", views.FormListCreateView.as_view()),
    path("workspaces/<slug:workspace>/form-library/check/", views.FormCheckView.as_view()),
    path("workspaces/<slug:workspace>/form-library/builtin-templates/", views.BuiltinTemplatesView.as_view()),
    path("workspaces/<slug:workspace>/form-library/<slug:slug>/", views.FormDetailView.as_view()),
    path("workspaces/<slug:workspace>/form-library/<slug:slug>/publish/", views.FormPublishView.as_view()),
    path("workspaces/<slug:workspace>/form-library/<slug:slug>/duplicate/", views.FormDuplicateView.as_view()),
    path(
        "workspaces/<slug:workspace>/form-library/<slug:slug>/versions/<str:version>/", views.FormVersionView.as_view()
    ),
    path("workspaces/<slug:workspace>/control-library/", control_views.ControlListCreateView.as_view()),
    path("workspaces/<slug:workspace>/control-library/check/", control_views.ControlCheckView.as_view()),
    path(
        "workspaces/<slug:workspace>/control-library/source-preview/", control_views.ControlSourcePreviewView.as_view()
    ),
    path("workspaces/<slug:workspace>/control-library/<slug:slug>/", control_views.ControlDetailView.as_view()),
    path(
        "workspaces/<slug:workspace>/control-library/<slug:slug>/publish/",
        control_views.ControlPublishView.as_view(),
    ),
    path(
        "workspaces/<slug:workspace>/control-library/<slug:slug>/versions/<str:version>/",
        control_views.ControlVersionView.as_view(),
    ),
]
