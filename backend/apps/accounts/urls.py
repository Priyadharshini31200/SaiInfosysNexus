from django.urls import path

from . import company, consolidated_reports, staff_report, staff_views, views

urlpatterns = [
    path("reports/consolidated/", consolidated_reports.ConsolidatedReportsView.as_view(), name="consolidated-reports"),
    path("staff/expiry-report/", staff_report.ConsolidatedMonthExpiryView.as_view(), name="staff-expiry-report"),
    path("config/staff/", staff_views.StaffListView.as_view(), name="staff-list"),
    path("config/staff/<int:pk>/", staff_views.StaffDetailView.as_view(), name="staff-detail"),
    path("config/staff/<int:pk>/setup-link/", staff_views.StaffSetupLinkView.as_view(), name="staff-setup-link"),
    path("auth/setup-password/", staff_views.SetupPasswordView.as_view(), name="auth-setup-password"),
    path("config/project-permissions/", views.ProjectPermissionsView.as_view()),
    path("config/admins/<int:pk>/reset-password/", views.AdminPasswordResetView.as_view()),
    path("auth/csrf/", views.CsrfView.as_view(), name="auth-csrf"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("company/profile/", company.CompanyProfileView.as_view(), name="company-profile"),
    path("company/logo/", company.CompanyLogoView.as_view(), name="company-logo"),
    path("auth/change-password/", views.ChangePasswordView.as_view(), name="auth-change-password"),
    path("config/admin-permissions/", views.AdminPermissionsView.as_view(), name="config-admin-permissions"),
    path("config/admin-permissions/<int:pk>/", views.AdminPermissionDetailView.as_view(), name="config-admin-permission"),
]
