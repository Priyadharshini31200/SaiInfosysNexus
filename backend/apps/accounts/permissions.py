"""Reusable DRF permissions. All authorization is enforced here, never only in the UI."""
from rest_framework.permissions import BasePermission

from .constants import Action, Role
from .models import UserModulePermission


def get_role(user):
    profile = getattr(user, "profile", None) if user and user.is_authenticated else None
    return profile.role if profile else None


class IsSuperAdmin(BasePermission):
    message = "Super Admin access is required."

    def has_permission(self, request, view):
        return get_role(request.user) == Role.SUPERADMIN


class IsAdminOrSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return get_role(request.user) in {Role.SUPERADMIN, Role.ADMIN}


def user_can(user, module, action):
    """Superadmin has full access; Admin needs an explicit per-account module grant."""
    role = get_role(user)
    if role == Role.SUPERADMIN:
        return True
    if role != Role.ADMIN:
        return False
    if module is None:
        # Aggregate only for navigation; data endpoints always pass a concrete module.
        return any(user_can(user, code, action) for code in permission_modules())
    grant = UserModulePermission.objects.filter(user=user, module=module).first()
    return grant.allows(action) if grant is not None else False


def permission_modules():
    from apps.registrations.models import PROJECTS
    return [module for code in PROJECTS for module in (code, f"{code}:dashboard", f"{code}:usage", f"{code}:expiry")]



def HasModulePermission(module, action):
    """Factory: `permission_classes = [HasModulePermission("projects", Action.VIEW)]`."""

    class _HasModulePermission(BasePermission):
        message = "You do not have permission to perform this action."

        def has_permission(self, request, view):
            return user_can(request.user, module, action)

    _HasModulePermission.__name__ = f"HasModulePermission_{module}_{Action(action).value}"
    return _HasModulePermission


def permission_map(user):
    """Aggregate navigation hints. Never use these aggregates to authorize a data query."""
    scoped = scoped_permission_map(user)
    return {a.value: any(grants[a.value] for grants in scoped.values()) for a in Action}


def scoped_permission_map(user):
    role = get_role(user)
    grants = {row.module: row for row in UserModulePermission.objects.filter(user=user)} if role == Role.ADMIN else {}
    return {module: {a.value: role == Role.SUPERADMIN or bool(grants.get(module) and grants[module].allows(a))
                     for a in Action} for module in permission_modules()}
