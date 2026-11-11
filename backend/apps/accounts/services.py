"""Business logic for authentication. Views stay thin and call into this module."""
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction

from .constants import CONFIGURABLE_ACTIONS, permission_heading
from .models import AdminPermission, UserProfile
from .passwords import normalize, set_user_password


class InvalidCredentials(Exception):
    pass


def authenticate_user(request, username, password):
    user = authenticate(request, username=username, password=password)
    if user is None or not UserProfile.objects.filter(user=user).exists():
        raise InvalidCredentials()
    return user


def validate_new_password(user, current_password, new_password):
    """Returns a list of error messages (empty when acceptable)."""
    if not new_password or not new_password.strip():
        return ["Password is required."]
    if normalize(new_password) == normalize(current_password):
        return ["New password must be different from the current password."]
    return []  # no length or complexity rules: any non-empty password, case-insensitive


def set_password(user, new_password):
    set_user_password(user, new_password)
    user.save(update_fields=["password"])


def ensure_account(username, role, initial_password):
    """Idempotent: creates the user only if absent (case-insensitive). Never touches an
    existing password. Returns (user, created)."""
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.filter(username__iexact=username).first()
        created = user is None
        if created:
            user = User(username=username, is_active=True)
            set_user_password(user, initial_password)
            user.save()
        UserProfile.objects.get_or_create(user=user, defaults={"role": role})
    return user, created


# ---- Admin Permissions (Configuration)
class DuplicatePermission(Exception):
    pass


def clean_permission_name(name):
    return " ".join(name.split())


def list_admin_permissions():
    """`mapped` is False for a custom heading that is not linked to a supported action: it can be
    switched, but grants nothing until it is mapped."""
    return [
        {"id": row.id, "name": row.name, "enabled": row.enabled, "mapped": row.action is not None}
        for row in AdminPermission.objects.all()
    ]


def add_admin_permission(name):
    """Adds a heading as an ON entry. A heading that reads "<Action> Access" is mapped to that
    action; any other heading stays unmapped. Raises DuplicatePermission for a name already listed
    (compared ignoring case and extra spaces) or an action that already has its heading."""
    name = clean_permission_name(name)
    action = next((a.value for a in CONFIGURABLE_ACTIONS if permission_heading(a).lower() == name.lower()), None)
    try:
        with transaction.atomic():
            if any(n.lower() == name.lower() for n in AdminPermission.objects.values_list("name", flat=True)):
                raise DuplicatePermission()
            return AdminPermission.objects.create(name=name, action=action, enabled=True)
    except IntegrityError:
        raise DuplicatePermission()


def set_admin_permission(pk, enabled):
    """Returns False when that entry is not in the list."""
    return bool(AdminPermission.objects.filter(pk=pk).update(enabled=enabled))
