"""Card-visibility service + API. The server clock is authoritative for scheduled visibility (no cron, no browser dependency)."""
import re

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.utils.dateparse import parse_datetime

from apps.accounts.permissions import user_can
from .models import ProjectDashboardVisibility as V, ProjectDashboardVisibilityAudit as Audit

CODE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def can_manage(user, code):
    """dashboard_visibility.manage: Superadmin always; an Admin only with an explicit grant for that project."""
    from apps.accounts.constants import Role
    from apps.accounts.models import DashboardVisibilityGrant
    from apps.accounts.permissions import get_role
    role = get_role(user)
    if role == Role.SUPERADMIN:
        return True
    return role == Role.ADMIN and DashboardVisibilityGrant.objects.filter(user=user, project_code=code).exists()


def _known(code):
    from apps.registrations.models import PROJECTS
    from apps.integrations.models import IntegrationProject
    from apps.tallysl.models import TallyProduct
    return code in PROJECTS or IntegrationProject.objects.filter(code=code).exists() or TallyProduct.objects.filter(code=code).exists()


def _view(row, now, user, code):
    return {
        "code": code,
        "mode": row.visibility_mode if row else V.SHOW,
        "visible_from_utc": row.visible_from_utc.isoformat() if row and row.visible_from_utc else None,
        "effective": row.is_effective(now) if row else True,
        "version": row.concurrency_version if row else 0,
        "can_edit": can_manage(user, code),
    }


class CardVisibilityListView(APIView):
    """GET: effective visibility of every project that has a setting (absent = visible)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        rows = {r.project_code: r for r in V.objects.all()}
        return Response({"server_time_utc": now.isoformat(), "settings": {c: _view(r, now, request.user, c) for c, r in rows.items()}})


class CardVisibilityDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        if not CODE.match(code) or not _known(code):
            raise NotFound("Unknown project.")
        now = timezone.now()
        return Response({"server_time_utc": now.isoformat(), **_view(V.objects.filter(project_code=code).first(), now, request.user, code)})

    def put(self, request, code):
        if not CODE.match(code) or not _known(code):
            raise NotFound("Unknown project.")
        if not can_manage(request.user, code):
            raise PermissionDenied("You do not have permission to change this card's visibility.")
        mode = request.data.get("mode")
        if mode not in (V.SHOW, V.HIDDEN, V.SCHEDULED):
            raise ValidationError({"mode": ["Choose Show Now, Hide or Schedule."]})
        now = timezone.now()
        when = None
        if mode == V.SCHEDULED:
            raw = request.data.get("visible_from_utc")
            when = parse_datetime(raw) if isinstance(raw, str) else None
            if when is None or timezone.is_naive(when):
                raise ValidationError({"visible_from_utc": ["Enter a valid date and time."]})
            if when <= now:
                raise ValidationError({"visible_from_utc": ["The scheduled time must be in the future."]})
        try:
            expected = int(request.data.get("version", 0))
        except (TypeError, ValueError):
            raise ValidationError({"version": ["Invalid version."]})
        with transaction.atomic():
            row = V.objects.select_for_update().filter(project_code=code).first()
            current = row.concurrency_version if row else 0
            if current != expected:
                return Response({"message": "This setting was changed by someone else. Reload and try again.", "errors": {}}, status=status.HTTP_409_CONFLICT)
            old_mode, old_from = (row.visibility_mode, row.visible_from_utc) if row else (V.SHOW, None)
            if row is None:
                row = V(project_code=code)
            row.visibility_mode, row.visible_from_utc, row.updated_by = mode, when, request.user
            row.concurrency_version = current + 1
            row.save()
            Audit.objects.create(project_code=code, old_mode=old_mode, new_mode=mode, old_visible_from_utc=old_from, new_visible_from_utc=when, changed_by=request.user)
        return Response({"server_time_utc": timezone.now().isoformat(), **_view(row, timezone.now(), request.user, code)})


class CardVisibilityProjectsView(APIView):
    """Every registered project (hidden and scheduled included) that the caller may manage - feeds Configuration > Dashboard Visibility.
    Reads the same records as the card flip; nothing is duplicated."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.accounts.staff_views import project_catalog, project_categories
        names, categories = project_catalog(), project_categories()
        now = timezone.now()
        rows = {r.project_code: r for r in V.objects.all()}
        out = [{"name": name, "category": categories.get(code), **_view(rows.get(code), now, request.user, code)} for code, name in names.items() if can_manage(request.user, code)]
        return Response({"server_time_utc": now.isoformat(), "projects": sorted(out, key=lambda p: p["name"].lower())})
