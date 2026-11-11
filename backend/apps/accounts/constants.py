from django.db import models


class Role(models.TextChoices):
    SUPERADMIN = "SUPERADMIN", "Super Admin"
    ADMIN = "ADMIN", "Admin"


class Action(models.TextChoices):
    """Operations a Super Admin can grant per module (future Configuration page)."""

    ENTRY = "entry", "Entry"
    VIEW = "view", "View"
    EDIT = "edit", "Edit"
    DELETE = "delete", "Delete"
    EXTEND_TRIAL = "extend_trial", "Extend Trial"
    CANCEL_TRIAL = "cancel_trial", "Cancel Trial"
    INACTIVATE = "inactivate", "Inactivate"
    APPROVE = "approve", "Approve / Reject"
    DEVICE_TRANSFER = "device_transfer", "Device Transfer / Revoke"
    RENEW = "renew", "Renew License"
    REPORTS = "reports", "Reports"
    EXPORT_PRINT = "export_print", "Export / Print"


# Admins hold only these actions on a module until a Super Admin grants more.
DEFAULT_ADMIN_ACTIONS = frozenset()

# Fixed bootstrap accounts (the 9-character serial-number policy applies to future users only).
BOOTSTRAP_ACCOUNTS = (
    ("Superadmin", Role.SUPERADMIN),
    ("Admin", Role.ADMIN),
)


# Global Admin Permissions (Configuration): any action can be listed as "<Action> Access" and
# switched ON/OFF for the Admin role across all projects.
CONFIGURABLE_ACTIONS = tuple(Action)


def permission_heading(action):
    return f"{Action(action).label} Access"
