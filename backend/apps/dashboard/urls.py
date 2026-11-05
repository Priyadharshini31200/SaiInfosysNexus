from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/sync/", views.DashboardSyncView.as_view(), name="dashboard-sync"),
    path("dashboard/project-usage/", views.ProjectUsageSummaryView.as_view(), name="dashboard-project-usage"),
    path(
        "dashboard/project-usage/<str:project>/<str:category>/",
        views.ProjectUsageListView.as_view(),
        name="dashboard-project-usage-list",
    ),
]
