"""Staff API behind the product pages (Dashboard / Customers) of an API-connected product such as Mothys.
Everything is scoped by the project code in the URL; the customer and licence rules are the ones in `services` (the same ones the
product's own backend triggers through /integrations/v1/). Super Admin: everything; Admin: only explicitly granted project permissions."""
from django.http import HttpResponse
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.accounts.constants import Role
from apps.accounts.permissions import get_role, user_can

from apps.registrations import exports

from . import services as s
from . import usage
from .admin_views import AdminView
from .models import IntegrationAudit, IntegrationCustomer, IntegrationProject, IntegrationSession, IntegrationUsageEvent

PAGE_SIZES = (10, 25, 50, 100)
TYPE_LABEL = {"TRIAL": "Trial", "ONE_YEAR": "1 Year", "LIFETIME": "Lifetime", "CUSTOM": "Custom"}
STATUS_LABEL = {"pending": "Pending Approval", "not_activated": "Not Activated", "active": "Active", "expired": "Expired", "cancelled": "Cancelled", "inactive": "Inactive"}
CATEGORY_LABEL = {"speed": "Speed", "normal": "Normal", "new": "New", "one_time": "One-Time"}


def _filtered(project, params):
    rows = usage.customer_rows(project)
    search = " ".join(str(params.get("search", "")).split()).lower()
    if search:
        rows = [r for r in rows if search in " ".join(str(r[k]) for k in ("name", "email", "contact_number", "serial_number", "external_customer_id")).lower()]
    if params.get("license_type") not in (None, "", "all"):
        rows = [r for r in rows if r["license_type"] == params["license_type"]]
    if params.get("status") not in (None, "", "all"):
        rows = [r for r in rows if r["status"] == params["status"]]
    if params.get("filter") in usage.CATEGORY_FILTERS:  # a Dashboard count
        rows = [r for r in rows if usage.matches(r, params["filter"])]
    if params.get("usage") in CATEGORY_LABEL:
        rows = [r for r in rows if r["usage_category"] == params["usage"]]
    return sorted(rows, key=lambda r: -r["id"])


def _row_out(r):
    out = {k: r[k] for k in ("id", "license_id", "external_customer_id", "name", "email", "contact_number", "serial_number", "user_id", "license_type", "status",
                             "activated_at", "expires_at", "last_login", "active_devices", "active_device", "welcome_email_status", "created_at")}
    out["starts_at"] = r["starts_at"]
    out["trial_days"] = r["trial_days"]
    out["usage_category"] = r["usage_category"] or ("not_used" if not r["used"] else None)
    return out


class ProductAccess(BasePermission):
    """Superadmin: everything (unchanged). Admin: only what was explicitly granted for the project named in the URL
    (UserModulePermission rows `<CODE>`, `<CODE>:dashboard`, `<CODE>:expiry`, `<CODE>:usage`). Anything else: 403."""

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        role = get_role(request.user)
        if role == Role.SUPERADMIN:
            return True
        if role != Role.ADMIN:
            return False
        code = str(view.kwargs.get("code", "")).upper()
        needed = view.required(request, code) if hasattr(view, "required") else None
        if not needed or not IntegrationProject.objects.filter(code=code).exists():
            return False
        return all(user_can(request.user, module, action) for module, action in needed)


def _report_module(code, kind):
    return f"{code}:expiry" if kind == "expiry" else f"{code}:usage"


class ProductView(AdminView):
    permission_classes = [ProductAccess]

    def required(self, request, code):
        return None  # deny by default for Admins; each view declares what it needs

    def customer(self, code, pk):
        c = IntegrationCustomer.objects.select_related("license", "project").filter(project=self.project(code), pk=pk).first()
        if not c:
            raise NotFound("Unknown customer.")
        return c


class ProductDashboardView(ProductView):
    def required(self, request, code):
        return [(f"{code}:dashboard", "view")]

    def get(self, request, code):
        return Response(usage.project_stats(self.project(code)))


