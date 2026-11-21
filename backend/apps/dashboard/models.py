"""Overall Dashboard project-card visibility. Purely presentational: it never touches projects, licenses, customers or metrics.

One row per project code. A project with no row is visible (`show`), so every existing card stays visible with no data migration."""
from django.conf import settings
from django.db import models


class ProjectDashboardVisibility(models.Model):
    SHOW, HIDDEN, SCHEDULED = "show", "hidden", "scheduled"
    MODES = [(SHOW, "Show"), (HIDDEN, "Hidden"), (SCHEDULED, "Scheduled")]

    project_code = models.CharField(max_length=64, unique=True)
    visibility_mode = models.CharField(max_length=10, choices=MODES, default=SHOW)
    visible_from_utc = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at_utc = models.DateTimeField(auto_now=True)
    concurrency_version = models.PositiveIntegerField(default=1)

    def is_effective(self, now):
        if self.visibility_mode == self.SHOW:
            return True
        if self.visibility_mode == self.HIDDEN:
            return False
        return self.visible_from_utc is not None and now >= self.visible_from_utc


class ProjectDashboardVisibilityAudit(models.Model):
    """Append-only history of every visibility change."""

    project_code = models.CharField(max_length=64, db_index=True)
    old_mode = models.CharField(max_length=10)
    new_mode = models.CharField(max_length=10)
    old_visible_from_utc = models.DateTimeField(null=True)
    new_visible_from_utc = models.DateTimeField(null=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at_utc = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Visibility audit entries are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Visibility audit entries are append-only.")
