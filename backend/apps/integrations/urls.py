from django.urls import path

from . import admin_views as av
from . import product_views as pv
from . import views as v

V1 = "integrations/v1"
ADMIN = "integrations/admin"

urlpatterns = [
    # ---- project backends (X-Client-Id / X-Client-Secret)
    path(f"{V1}/customers/register", v.RegisterView.as_view()),
    path(f"{V1}/customers/update", v.UpdateView.as_view()),
    path(f"{V1}/customers/import", v.ImportView.as_view()),
    path(f"{V1}/auth/verify", v.AuthVerifyView.as_view()),
    path(f"{V1}/auth/change-password", v.ChangePasswordView.as_view()),
    path(f"{V1}/licenses/validate", v.LicenseValidateView.as_view()),
    path(f"{V1}/activation/request", v.ActivationRequestView.as_view()),
    path(f"{V1}/activation/verify", v.ActivationVerifyView.as_view()),
    path(f"{V1}/devices/transfer/confirm", v.TransferConfirmView.as_view()),
    path(f"{V1}/sessions/introspect", v.IntrospectView.as_view()),
    path(f"{V1}/sessions/logout", v.LogoutView.as_view()),
    path(f"{V1}/usage/events", v.UsageView.as_view()),
    path(f"{V1}/projects/<str:project_code>/config", v.ConfigView.as_view()),
    # ---- Super Admin
    path(f"{ADMIN}/dashboard/", av.DashboardView.as_view()),
    path(f"{ADMIN}/projects/", av.ProjectListView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/", av.ProjectDetailView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/rotate-credentials/", av.RotateView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/test-connection/", av.TestConnectionView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/docs/<slug:kind>/", av.DocsView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/customers/", av.CustomerListView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/audit/", av.AuditView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/dashboard/", pv.ProductDashboardView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/product-customers/", pv.ProductCustomersView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/product-customers/export/", pv.ProductExportView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/product-customers/<int:pk>/", pv.ProductCustomerDetailView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/product-customers/<int:pk>/<slug:action>/", pv.ProductCustomerActionView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/reports/<slug:kind>/", pv.ProductReportView.as_view()),
    path(f"{ADMIN}/projects/<str:code>/reports/<slug:kind>/export/", pv.ProductReportExportView.as_view()),
    path(f"{ADMIN}/licenses/<int:license_id>/approve/", av.ApproveView.as_view()),
    path(f"{ADMIN}/customers/<int:customer_id>/resend-welcome/", av.ResendWelcomeView.as_view()),
    path(f"{ADMIN}/failed-sync/", av.FailedSyncView.as_view()),
    path(f"{ADMIN}/failed-sync/<int:log_id>/retry/", av.RetrySyncView.as_view()),
]