class ProductCustomersView(ProductView):
    def required(self, request, code):
        return [(code, "view")]

    def get(self, request, code):
        rows = _filtered(self.project(code), request.query_params)
        try:
            page, size = max(int(request.query_params.get("page", 1)), 1), int(request.query_params.get("page_size", 25))
        except ValueError:
            raise ValidationError({"page": ["Page and page size must be numbers."]})
        if size not in PAGE_SIZES:
            raise ValidationError({"page_size": [f"Page size must be one of {', '.join(map(str, PAGE_SIZES))}."]})
        return Response({"total": len(rows), "page": page, "page_size": size, "results": [_row_out(r) for r in rows[(page - 1) * size: page * size]]})


class ProductExportView(ProductView):
    def required(self, request, code):
        return [(code, "view"), (code, "export_print")]

    def post(self, request, code):
        rows = _filtered(self.project(code), request.data.get("filters") or {})
        fmt = request.data.get("fmt")
        d = lambda v: v.strftime("%d/%m/%Y") if v else "—"  # noqa: E731
        body = [[str(i), r["name"], r["email"], r["contact_number"], r["serial_number"], TYPE_LABEL[r["license_type"]], STATUS_LABEL[r["status"]], d(r["activated_at"]),
                 "Never" if r["license_type"] == "LIFETIME" else d(r["expires_at"]), d(r["last_login"]), CATEGORY_LABEL.get(r["usage_category"], "Not Used" if not r["used"] else "—")]
                for i, r in enumerate(rows, 1)]
        table = exports.Table(f"{self.project(code).name} Customers", exports._subtitle(len(body), ""),
                              ["S.No", "Customer", "Email", "Contact", "Serial / User ID", "License Type", "License Status", "Activation Date", "Expiry Date", "Last Login", "Usage"],
                              body, [3, 11, 14, 8, 9, 7, 8, 8, 8, 8, 7])
        if fmt == "print":
            return Response(table.as_json())
        if fmt == "xlsx":
            data, ctype = exports.build_xlsx(table), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "pdf":
            data, ctype = exports.build_pdf(table), "application/pdf"
        else:
            raise ValidationError({"fmt": ["Choose xlsx, pdf or print."]})
        name = exports.file_name(table, fmt)
        resp = HttpResponse(data, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        resp["X-Filename"] = name
        return resp


class ProductCustomerDetailView(ProductView):
    def required(self, request, code):
        return [(code, "view" if request.method == "GET" else "edit")]

    """Customer + licence + device activation history + device transfer history + usage history."""

    def get(self, request, code, pk):
        c = self.customer(code, pk)
        row = next(r for r in usage.customer_rows(c.project) if r["id"] == c.pk)
        lic = c.license
        sessions = IntegrationSession.objects.filter(license=lic).order_by("-id")[:100]
        transfers = IntegrationAudit.objects.filter(project=c.project, action="transfer.confirmed", subject=c.user_id).order_by("-id")[:50]
        events = IntegrationUsageEvent.objects.filter(license=lic).order_by("-occurred_at")[:100]

        def parts(detail):
            return dict(p.split("=", 1) for p in (detail or "").split(";") if "=" in p)
        return Response({
            "customer": {**_row_out(row), "company": None, "must_change_password": c.must_change_password, "locked": bool(c.locked_until)},
            "license": {**s.license_payload(lic), "status": row["status"], "trial_days_default": c.project.trial_days},
            "activations": [{"device_name": x.device_name or x.device_id, "device_id": x.device_id, "activated_at": x.created_at, "status": x.status,
                             "ended_at": x.revoked_at, "ended_reason": x.revoked_reason, "last_seen_at": x.last_seen_at} for x in sessions],
            "transfers": [{"at": t.created_at, "from_device": parts(t.detail).get("from", "—"), "to_device": parts(t.detail).get("to", "—")} for t in transfers],
            "usage": [{"at": e.occurred_at, "event": e.event_type, "device": e.device_id or "—"} for e in events],
        })

    def patch(self, request, code, pk):
        c = s.edit_customer(self.customer(code, pk), request.data, self.actor())
        return Response({"customer": _row_out(next(r for r in usage.customer_rows(c.project) if r["id"] == c.pk))})


class ProductCustomerActionView(ProductView):
    NEEDS = {"extend-trial": "extend_trial", "cancel-trial": "cancel_trial", "renew": "renew", "set-license": "edit", "approve": "approve",
             "inactivate": "inactivate", "reactivate": "inactivate", "resend-credentials": "edit"}

    def required(self, request, code):
        action = self.NEEDS.get(self.kwargs.get("action"))
        return [(code, action)] if action else None

    ACTIONS = ("extend-trial", "cancel-trial", "renew", "set-license", "approve", "inactivate", "reactivate", "resend-credentials")

    def post(self, request, code, pk, action):
        c = self.customer(code, pk)
        lic, who, body = c.license, self.actor(), request.data
        if action == "extend-trial":
            s.extend_trial(lic, body.get("days"), who)
        elif action == "cancel-trial":
            s.cancel_trial(lic, who)
        elif action == "renew":
            s.renew_license(lic, who)
        elif action == "set-license":
            s.set_license_type(lic, body.get("license_type"), body.get("trial_days"), who, years=body.get("years"))
        elif action == "approve":
            s.approve_license(lic, body.get("license_type"), who)
        elif action == "inactivate":
            s.set_customer_active(c, False, who)
        elif action == "reactivate":
            s.set_customer_active(c, True, who)
        elif action == "resend-credentials":
            if not c.must_change_password:
                raise s.IntegrationError("INVALID_STATE", "This customer already chose their own password; credentials cannot be resent.", 409)
            return Response({"welcome_email": s.resend_welcome(c, who)})
        else:
            raise NotFound("Unknown action.")
        c = IntegrationCustomer.objects.select_related("license", "project").get(pk=c.pk)
        return Response({"customer": _row_out(next(r for r in usage.customer_rows(c.project) if r["id"] == c.pk))})


# ---------------------------------------------------------------- Reports (mirror of the GSTR report patterns, Mothys data only)
REPORTS = {
    "expiry": ("License Expiry Report", ["S.No", "Customer Name", "Email", "Contact Number", "Serial Number", "License Type", "Activation Date", "Expiry Date", "License Status"], [3, 10, 13, 8, 8, 7, 8, 8, 8]),
    "details": ("Customer Details Report", ["S.No", "Customer Name", "Email", "Contact Number", "Mothys Customer ID", "Nexus User ID / Serial Number", "License Type", "Active Device", "Registration Date", "Last Login"],
                [3, 10, 13, 8, 9, 9, 7, 9, 8, 8]),
    "performance": ("User Performance Report", ["S.No", "Customer Name", "Serial Number", "User Category", "Login Count", "Last Active Date", "Usage History", "Active Device"], [3, 11, 9, 8, 6, 9, 12, 10]),
}


def _day(v):
    return v.strftime("%d/%m/%Y") if v else "—"


def _report_cells(kind, r):
    if kind == "expiry":
        return [r["name"], r["email"], r["contact_number"], r["serial_number"], TYPE_LABEL[r["license_type"]], _day(r["activated_at"]),
                "Never" if r["license_type"] == "LIFETIME" else _day(r["expires_at"]), STATUS_LABEL[r["status"]]]
    if kind == "details":
        return [r["name"], r["email"], r["contact_number"], r["external_customer_id"], r["serial_number"], TYPE_LABEL[r["license_type"]],
                r["active_device"] or "—", _day(r["created_at"]), _day(r["last_login"])]
    history = f'{r["active_days"]} active day{"" if r["active_days"] == 1 else "s"} · {r["opens"]} open{"" if r["opens"] == 1 else "s"}' if r["used"] else "No usage yet"
    return [r["name"], r["serial_number"], CATEGORY_LABEL.get(r["usage_category"], "Not Used" if not r["used"] else "Normal"), str(r["login_count"]),
            _day(r["last_opened"]), history, r["active_device"] or "—"]


def _report_rows(project, kind, p):
    from datetime import date
    rows = usage.customer_rows(project)
    search = " ".join(str(p.get("search", "")).split()).lower()
    if search:
        rows = [r for r in rows if search in f'{r["name"]} {r["serial_number"]} {r["email"]}'.lower()]
    if p.get("license_type") not in (None, "", "all"):
        rows = [r for r in rows if r["license_type"] == p["license_type"]]
    if p.get("status") not in (None, "", "all"):
        rows = [r for r in rows if r["status"] == p["status"]]
    if p.get("category") in CATEGORY_LABEL:
        rows = [r for r in rows if r["usage_category"] == p["category"]]
    if p.get("usage") == "used":
        rows = [r for r in rows if r["used"]]
    elif p.get("usage") == "not_used":
        rows = [r for r in rows if not r["used"]]
    key = {"expiry": "expires_at", "details": "created_at", "performance": "last_opened"}[kind]  # what the From / To dates apply to
    try:
        lo = date.fromisoformat(p["from"]) if p.get("from") else None
        hi = date.fromisoformat(p["to"]) if p.get("to") else None
    except ValueError:
        raise ValidationError({"from": ["Use a valid date."]})
    if lo:
        rows = [r for r in rows if r[key] and r[key].date() >= lo]
    if hi:
        rows = [r for r in rows if r[key] and r[key].date() <= hi]
    return sorted(rows, key=lambda r: -r["id"])


class ProductReportView(ProductView):
    def required(self, request, code):
        return [(_report_module(code, self.kwargs.get("kind")), "reports")]

    def get(self, request, code, kind):
        if kind not in REPORTS:
            raise NotFound("Unknown report.")
        rows = _report_rows(self.project(code), kind, request.query_params)
        try:
            page, size = max(int(request.query_params.get("page", 1)), 1), int(request.query_params.get("page_size", 25))
        except ValueError:
            raise ValidationError({"page": ["Page and page size must be numbers."]})
        if size not in PAGE_SIZES:
            raise ValidationError({"page_size": [f"Page size must be one of {', '.join(map(str, PAGE_SIZES))}."]})
        out = [{"id": r["id"], "cells": _report_cells(kind, r)} for r in rows[(page - 1) * size: page * size]]
        return Response({"total": len(rows), "page": page, "page_size": size, "columns": REPORTS[kind][1], "results": out})


class ProductReportExportView(ProductView):
    def required(self, request, code):
        module = _report_module(code, self.kwargs.get("kind"))
        return [(module, "reports"), (module, "export_print")]

    def post(self, request, code, kind):
        if kind not in REPORTS:
            raise NotFound("Unknown report.")
        project = self.project(code)
        rows = _report_rows(project, kind, request.data.get("filters") or {})
        title, heads, weights = REPORTS[kind]
        table = exports.Table(f"{project.name} – {title}", exports._subtitle(len(rows), ""), heads, [[str(i)] + _report_cells(kind, r) for i, r in enumerate(rows, 1)], weights)
        fmt = request.data.get("fmt")
        if fmt == "print":
            return Response(table.as_json())
        if fmt not in ("xlsx", "pdf"):
            raise ValidationError({"fmt": ["Choose xlsx, pdf or print."]})
        data, ctype = (exports.build_xlsx(table), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") if fmt == "xlsx" else (exports.build_pdf(table), "application/pdf")
        name = exports.file_name(table, fmt)
        resp = HttpResponse(data, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        resp["X-Filename"] = name
        return resp


class UsageReportView(ProductView):
    def required(self, request, code):
        return [(f"{code}:usage", "reports")]

    def get(self, request, code):
        from . import usage_reports
        return Response(usage_reports.usage_report(self.project(code), request.query_params))


class DeviceTransferReportView(ProductView):
    def required(self, request, code):
        return [(f"{code}:usage", "reports")]

    def get(self, request, code):
        from . import usage_reports
        return Response(usage_reports.device_transfer_history(self.project(code), request.query_params))
