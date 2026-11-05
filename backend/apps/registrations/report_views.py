"""License Expiry Report (all projects) and the Excel / PDF / print downloads of it and of a project's Customer List."""
import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import Action
from apps.accounts.permissions import user_can

from . import exports, services
from .models import PROJECTS, ProjectCustomer
from .serializers import customer_data
from .views import ProjectView

logger = logging.getLogger(__name__)


class ServiceUnavailable(APIException):
    status_code = 503
    default_detail = "This data is currently unavailable."


MAX_EXPORT_ROWS = 20000
FORMATS = {
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", exports.build_xlsx),
    "pdf": ("pdf", "application/pdf", exports.build_pdf),
}


class _All(BasePermission):
    """Every listed action is needed (the Customer List views use 'any of' elsewhere)."""

    message = "You do not have permission to perform this action."
    actions = ()

    def has_permission(self, request, view):
        return all(user_can(request.user, None, a) for a in self.actions)


class CanViewReports(_All):
    actions = (Action.REPORTS,)


class CanExportReports(_All):
    actions = (Action.REPORTS, Action.EXPORT_PRINT)


def expiry_customers():
    """Every project's active licenses with an effective expiry date (not Lifetime), soonest first: expired and upcoming."""
    return services.with_expiry(
        ProjectCustomer.objects.filter(project__in=list(PROJECTS), status=ProjectCustomer.ACTIVE)
    ).select_related("created_by", "updated_by").order_by("expires_at", "id")


class LicenseExpiryReportView(APIView):
    permission_classes = [CanViewReports]

    def get(self, request):
        now = timezone.now()
        return Response(
            {
                "results": [
                    {**customer_data(c), "project": c.project, "product": PROJECTS[c.project], "expiry_status": exports.expiry_status(c, now)}
                    for c in expiry_customers()
                ]
            }
        )


def _export_request(request):
    """(format, ids, filter text) from the body; the ids are the rows currently on the user's screen, in order."""
    fmt = request.data.get("fmt")
    ids = request.data.get("ids")
    if fmt not in (*FORMATS, "print"):
        raise ValidationError({"fmt": ["Choose xlsx, pdf or print."]})
    if not isinstance(ids, list) or len(ids) > MAX_EXPORT_ROWS or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ValidationError({"ids": ["Send the list of record ids to export."]})
    filters = " ".join(str(request.data.get("filters", "")).split())[:200]
    return fmt, ids, filters


def _in_order(queryset, ids):
    by_id = {c.id: c for c in queryset.filter(id__in=ids)}
    return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]


def _deliver(table, fmt):
    if fmt == "print":
        return Response(table.as_json())
    extension, content_type, build = FORMATS[fmt]
    response = HttpResponse(build(table), content_type=content_type)
    name = exports.file_name(table, extension)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["X-Filename"] = name
    response["Cache-Control"] = "no-store"
    return response


class CustomerExportView(ProjectView):
    """The Customer List rows on screen, as .xlsx / .pdf or print data. Needs Export / Print, and View as the list itself does."""

    required_actions = {"POST": Action.EXPORT_PRINT}

    def post(self, request, project):
        if not user_can(request.user, project, Action.VIEW):
            raise PermissionDenied("You do not have permission to perform this action.")
        fmt, ids, filters = _export_request(request)
        customers = _in_order(ProjectCustomer.objects.filter(project=project).select_related("created_by"), ids)
        return _deliver(exports.customer_table(PROJECTS[project], customers, filters), fmt)


class LicenseExpiryExportView(APIView):
    permission_classes = [CanExportReports]

    def post(self, request):
        fmt, ids, filters = _export_request(request)
        return _deliver(exports.expiry_table(_in_order(expiry_customers(), ids), filters), fmt)


STATUSES = {"all": "All", "used": "Used", "not-used": "Not Used"}


def _usage_rows(params):
    """(rows, unavailable project labels, filter text) for ?project=&status=&search=, read from each project's own database."""
    from apps.dashboard import usage

    project = params.get("project") or "all"
    status = params.get("status") or "all"
    if project != "all" and project not in usage.PROJECTS:
        raise ValidationError({"project": ["Unknown project."]})
    if status not in STATUSES:
        raise ValidationError({"status": ["Choose all, used or not-used."]})
    search = " ".join(str(params.get("search") or "").split())[:100]
    codes = list(usage.PROJECTS) if project == "all" else [project]
    rows, unavailable = [], []
    for code in codes:
        try:
            rows += usage.report_rows(code, status, search)
        except usage.ProjectUnavailable:
            logger.exception("Usage report: %s data is unavailable", code)
            unavailable.append(usage.PROJECTS[code]["label"])
    if len(codes) == len(unavailable):
        raise ServiceUnavailable(f"{', '.join(unavailable)} data is currently unavailable.")
    filters = "; ".join(
        part
        for part in (
            project != "all" and f"Project: {usage.PROJECTS[project]['label']}",
            status != "all" and f"Status: {STATUSES[status]}",
            search and f"Search: {search}",
        )
        if part
    )
    return rows, unavailable, filters


class UsageReportView(APIView):
    """Used / Not Used units of every project (or one), filtered on the server."""

    permission_classes = [CanViewReports]

    def get(self, request):
        rows, unavailable, _ = _usage_rows(request.query_params)
        return Response({"results": rows, "unavailable": unavailable})


class UsageReportExportView(APIView):
    permission_classes = [CanExportReports]

    def post(self, request):
        fmt = request.data.get("fmt")
        if fmt not in (*FORMATS, "print"):
            raise ValidationError({"fmt": ["Choose xlsx, pdf or print."]})
        rows, _, filters = _usage_rows(request.data)
        return _deliver(exports.usage_table(rows, filters), fmt)
