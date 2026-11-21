from django.urls import path

from . import card_visibility, views

urlpatterns = [
    path("dashboard/card-visibility/", card_visibility.CardVisibilityListView.as_view(), name="dashboard-card-visibility"),
    path("dashboard/card-visibility/projects/", card_visibility.CardVisibilityProjectsView.as_view(), name="dashboard-card-visibility-projects"),
    path("dashboard/card-visibility/<str:code>/", card_visibility.CardVisibilityDetailView.as_view(), name="dashboard-card-visibility-detail"),
    path("dashboard/sync/", views.DashboardSyncView.as_view(), name="dashboard-sync"),
    path("dashboard/project-usage/", views.ProjectUsageSummaryView.as_view(), name="dashboard-project-usage"),
    path(
        "dashboard/project-usage/<str:project>/<str:category>/",
        views.ProjectUsageListView.as_view(),
        name="dashboard-project-usage-list",
    ),
]
