"""Scoped grants for managing UNIVERSE projects."""
from rest_framework.permissions import BasePermission

from apps.accounts.constants import Role
from apps.accounts.permissions import get_role, user_can

from .models import IntegrationProject


def project_actions(user, project):
    role = get_role(user)
    if project.license_policy != "CALENDAR":
        return {"edit": False, "archive": False, "configure": False}
    return {
        "edit": role == Role.SUPERADMIN or user_can(user, f"{project.code}:manage", "edit"),
        "archive": role == Role.SUPERADMIN or user_can(user, f"{project.code}:manage", "inactivate"),
        "configure": role == Role.SUPERADMIN or user_can(user, f"{project.code}:api", "edit"),
    }


class CanConfigureProject(BasePermission):
    def has_permission(self, request, view):
        role = get_role(request.user)
        if role == Role.SUPERADMIN:
            return True
        code = view.kwargs["code"].upper()
        return role == Role.ADMIN and user_can(request.user, f"{code}:api", "edit") and IntegrationProject.objects.filter(
            code=code, license_policy="CALENDAR",
        ).exists()
