from django.urls import path

from . import admin_views as a
from . import branding_views as br
from . import mcp_tokens as mt
from . import platform_views as pf
from . import scim, views
from . import sso_admin_views as sa
from . import sso_views as sv
from . import user_views as uv

urlpatterns = [
    path("platform/workspaces/", pf.WorkspacesView.as_view()),
    path("platform/workspaces/<slug:slug>/", pf.WorkspaceDetailView.as_view()),
    path("platform/workspaces/<slug:slug>/admins/", pf.WorkspaceAdminView.as_view()),
    path("platform/admins/", pf.PlatformAdminsView.as_view()),
    path("platform/admins/<str:username>/", pf.PlatformAdminDetailView.as_view()),
    path("branding/", br.PublicBrandingView.as_view()),
    path("branding/assets/<slug:kind>/", br.PublicAssetView.as_view()),
    path("platform/branding/", br.BrandingAdminView.as_view()),
    path("platform/branding/reset/", br.BrandingResetView.as_view()),
    path("platform/branding/assets/<slug:kind>/", br.BrandingAssetAdminView.as_view()),
    path("platform/egress/", pf.EgressView.as_view()),
    path("platform/egress/<int:pk>/", pf.EgressDetailView.as_view()),
    path("platform/audit/", pf.PlatformAuditView.as_view()),
    path("platform/users/", uv.UsersView.as_view()),
    path("platform/users/<str:username>/", uv.UserDetailView.as_view()),
    path("platform/users/<str:username>/reset-password/", uv.UserResetPasswordView.as_view()),
    path("platform/users/<str:username>/memberships/", uv.UserMembershipsView.as_view()),
    path("platform/users/<str:username>/memberships/<slug:workspace>/", uv.UserMembershipDetailView.as_view()),
    path("auth/password/", uv.PasswordChangeView.as_view()),
    path("auth/csrf/", views.CsrfView.as_view()),
    path("auth/login/", views.LoginView.as_view()),
    path("auth/logout/", views.LogoutView.as_view()),
    path("auth/me/", views.MeView.as_view()),
    path("scim/v2/ServiceProviderConfig", scim.ServiceProviderConfigView.as_view()),
    path("scim/v2/Users", scim.UsersView.as_view()),
    path("scim/v2/Users/<str:pk>", scim.UserDetailView.as_view()),
    path("platform/sso/providers/", sa.ProvidersView.as_view()),
    path("platform/sso/providers/<slug:slug>/", sa.ProviderView.as_view()),
    path("platform/sso/providers/<slug:slug>/test/", sa.ProviderTestView.as_view()),
    path("platform/sso/providers/<slug:slug>/rules/", sa.RulesView.as_view()),
    path("platform/sso/providers/<slug:slug>/simulate/", sa.SimulateView.as_view()),
    path("platform/sso/saml/import-metadata/", sa.SamlMetadataImportView.as_view()),
    path("auth/sso/providers/", sv.ProvidersView.as_view()),
    path("auth/sso/<slug:slug>/login/", sv.LoginView.as_view()),
    path("auth/sso/<slug:slug>/callback/", sv.CallbackView.as_view()),
    path("auth/sso/<slug:slug>/acs/", sv.AcsView.as_view()),
    path("auth/sso/<slug:slug>/metadata/", sv.MetadataView.as_view()),
    path("auth/oidc/config/", views.OidcConfigView.as_view()),
    path("auth/oidc/login/", views.OidcLoginView.as_view()),
    path("auth/oidc/callback/", views.OidcCallbackView.as_view()),
    path("workspaces/<slug:workspace>/members/", a.MembersView.as_view()),
    path("workspaces/<slug:workspace>/members/<str:username>/", a.MemberDetailView.as_view()),
    path("workspaces/<slug:workspace>/service-accounts/", a.ServiceAccountsView.as_view()),
    path("workspaces/<slug:workspace>/tokens/", a.TokensView.as_view()),
    path("workspaces/<slug:workspace>/tokens/<int:pk>/rotate/", a.TokenRotateView.as_view()),
    path("workspaces/<slug:workspace>/tokens/<int:pk>/", a.TokenRevokeView.as_view()),
    path("workspaces/<slug:workspace>/me/mcp-tokens/", mt.McpTokensView.as_view()),
    path("workspaces/<slug:workspace>/me/mcp-tokens/<int:pk>/", mt.McpTokenRevokeView.as_view()),
    path("workspaces/<slug:workspace>/policy/", a.PolicyView.as_view()),
    path("workspaces/<slug:workspace>/audit/", a.AuditListView.as_view()),
    path("workspaces/<slug:workspace>/audit/export/", a.AuditExportView.as_view()),
]
