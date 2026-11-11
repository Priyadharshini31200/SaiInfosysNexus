import logging

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_can
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied


from apps.registrations import services
from . import usage


class CanViewDashboard(BasePermission):
    def has_permission(self, request, view):
        return any(user_can(request.user, f"{code}:dashboard", "view") for code in usage.PROJECTS)

logger = logging.getLogger(__name__)


class ProjectUsageSummaryView(APIView):
    """The three counts for every project. A project whose data cannot be read is reported as
    unavailable instead of failing the whole dashboard."""

    permission_classes = [CanViewDashboard]

    def get(self, request):
        projects = []
        for code, project in usage.PROJECTS.items():
            if not user_can(request.user, f"{code}:dashboard", "view"):
                continue
            entry = {"code": code, "label": project["label"], "available": True}
            try:
                entry.update(usage.summary(code))
            except usage.ProjectUnavailable:
                logger.exception("Dashboard: %s usage data is unavailable", code)
                entry.update(available=False, message=f"{project['label']} data is currently unavailable.")
            projects.append(entry)
        return Response({"projects": projects})


class CanReadSync(BasePermission):
    def has_permission(self, request, view):
        return any(user_can(request.user, f"{code}:{kind}", action)
                   for code in usage.PROJECTS for kind, action in (("dashboard", "view"), ("usage", "reports"), ("expiry", "reports")))


class DashboardSyncView(APIView):
    """Fingerprint of every project's customer records, polled so the dashboard counts follow changes made in other sessions."""

    permission_classes = [CanReadSync]

    def get(self, request):
        return Response({"version": "|".join(services.sync_version(code) for code in usage.PROJECTS if any(
            user_can(request.user, f"{code}:{kind}", action) for kind, action in (("dashboard", "view"), ("usage", "reports"), ("expiry", "reports"))))})


class ProjectUsageListView(APIView):
    """The customers/devices behind one count of one project, with search and pagination."""

    permission_classes = [CanViewDashboard]

    def get(self, request, project, category):
        if project not in usage.PROJECTS or category not in usage.CATEGORIES:
            raise NotFound("Unknown project or count.")
        if not user_can(request.user, f"{project}:dashboard", "view"):
            raise PermissionDenied()
        params = request.query_params
        try:
            page = max(int(params.get("page", 1)), 1)
            page_size = int(params.get("page_size", usage.PAGE_SIZES[0]))
        except ValueError:
            raise ValidationError({"page": ["Page and page size must be numbers."]})
        if page_size not in usage.PAGE_SIZES:
            raise ValidationError({"page_size": [f"Page size must be one of {', '.join(map(str, usage.PAGE_SIZES))}."]})
        search = " ".join(params.get("search", "").split())[:100]
        try:
            data = usage.listing(project, category, search, page, page_size)
        except usage.ProjectUnavailable:
            logger.exception("Dashboard: %s usage list is unavailable", project)
            return Response(
                {"message": f"{usage.PROJECTS[project]['label']} data is currently unavailable.", "errors": {}},
                status=503,
            )
        return Response({"page": page, "page_size": page_size, **data})
