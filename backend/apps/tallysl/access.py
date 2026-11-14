"""Project-wise (per Tally SL product) authorisation for staff. Superadmin: everything. Admin: only what a Superadmin
granted for THAT product. Enforced on every staff endpoint - never by hiding menus alone."""
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.accounts.constants import Role
from apps.accounts.permissions import get_role

from .models import PERMISSION_ACTIONS, TallyAdminPermission, TallyProduct


def _grants(user):
    return {g.product_id: g for g in TallyAdminPermission.objects.filter(user=user)}


def can(user, product, action):
    role = get_role(user)
    if role == Role.SUPERADMIN:
        return True
    if role != Role.ADMIN or action not in PERMISSION_ACTIONS:
        return False
    grant = TallyAdminPermission.objects.filter(user=user, product=product).first()
    return bool(grant and getattr(grant, f"can_{action}"))


def permitted_products(user, action):
    """Queryset of the active products where `user` holds `action`."""
    products = TallyProduct.objects.all()
    role = get_role(user)
    if role == Role.SUPERADMIN:
        return products
    if role != Role.ADMIN or action not in PERMISSION_ACTIONS:
        return products.none()
    return products.filter(admin_permissions__user=user, **{f"admin_permissions__can_{action}": True})


def require(user, product, action):
    if not can(user, product, action):
        raise PermissionDenied("You do not have permission to perform this action.")


def access_map(user):
    """{product code: {action: bool}} - navigation hints only."""
    role = get_role(user)
    grants = _grants(user) if role == Role.ADMIN else {}
    out = {}
    for product in TallyProduct.objects.filter(active=True):
        g = grants.get(product.pk)
        out[product.code] = {a: role == Role.SUPERADMIN or bool(g and getattr(g, f"can_{a}")) for a in PERMISSION_ACTIONS}
    return out


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        return get_role(request.user) in {Role.SUPERADMIN, Role.ADMIN}


class IsSuperAdminOnly(BasePermission):
    message = "Super Admin access is required."

    def has_permission(self, request, view):
        return get_role(request.user) == Role.SUPERADMIN
