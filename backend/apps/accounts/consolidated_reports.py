"""Read-only, project-scoped adapters for the existing Nexus reporting sources."""
from datetime import date, datetime, timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.integrations import usage, usage_reports
from apps.integrations.models import IntegrationProject, IntegrationTallyInfo
from apps.registrations import exports, services as registration
from apps.registrations.models import PROJECTS, ProjectCustomer
from apps.registrations.report_views import FORMATS, MAX_EXPORT_ROWS, _deliver
from apps.tallysl import services as tally
from apps.tallysl.models import TallyAdminPermission, TallyCustomer, TallyProduct, TallyUsageEvent

from .constants import Role
from .models import StaffReportProject, UserModulePermission
from .permissions import get_role
from .staff_report import IST, TYPE_KEY, CanSeeConsolidatedExpiry, month_bounds

REPORTS = {
    "expiry": ("License Expiry Report", (
        ("customer", "Customer"), ("product", "Project"), ("license_type", "License Type"),
        ("purchase_date", "Purchase Date"), ("expiry", "Expiry Date"), ("remaining_days", "Remaining Days"), ("status", "Status"),
    )),
    "tally": ("Tally Details Report", (
        ("customer", "Customer"), ("product", "Project"), ("serial", "Tally Serial"), ("edition", "Edition"),
        ("tss_details", "TSS / Software Services"), ("administrator", "Administrator"), ("tss_expiry", "TSS Expiry"),
    )),
    "performance": ("User Performance Report", (
        ("product", "Project"), ("customer", "Customer"), ("category", "Category"),
        ("login_count", "Login Count"), ("last_used", "Last Used"), ("active_minutes", "Active Time (min)"),
    )),
}
DATE_FIELDS = {"expiry", "tss_expiry", "purchase_date", "activation"}
DATE_DEFAULT = {"expiry": "expiry", "tally": "tss_expiry", "performance": "activation"}


def _date(value):
    return value.astimezone(IST).date() if isinstance(value, datetime) else value


def _bounds(params, today):
    period = params.get("period") or "this_month"
    start, end = (_date(d) for d in month_bounds())
    if period == "this_month":
        return start, end
    if period == "next_month":
        return end, (end + timedelta(days=32)).replace(day=1)
    if period == "this_year":
        return date(today.year, 1, 1), date(today.year + 1, 1, 1)
    if period == "custom":
        try:
            start, end = date.fromisoformat(params.get("from", "")), date.fromisoformat(params.get("to", ""))
            if start <= end:
                return start, end + timedelta(days=1)
        except (ValueError, OverflowError):
            pass
        raise ValidationError({"period": "Enter a valid From and To date in order."})
    raise ValidationError({"period": "Choose a supported reporting period."})


def _catalog():
    projects = {code: (name, "legacy", None, False) for code, name in PROJECTS.items()}
    projects.update({p.code: (p.name, "tally", p, True) for p in TallyProduct.objects.filter(active=True)})
    projects.update({p.code: (p.name, "integration", p, p.tally_serial_required) for p in IntegrationProject.objects.all()})
    return projects


def _grants(user, catalog, role):
    assigned = set(StaffReportProject.objects.filter(user=user).values_list("project_code", flat=True)) if role == Role.USER else set()
    modules = {g.module: g for g in UserModulePermission.objects.filter(user=user)} if role == Role.ADMIN else {}
    tally_codes = set(TallyAdminPermission.objects.filter(user=user, can_reports=True).values_list("product__code", flat=True)) if role == Role.ADMIN else set()
    view = {report: {} for report in REPORTS}
    export = {report: set() for report in REPORTS}
    for code, (name, source, project, is_tally) in catalog.items():
        for report in REPORTS:
            if report == "tally" and not is_tally:
                continue
            if role == Role.USER:
                if report == "expiry" and code in assigned:
                    view[report][code] = (name, source, project, is_tally)
                continue
            module = modules.get(f"{code}:{'expiry' if report == 'expiry' else 'usage'}")
            if source == "tally":
                allowed = downloadable = code in tally_codes
            else:
                allowed = bool(module and module.allows("reports"))
                downloadable = bool(allowed and module.allows("export_print"))
            if role == Role.SUPERADMIN or allowed:
                view[report][code] = (name, source, project, is_tally)
            if role == Role.SUPERADMIN or downloadable:
                export[report].add(code)
    return view, export


def _base(customer, serial, expiry, activation, license_type, status):
    return {
        "customer": customer.name, "email": customer.email, "user_id": customer.user_id,
        "serial": serial, "expiry": _date(expiry), "activation": _date(activation), "purchase_date": None,
        "license_type": TYPE_KEY.get(license_type, license_type), "status": status.replace("_", " ").title(),
    }


