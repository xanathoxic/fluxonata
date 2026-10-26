from django.urls import path

from . import views

urlpatterns = [
    path("workspaces/<slug:workspace>/schedules/", views.ScheduleListCreateView.as_view()),
    path("workspaces/<slug:workspace>/schedules/preview/", views.CronPreviewView.as_view()),
    path("workspaces/<slug:workspace>/schedules/<int:pk>/", views.ScheduleDetailView.as_view()),
    path("workspaces/<slug:workspace>/webhooks/", views.WebhookListCreateView.as_view()),
    path("workspaces/<slug:workspace>/webhooks/<uuid:pk>/", views.WebhookDetailView.as_view()),
    path("hooks/<uuid:pk>/", views.WebhookReceiveView.as_view()),
]
