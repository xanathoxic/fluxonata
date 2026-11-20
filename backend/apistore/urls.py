from django.urls import path

from . import consume_views as c
from . import manage_views as m
from . import mcp_view

urlpatterns = [
    # Consommateurs : authentifiés par une clé de souscription.
    path("v1/<slug:workspace>/<slug:product>/workflows/<slug:workflow>/runs/", c.InvokeView.as_view()),
    path("v1/<slug:workspace>/<slug:product>/runs/<uuid:run_id>/", c.RunStatusView.as_view()),
    path("v1/<slug:workspace>/<slug:product>/mcp/", mcp_view.ProductMcpView.as_view()),
    # Gestion : session ou jeton, droits de l'espace.
    path("manage/<slug:workspace>/options/", m.OptionsView.as_view()),
    path("manage/<slug:workspace>/me/", m.MeView.as_view()),
    path("manage/<slug:workspace>/products/", m.ProductsView.as_view()),
    path("manage/<slug:workspace>/products/<slug:slug>/", m.ProductDetailView.as_view()),
    path("manage/<slug:workspace>/products/<slug:slug>/plans/", m.PlansView.as_view()),
    path("manage/<slug:workspace>/products/<slug:slug>/docs/", m.ProductDocsView.as_view()),
    path("manage/<slug:workspace>/products/<slug:slug>/docs/openapi/", m.ProductOpenApiView.as_view()),
    path("manage/<slug:workspace>/subscriptions/", m.SubscriptionsView.as_view()),
    path("manage/<slug:workspace>/subscriptions/<int:pk>/", m.SubscriptionDetailView.as_view()),
    path("manage/<slug:workspace>/subscriptions/<int:pk>/decision/", m.DecisionView.as_view()),
    path("manage/<slug:workspace>/subscriptions/<int:pk>/status/", m.StatusView.as_view()),
    path("manage/<slug:workspace>/subscriptions/<int:pk>/keys/", m.KeysView.as_view()),
    path("manage/<slug:workspace>/subscriptions/<int:pk>/keys/<int:key_id>/revoke/", m.KeyRevokeView.as_view()),
    path("manage/<slug:workspace>/subscriptions/<int:pk>/usage/", m.UsageView.as_view()),
]
