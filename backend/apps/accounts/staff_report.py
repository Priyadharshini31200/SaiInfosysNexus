"""Consolidated This Month Expiry Report: ONE report for Barcode AutoPrint, GSTR2Tally, Mothys and every UNIVERSE project.

Definitions (shared by the report and its summary count, so they reconcile):
  * A licence belongs to the month if its expiry instant falls inside that calendar month in Asia/Kolkata (default: the current month).
  * Lifetime licences have no expiry and never appear. A licence appears once (de-duplicated by project + User ID).
  * Status is the licence's own state: Active, Expired (already past), Inactive / Cancelled / Pending where the product models them.
  * Legacy rules are untouched: Barcode AutoPrint keeps `with_expiry` (a device-product trial that was never activated has no expiry yet).
Access: Superadmin all projects; Admin the projects they may report on; restricted User only their assigned projects. Read-only."""
from datetime import datetime

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .constants import Role
from .models import StaffReportProject
from .permissions import get_role, user_can

IST = timezone.get_fixed_timezone(330)
TYPE_KEY = {"TRIAL": "Trial", "ONE_YEAR": "Yearly", "LICENSED": "Yearly", "LIFETIME": "Lifetime", "CUSTOM": "Custom"}
STATUS_LABEL = {"active": "Active", "expired": "Expired", "cancelled": "Cancelled", "inactive": "Inactive", "pending": "Pending", "suspended": "Suspended"}


class CanSeeConsolidatedExpiry(BasePermission):
    def has_permission(self, request, view):
        return get_role(request.user) in {Role.SUPERADMIN, Role.ADMIN, Role.USER}


def allowed_projects(user):
    from .staff_views import project_catalog
    catalog = project_catalog()
    role = get_role(user)
    if role == Role.SUPERADMIN:
        return catalog
    if role == Role.USER:
        codes = set(StaffReportProject.objects.filter(user=user).values_list("project_code", flat=True))
    else:  # Admin: only projects whose expiry report they may already view
        codes = {c for c in catalog if user_can(user, f"{c}:expiry", "reports")}
        from apps.tallysl.models import TallyAdminPermission
        codes |= set(TallyAdminPermission.objects.filter(user=user, can_reports=True).values_list("product__code", flat=True))
    return {c: n for c, n in catalog.items() if c in codes}


def month_bounds(month=None):
    now = timezone.now().astimezone(IST)
    year, mon = (now.year, now.month)
    if month:
        try:
            year, mon = [int(x) for x in str(month).split("-")]
            datetime(year, mon, 1)
        except (ValueError, TypeError):
            raise ValidationError({"month": ["Use YYYY-MM."]})
    start = datetime(year, mon, 1, tzinfo=IST)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=IST)
    return start, end


def _row(code, name, customer, company, user_id, ltype, expires_at, status, cid):
    today = timezone.now().astimezone(IST).date()
    return {"project": code, "product": name, "customer": customer, "company": company or "", "user_id": user_id, "license_type": TYPE_KEY.get(ltype, str(ltype)),
            "expires_at": expires_at.isoformat(), "remaining_days": (expires_at.astimezone(IST).date() - today).days, "status": STATUS_LABEL.get(status, str(status).title()), "customer_id": cid}


def rows_for(code, name, start, end):
    from apps.integrations import services as isvc
    from apps.integrations.models import IntegrationLicense, IntegrationProject
    from apps.registrations import services
    from apps.registrations.models import ProjectCustomer
    from apps.tallysl.models import TallyLicense, TallyProduct
    rows = []
    if code == "prn-auto-print":
        for c in services.with_expiry(ProjectCustomer.objects.filter(project=code, status=ProjectCustomer.ACTIVE)).filter(expires_at__gte=start, expires_at__lt=end):
            rows.append(_row(code, name, c.name, getattr(c, "company_name", ""), c.user_id, c.license_type, c.expires_at, services.state_of(c), c.pk))
    elif TallyProduct.objects.filter(code=code).exists():
        for lic in TallyLicense.objects.filter(customer__product__code=code, expires_at__gte=start, expires_at__lt=end).exclude(license_type=TallyLicense.LIFETIME).select_related("customer"):
            status = "expired" if lic.expires_at <= timezone.now() else "inactive" if lic.status in ("INACTIVE", "TRIAL_CANCELLED") else "pending" if lic.status == "PENDING" else "active"
            rows.append(_row(code, name, lic.customer.name, lic.customer.company_name, lic.customer.user_id, lic.license_type, lic.expires_at, status, lic.customer_id))
    elif IntegrationProject.objects.filter(code=code).exists():
        for lic in IntegrationLicense.objects.filter(customer__project__code=code, expires_at__gte=start, expires_at__lt=end).select_related("customer"):
            state = isvc.license_state(lic).lower()
            rows.append(_row(code, name, lic.customer.name, lic.customer.company_name, lic.customer.user_id, lic.license_type, lic.expires_at,
                             {"pending_approval": "pending"}.get(state, state), lic.customer_id))
    return rows


def consolidated(user, params):
    allowed = allowed_projects(user)
    wanted = params.get("project")
    if wanted:
        if wanted not in allowed:
            raise ValidationError({"project": ["You are not assigned to this project."]})
        scope = {wanted: allowed[wanted]}
    else:
        scope = allowed
    start, end = month_bounds(params.get("month"))
    seen, results = set(), []
    for code, name in scope.items():
        for r in rows_for(code, name, start, end):
            key = (r["project"], r["user_id"])
            if key not in seen:  # a licence is never counted twice
                seen.add(key)
                results.append(r)
    ltype, status, q = params.get("license_type"), params.get("status"), (params.get("search") or "").strip().lower()
    if ltype and ltype != "all":
        results = [r for r in results if r["license_type"].lower() == ltype.lower()]
    if status and status != "all":
        results = [r for r in results if r["status"].lower() == status.lower()]
    if q:
        results = [r for r in results if q in " ".join(str(r[k]) for k in ("customer", "company", "user_id")).lower()]
    results.sort(key=lambda r: r["expires_at"])
    return {"month": start.strftime("%Y-%m"), "month_start": start.isoformat(), "total": len(results), "projects": [{"code": c, "name": n} for c, n in allowed.items()], "results": results}


class ConsolidatedMonthExpiryView(APIView):
    permission_classes = [CanSeeConsolidatedExpiry]

    def get(self, request):
        month = request.query_params.get("month")
        if get_role(request.user) == Role.USER and month and month != month_bounds()[0].strftime("%Y-%m"):
            raise PermissionDenied("Your account can only view this month's expiry report.")
        return Response(consolidated(request.user, request.query_params))
