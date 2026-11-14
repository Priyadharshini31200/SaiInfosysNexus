from django.urls import include, path

from apps.core.views import HealthView

# Single API base path: everything is served under /api/
urlpatterns = [
    path("api/health/", HealthView.as_view(), name="health"),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.dashboard.urls")),
    path("api/", include("apps.registrations.urls")),
    path("api/", include("apps.tallysl.urls")),
]
