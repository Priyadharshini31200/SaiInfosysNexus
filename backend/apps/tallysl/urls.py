from django.urls import path

from . import admin_views as av
from . import client_views as cv

V1 = "v1/tally-sl"
ADMIN = "v1/tally-sl-admin"

urlpatterns = [
    # ---- staff (session auth + per-product permission)
    path(f"{ADMIN}/access/", av.MyAccessView.as_view()),
    path(f"{ADMIN}/products/", av.ProductListView.as_view()),
    path(f"{ADMIN}/permissions/", av.PermissionsConfigView.as_view()),
    path(f"{ADMIN}/dashboard/", av.DashboardView.as_view()),
    path(f"{ADMIN}/usage/<str:category>/", av.UsageListView.as_view()),
    path(f"{ADMIN}/reports/license-expiry/", av.ExpiryReportView.as_view()),
    path(f"{ADMIN}/reports/license-expiry/export/", av.ExpiryExportView.as_view()),
    path(f"{ADMIN}/reports/user-performance/", av.PerformanceReportView.as_view()),
    path(f"{ADMIN}/reports/user-performance/export/", av.PerformanceExportView.as_view()),
    path(f"{ADMIN}/reports/tally-details/", av.TallyDetailsReportView.as_view()),
    path(f"{ADMIN}/reports/tally-details/export/", av.TallyDetailsExportView.as_view()),
    path(f"{ADMIN}/products/<slug:product>/customers/", av.CustomerListView.as_view()),
    path(f"{ADMIN}/products/<slug:product>/customers/<int:pk>/", av.CustomerDetailView.as_view()),
    path(f"{ADMIN}/products/<slug:product>/customers/<int:pk>/license/<slug:action>/", av.LicenseActionView.as_view()),
    path(f"{ADMIN}/products/<slug:product>/customers/<int:pk>/device/revoke/", av.DeviceRevokeView.as_view()),
    # ---- product clients (X-Product-Key + customer bearer token)
    path(f"{V1}/<slug:product>/identify/", cv.IdentifyView.as_view()),
    path(f"{V1}/<slug:product>/register/", cv.RegisterView.as_view()),
    path(f"{V1}/<slug:product>/auth/login/", cv.LoginView.as_view()),
    path(f"{V1}/<slug:product>/auth/refresh/", cv.RefreshView.as_view()),
    path(f"{V1}/<slug:product>/auth/logout/", cv.LogoutView.as_view()),
    path(f"{V1}/<slug:product>/auth/forgot-password/", cv.ForgotPasswordView.as_view()),
    path(f"{V1}/<slug:product>/auth/reset-password/", cv.ResetPasswordView.as_view()),
    path(f"{V1}/<slug:product>/auth/change-password/", cv.ChangePasswordView.as_view()),
    path(f"{V1}/<slug:product>/session/", cv.SessionView.as_view()),
    path(f"{V1}/<slug:product>/account/", cv.AccountView.as_view()),
    path(f"{V1}/<slug:product>/license/status/", cv.LicenseStatusView.as_view()),
    path(f"{V1}/<slug:product>/tally-info/", cv.TallyInfoView.as_view()),
    path(f"{V1}/<slug:product>/usage/open/", cv.UsageOpenView.as_view()),
    path(f"{V1}/<slug:product>/activate/request-code/", cv.ActivationCodeView.as_view()),
    path(f"{V1}/<slug:product>/activate/verify/", cv.ActivationVerifyView.as_view()),
    path(f"{V1}/<slug:product>/activate/confirm/", cv.ActivationConfirmView.as_view()),
]