def _tally_rows(project, performance):
    customers = TallyCustomer.objects.filter(product=project).select_related("license", "tally_info")
    if performance:
        customers = customers.annotate(login_count=Count("usage_events", filter=Q(usage_events__kind="login"), distinct=True))
        readings = tally.usage_rows(customers)
    else:
        readings = ((customer, {}) for customer in customers)
    for customer, stats in readings:
        lic = getattr(customer, "license", None)
        info = getattr(customer, "tally_info", None)
        expiry = lic.expires_at if lic and lic.license_type != lic.LIFETIME else None
        row = _base(customer, customer.tally_serial, expiry, getattr(lic, "activated_at", None),
                    getattr(lic, "license_type", None), tally.license_state(lic) if lic else "pending")
        yield dict(row, tss_expiry=getattr(info, "tss_expiry_date", None), edition=getattr(info, "edition", None),
                   administrator=getattr(info, "license_administrator", None), tss_details=getattr(info, "tss_details", None),
                   tally_email=getattr(info, "registered_email", None), login_count=getattr(customer, "login_count", None),
                   last_used=_date(stats.get("last_opened")), category=tally.CATEGORY_LABELS.get(stats.get("category")))


def _integration_rows(project, performance, start, end):
    stats = usage_reports.usage_report(project, {"from": start.isoformat(), "to": (end - timedelta(days=1)).isoformat()}) if performance else {}
    duration = {r["customer_id"]: r["active_minutes"] for r in stats.get("customers", [])}
    details = {r.customer_id: r for r in IntegrationTallyInfo.objects.filter(customer__project=project)} if project.tally_serial_required else {}
    for r in usage.customer_rows(project):
        info = details.get(r["id"])
        yield {
            "customer": r["name"], "email": r["email"], "user_id": r["user_id"],
            "serial": info.tally_serial if info else r["serial_number"] if not project.tally_serial_required else None,
            "expiry": _date(r["expires_at"]) if r["license_type"] != "LIFETIME" else None,
            "activation": _date(r["activated_at"]), "purchase_date": None,
            "license_type": TYPE_KEY.get(r["license_type"], r["license_type"]), "status": r["status"].replace("_", " ").title(),
            "tss_expiry": info.tss_expiry if info else None, "edition": info.edition if info else None,
            "administrator": info.license_admin if info else None, "tss_details": ("Active" if info.tss_active else "Inactive") if info else None,
            "login_count": r["login_count"], "category": (r["usage_category"] or "").replace("_", "-").title() or None,
            "last_used": _date(r["last_opened"]), "active_minutes": duration.get(r["id"]),
        }


def _legacy_rows(code, performance):
    customers = ProjectCustomer.objects.filter(project=code)
    if performance:
        customers = customers.annotate(last_used=Max("devices__last_used_at"))
    for c in customers:
        row = _base(c, c.serial_number, registration.effective_expiry(c), c.first_activated_at if code == "prn-auto-print" else c.activated_at,
                    c.get_license_type_display(), registration.state_of(c))
        # Barcode's entitlement API explicitly exposes activated_at as purchased_at for the issued package.
        row["purchase_date"] = _date(c.activated_at) if code == "prn-auto-print" and c.license_type != c.TRIAL else None
        yield dict(row, last_used=_date(getattr(c, "last_used", None)))


def _matches(value, start, end):
    return bool(value and start <= value < end)


def _collect(scope, report, start, end, field, term, today):
    performance = report == "performance"
    rows = []
    for code, (name, source, project, _) in scope.items():
        if source == "tally":
            data = _tally_rows(project, performance)
        elif source == "integration":
            data = _integration_rows(project, performance, start, end)
        else:
            data = _legacy_rows(code, performance)
        for row in data:
            if not _matches(row.get(field), start, end):
                continue
            if term and not any(term in str(row.get(key) or "").casefold() for key in ("customer", "user_id", "serial", "email", "tally_email")):
                continue
            expiry = row["expiry"]
            rows.append(dict(row, project=code, product=name, remaining_days=(expiry - today).days if expiry else None))
    rows.sort(key=lambda r: (r[field], r["product"].casefold(), r["customer"].casefold(), r["user_id"]))
    return rows


