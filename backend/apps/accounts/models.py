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
    """Per-account, per-project/menu grants. Unassigned actions are denied.
    Legacy global AdminPermission rows remain intact but no longer authorize requests."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="module_permissions")
    module = models.CharField(max_length=50)
    can_entry = models.BooleanField(default=False)
    can_view = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_extend_trial = models.BooleanField(default=False)
    can_cancel_trial = models.BooleanField(default=False)
    can_inactivate = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)
    can_device_transfer = models.BooleanField(default=False)
    can_renew = models.BooleanField(default=False)
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


class StaffProfile(models.Model):
    """Extra identity details of a staff account (Admin / User). Staff accounts are never customer identities."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class DashboardVisibilityGrant(models.Model):
    """dashboard_visibility.manage for one project. Superadmin always has it; an Admin only with a row here."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="visibility_grants")
    project_code = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "project_code"], name="uniq_visibility_grant")]


class StaffReportProject(models.Model):
    """Projects a restricted User (Report Viewer) may report on."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="report_projects")
    project_code = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "project_code"], name="uniq_staff_report_project")]


class StaffPermissionAudit(models.Model):
    """Append-only history of staff creation and every privilege change."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    target_username = models.CharField(max_length=150)
    action = models.CharField(max_length=40)
    detail = models.TextField(blank=True, default="")
    at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Staff permission audit entries are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Staff permission audit entries are append-only.")


class DeveloperProjectAccess(models.Model):
    """A project developer (external integrator) may configure ONLY the integration of the projects listed here."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="developer_projects")
    project_code = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "project_code"], name="uniq_developer_project")]


class CompanyProfile(models.Model):
    """Company branding shown in the header and My Profile. A single row (pk=1); an empty name means the default Sai Infosys Nexus branding."""
    name = models.CharField(max_length=150, blank=True, default="")
    gstin = models.CharField(max_length=15, blank=True, default="")
    logo = models.BinaryField(null=True, blank=True)
    logo_type = models.CharField(max_length=20, blank=True, default="")
    logo_version = models.CharField(max_length=16, blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]
