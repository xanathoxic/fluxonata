from django.urls import path

from . import forms, polls, views

urlpatterns = [
    path("workspaces/<slug:workspace>/schedules/", views.ScheduleListCreateView.as_view()),
    path("workspaces/<slug:workspace>/schedules/preview/", views.CronPreviewView.as_view()),
    path("workspaces/<slug:workspace>/schedules/<int:pk>/", views.ScheduleDetailView.as_view()),
    path("workspaces/<slug:workspace>/webhooks/", views.WebhookListCreateView.as_view()),
    path("workspaces/<slug:workspace>/webhooks/<uuid:pk>/", views.WebhookDetailView.as_view()),
    path("workspaces/<slug:workspace>/forms/", forms.FormListCreateView.as_view()),
    path("workspaces/<slug:workspace>/forms/<int:pk>/", forms.FormDetailView.as_view()),
    path("workspaces/<slug:workspace>/poll-triggers/", polls.PollListCreateView.as_view()),
    path("workspaces/<slug:workspace>/poll-triggers/<int:pk>/", polls.PollDetailView.as_view()),
    path("workspaces/<slug:workspace>/poll-triggers/<int:pk>/poll-now/", polls.PollNowView.as_view()),
    path("forms/<str:token>/", forms.PublicFormView.as_view()),
    path("resume-forms/<str:token>/", views.ResumeFormView.as_view()),
    path("hooks/resume/<str:token>/", views.ResumeWaitView.as_view()),
    path("hooks/<uuid:pk>/", views.WebhookReceiveView.as_view()),
]
