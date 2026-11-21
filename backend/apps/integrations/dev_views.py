"""Project-scoped developer workspace API: /api/integrations/dev/... A developer configures ONLY the integration of the projects they were
assigned (API endpoints, authentication reference, field mapping, tests, own logs, documentation). Never customers, licences, staff,
approvals, credentials rotation, e-mail or activation - those stay Superadmin-only."""
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.accounts.constants import Role
from apps.accounts.models import DeveloperProjectAccess
from apps.accounts.permissions import get_role

from . import admin_views as av
from .models import IntegrationProject


class DeveloperOfProject(BasePermission):
    message = "You are not assigned to this project."

    def has_permission(self, request, view):
        role = get_role(request.user)
        if role == Role.SUPERADMIN:
            return True
        code = str(view.kwargs.get("code", "")).upper()
        return role == Role.DEVELOPER and DeveloperProjectAccess.objects.filter(user=request.user, project_code__iexact=code).exists()


class DevProjectsView(av.AdminView):
    permission_classes = [DeveloperOfProject]

    def check_permissions(self, request):  # list view has no project in the URL
        if get_role(request.user) not in (Role.SUPERADMIN, Role.DEVELOPER):
            self.permission_denied(request)

    def get(self, request):
        qs = IntegrationProject.objects.all()
        if get_role(request.user) == Role.DEVELOPER:
            qs = qs.filter(code__in=[c.upper() for c in DeveloperProjectAccess.objects.filter(user=request.user).values_list("project_code", flat=True)])
        return Response({"projects": [{"code": p.code, "name": p.name, "status": p.status, "tally_required": p.tally_serial_required} for p in qs]})


class DevProjectView(av.AdminView):
    permission_classes = [DeveloperOfProject]

    def get(self, request, code):
        p = self.project(code)
        return Response({"project": {"code": p.code, "name": p.name, "frontend_url": p.frontend_url, "backend_url": p.backend_url, "status": p.status,
                                     "tally_required": p.tally_serial_required}})


class DevApiConfigView(av.ApiConfigView):
    permission_classes = [DeveloperOfProject]


class DevValidateView(av.ValidateApiView):
    permission_classes = [DeveloperOfProject]


class DevTestConnectionView(av.TestConnectionView):
    permission_classes = [DeveloperOfProject]


class DevDocsView(av.DocsView):
    permission_classes = [DeveloperOfProject]


class DevLogsView(av.AuditView):
    permission_classes = [DeveloperOfProject]
