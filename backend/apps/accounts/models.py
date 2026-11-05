from django.conf import settings
from django.db import models

from .constants import Action, Role


class UserProfile(models.Model):
    """Assigns an application role to a Django auth user."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ADMIN)

    def __str__(self):
        return f"{self.user.get_username()} ({self.role})"


class UserModulePermission(models.Model):
    """Per-user, per-module action grants. Written by the future Configuration page and
    enforced by apps.accounts.permissions.HasModulePermission. `module` is a free-form
    code (e.g. "projects") so new modules need no schema change."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="module_permissions")
    module = models.CharField(max_length=50)
    can_entry = models.BooleanField(default=True)
    can_view = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_reports = models.BooleanField(default=False)
    can_export_print = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "module"], name="uniq_user_module_permission")]

    def allows(self, action):
        return bool(getattr(self, f"can_{Action(action).value}"))


class AdminPermission(models.Model):
    """One entry in the Super Admin's global "Admin Permissions" list: a heading switched ON (every
    Admin may do the mapped action in all projects) or OFF (no Admin may). `action` is the supported
    action the heading is mapped to; a custom heading that maps to none is stored but grants nothing.
    Headings are unique, and so is each mapped action. An action with no row keeps the default
    (Entry allowed, rest denied)."""

    name = models.CharField(max_length=60)
    action = models.CharField(max_length=20, choices=Action.choices, null=True, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name"], name="uniq_admin_permission_name"),
            models.UniqueConstraint(
                fields=["action"], condition=models.Q(action__isnull=False), name="uniq_admin_permission_action"
            ),
        ]
        ordering = ["id"]