def consolidated_report(user, params, exporting=False):
    role = get_role(user)
    view, export = _grants(user, _catalog(), role)
    report = params.get("report") or "expiry"
    if report not in REPORTS:
        raise ValidationError({"report": "Choose a supported report."})
    field = params.get("filter_by") or DATE_DEFAULT[report]
    if field not in DATE_FIELDS:
        raise ValidationError({"filter_by": "Choose a supported date field."})
    restricted = role == Role.USER
    if restricted and (report != "expiry" or field != "expiry" or (params.get("period") or "this_month") != "this_month" or exporting):
        raise PermissionDenied("Your account can only view this month's license expiry report.")
    projects = view[report]
    if not projects and not restricted and role != Role.SUPERADMIN:
        raise PermissionDenied("You do not have access to this report.")
    project = params.get("project") or "all"
    if project != "all" and project not in projects:
        raise PermissionDenied("You do not have access to this project report.")
    kind = params.get("project_type") or "all"
    if kind not in {"all", "tally", "non_tally"}:
        raise ValidationError({"project_type": "Choose a supported project type."})
    scope = {code: item for code, item in projects.items() if (project == "all" or project == code)
             and (kind == "all" or item[3] == (kind == "tally"))}
    can_export = bool(scope) and set(scope) <= export[report]
    if exporting and not can_export:
        raise PermissionDenied("Export permission is required for every selected project.")
    today = _date(timezone.now())
    start, end = _bounds(params, today)
    term = (params.get("search") or "").strip()[:100].casefold()
    rows = _collect(scope, report, start, end, field, term, today)
    sort = str(params.get("sort") or "")
    if sort.lstrip("-") in {key for key, _ in REPORTS[report][1]}:  # optional column sort over ALL matching rows (default order unchanged)
        key = sort.lstrip("-")
        present = [r for r in rows if r.get(key) not in (None, "")]
        present.sort(key=lambda r: r[key].casefold() if isinstance(r[key], str) else r[key], reverse=sort.startswith("-"))
        rows = present + [r for r in rows if r.get(key) in (None, "")]  # missing values always last
    month_start, month_end = (_date(d) for d in month_bounds())
    summary = {"license_month": sum(_matches(r["expiry"], month_start, month_end) for r in rows)}
    if not restricted:
        summary.update(
            tss_month=sum(_matches(r.get("tss_expiry"), month_start, month_end) for r in rows),
            expired=sum(r["status"] == "Expired" for r in rows),
            purchased_month=sum(_matches(r["purchase_date"], month_start, month_end) for r in rows)
            if any(code == "prn-auto-print" for code in scope) else None,
        )
    title, columns = REPORTS[report]
    if exporting:
        return title, columns, rows, (start, end)
    try:
        page = max(1, int(params.get("page", 1)))
    except (TypeError, ValueError):
        raise ValidationError({"page": "Enter a valid page number."})
    size = 25
    results = [{key: row.get(key) for key, _ in columns} for row in rows[(page-1)*size:page*size]]
    return {
        "title": title, "columns": columns, "summary": summary, "total": len(rows), "page_size": size,
        "projects": [{"code": c, "name": p[0], "tally": p[3]} for c, p in sorted(projects.items(), key=lambda item: item[1][0].casefold())],
        "reports": [r for r, project_grants in view.items() if project_grants or role == Role.SUPERADMIN or (restricted and r == "expiry")],
        "can_export": can_export, "results": results, "from": start, "to": end - timedelta(days=1),
    }


class ConsolidatedReportsView(APIView):
    permission_classes = [CanSeeConsolidatedExpiry]

    def get(self, request):
        if request.query_params.get("meta") == "1":
            role = get_role(request.user)
            view, _ = _grants(request.user, _catalog(), role)
            return Response({"reports": [report for report, projects in view.items()
                                         if projects or role == Role.SUPERADMIN or (role == Role.USER and report == "expiry")]})
        return Response(consolidated_report(request.user, request.query_params))

    def post(self, request):
        fmt = request.data.get("fmt")
        if fmt not in (*FORMATS, "print"):
            raise ValidationError({"fmt": "Choose xlsx, pdf or print."})
        filters = request.data.get("filters", {})
        if not isinstance(filters, dict) or any(not isinstance(value, str) for value in filters.values()):
            raise ValidationError({"filters": "Use text values for report filters."})
        title, columns, rows, (start, end) = consolidated_report(request.user, filters, exporting=True)
        if len(rows) > MAX_EXPORT_ROWS:
            raise ValidationError({"filters": "Narrow the filters before exporting more than 20,000 rows."})
        cells = [[str(row[key]) if row.get(key) not in (None, "") else "Not available" for key, _ in columns] for row in rows]
        if fmt == "xlsx":
            cells = [["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) and not value.lstrip("-").isdigit()
                      else value for value in row] for row in cells]
        subtitle = f"{len(rows)} records | {start:%d %b %Y} to {end - timedelta(days=1):%d %b %Y}"
        return _deliver(exports.Table(f"Nexus {title}", subtitle, [label for _, label in columns], cells, [1] * len(columns)), fmt)
