from django.urls import path

from . import admin_views as a
from . import platform_views as pf
from . import scim, views
from . import user_views as uv

urlpatterns = [
    path("platform/workspaces/", pf.WorkspacesView.as_view()),
    path("platform/workspaces/<slug:slug>/", pf.WorkspaceDetailView.as_view()),
    path("platform/workspaces/<slug:slug>/admins/", pf.WorkspaceAdminView.as_view()),
    path("platform/workers/", pf.WorkersView.as_view()),
    path("platform/workers/purge/", pf.WorkersPurgeView.as_view()),
    path("platform/admins/", pf.PlatformAdminsView.as_view()),
    path("platform/admins/<str:username>/", pf.PlatformAdminDetailView.as_view()),
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
    path("auth/oidc/config/", views.OidcConfigView.as_view()),
    path("auth/oidc/login/", views.OidcLoginView.as_view()),
    path("auth/oidc/callback/", views.OidcCallbackView.as_view()),
    path("workspaces/<slug:workspace>/members/", a.MembersView.as_view()),
    path("workspaces/<slug:workspace>/members/<str:username>/", a.MemberDetailView.as_view()),
    path("workspaces/<slug:workspace>/service-accounts/", a.ServiceAccountsView.as_view()),
    path("workspaces/<slug:workspace>/tokens/", a.TokensView.as_view()),
    path("workspaces/<slug:workspace>/tokens/<int:pk>/rotate/", a.TokenRotateView.as_view()),
    path("workspaces/<slug:workspace>/tokens/<int:pk>/", a.TokenRevokeView.as_view()),
    path("workspaces/<slug:workspace>/policy/", a.PolicyView.as_view()),
    path("workspaces/<slug:workspace>/promotions/", a.PromotionListView.as_view()),
    path("workspaces/<slug:workspace>/promotions/<int:pk>/decide/", a.PromotionDecideView.as_view()),
    path("workspaces/<slug:workspace>/workflows/<slug:slug>/versions/<str:version>/promote/", a.PromoteView.as_view()),
    path("workspaces/<slug:workspace>/audit/", a.AuditListView.as_view()),
    path("workspaces/<slug:workspace>/audit/export/", a.AuditExportView.as_view()),
]
