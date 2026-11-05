"""Reusable DRF permissions. All authorization is enforced here, never only in the UI."""
from rest_framework.permissions import BasePermission

from .constants import DEFAULT_ADMIN_ACTIONS, Action, Role
from .models import AdminPermission, UserModulePermission


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
    """SUPERADMIN: everything. ADMIN: the global Admin Permissions entry for the action
    (Configuration; same in every project), else per-user grants, else the default actions."""
    role = get_role(user)
    if role == Role.SUPERADMIN:
        return True
    if role != Role.ADMIN:
        return False
    entry = AdminPermission.objects.filter(action=Action(action).value).first()
    if entry is not None:
        return entry.enabled
    grant = UserModulePermission.objects.filter(user=user, module=module).first()
    if grant is not None:
        return grant.allows(action)
    return Action(action) in DEFAULT_ADMIN_ACTIONS


def HasModulePermission(module, action):
    """Factory: `permission_classes = [HasModulePermission("projects", Action.VIEW)]`."""

    class _HasModulePermission(BasePermission):
        message = "You do not have permission to perform this action."

        def has_permission(self, request, view):
            return user_can(request.user, module, action)

    _HasModulePermission.__name__ = f"HasModulePermission_{module}_{Action(action).value}"
    return _HasModulePermission


def permission_map(user):
    """{action: bool} for every action; sent with /auth/me/ so the UI can hide actions the user
    may not perform (the API enforces them regardless). Global: the same in every project."""
    return {Action(a).value: user_can(user, None, a) for a in Action}
