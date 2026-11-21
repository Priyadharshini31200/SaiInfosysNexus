"""UNIVERSE, customer-centric API. One dynamic implementation for every UNIVERSE project (no per-project code):

    GET  /integrations/admin/universe/projects/                       sidebar registry (permission-aware)
    GET  /integrations/admin/universe/cards/                          Overall Dashboard project cards + licence rows for the six tiles
    GET  /integrations/admin/projects/<code>/universe-dashboard/      Speed / Normal / New / One-Time + Total / Last 7 Days / Pending / Transfers
    GET  /integrations/admin/projects/<code>/universe-customers/      Customers list (registrations + approved customers)
    GET|PATCH /.../universe-customers/<kind>/<id>/                    customer popup data / edit      (kind = registration | customer)
    POST /.../universe-customers/<kind>/<id>/<action>/                approve, hold, reject, retry-tally, extend-trial, renew, set-license, ...

Every endpoint is project-scoped (ProductAccess: Superadmin, or an Admin with the explicit grant for THAT project) and read-only for aggregation."""
import json
import re
import uuid
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.accounts.constants import Role
from apps.accounts.permissions import get_role, user_can

from . import pull, services as s, tally, universe, usage, usage_reports
from .admin_views import AdminView
from .models import (IntegrationActivationKey, IntegrationAudit, IntegrationCustomer, IntegrationLicense, IntegrationProject, IntegrationRegistration,
                     IntegrationRenewal, IntegrationSession, IntegrationTallyInfo, IntegrationUsageEvent)
from .product_views import ProductView
from .services import IntegrationError
from .project_access import project_actions

Reg = IntegrationRegistration
IST = timezone.get_fixed_timezone(330)
STATUS_LABEL = {"pending": "Pending Approval", "not_activated": "Pending Activation", "active": "Active", "expired": "Expired", "cancelled": "Cancelled", "inactive": "Suspended"}
REG_LABEL = {Reg.PENDING_REVIEW: "Pending Review", Reg.PENDING_TALLY: "Pending Tally Verification", Reg.HELD: "On Hold", Reg.REJECTED: "Rejected"}
TYPE_LABEL = {"TRIAL": "Trial", "ONE_YEAR": "Yearly", "LIFETIME": "Lifetime", "CUSTOM": "Custom"}
CATEGORIES = ("speed", "normal", "new", "one_time", "not_used")


def universe_projects(user=None):
    """UNIVERSE projects (calendar-year / review lifecycle) visible to `user`: Superadmin all; Admin only with a grant on that project."""
    out = []
    for p in IntegrationProject.objects.filter(license_policy="CALENDAR").order_by("name"):
        if user is not None and get_role(user) != Role.SUPERADMIN:
            if not (user_can(user, p.code, "view") or user_can(user, f"{p.code}:dashboard", "view") or any(project_actions(user, p).values())):
                continue
        out.append(p)
    return out


def _can(user, code, module_suffix, action):
    return get_role(user) == Role.SUPERADMIN or user_can(user, f"{code}{module_suffix}", action)


def _remaining(expires_at, now=None):
    if not expires_at:
        return None
    now = now or timezone.now()
    return (expires_at.astimezone(IST).date() - now.astimezone(IST).date()).days


def telemetry(project, rows=None):
    """Is activity telemetry present? Classification counts that need it are reported as unavailable (null), never as a misleading 0."""
    if not project.usage_tracking:
        return {"available": False, "reason": "Usage tracking is switched off for this project."}
    has_events = IntegrationUsageEvent.objects.filter(project=project, event_type__in=s.ACTIVITY_EVENTS).exists()
    if not has_events and IntegrationCustomer.objects.filter(project=project).exists():
        return {"available": False, "reason": "No activity events have been received from this project yet. The project backend must send usage events (app open, login, heartbeat)."}
    return {"available": True, "reason": ""}


def legacy_project_rows(user):
    rows = []
    for project in IntegrationProject.objects.exclude(license_policy="CALENDAR"):
        dashboard = _can(user, project.code, ":dashboard", "view")
        customers = _can(user, project.code, "", "view")
        if dashboard or customers:
            rows.append({"code": project.code, "name": project.name, "can": {"dashboard": dashboard, "customers": customers}})
    return rows


# ------------------------------------------------------------------ registry for the sidebar / overall dashboard
class UniverseProjectsView(AdminView):
    permission_classes = []

    def check_permissions(self, request):
        if get_role(request.user) not in (Role.SUPERADMIN, Role.ADMIN):
            self.permission_denied(request)

    def get(self, request):
        rows = []
        for p in universe_projects(request.user):
            rows.append({"code": p.code, "name": p.name, "status": p.status, "enabled": p.enabled, "tally_required": p.tally_serial_required,
                         "can": {"dashboard": _can(request.user, p.code, ":dashboard", "view"), "customers": _can(request.user, p.code, "", "view"),
                                 "approve": _can(request.user, p.code, "", "approve"), "edit": _can(request.user, p.code, "", "edit"),
                                 "integration": project_actions(request.user, p)["configure"]}})
        return Response({"projects": rows, "legacy": legacy_project_rows(request.user)})


class UniverseCardsView(UniverseProjectsView):
    """Overall Dashboard: one Used / Not Used / Expire card per APPROVED (integration ACTIVE) and enabled UNIVERSE project, plus the licence rows the six
    existing tiles aggregate (same identity rule as the legacy products: one person = one e-mail address). Read-only."""

    def get(self, request):
        now = timezone.now()
        cards, rows = [], []
        for p in universe_projects(request.user):
            if p.status != IntegrationProject.ACTIVE or not p.enabled:
                continue
            if get_role(request.user) != Role.SUPERADMIN and not _can(request.user, p.code, ":dashboard", "view"):
                continue
            tele = telemetry(p)
            stats = usage.project_stats(p, now)
            card = {"code": p.code, "label": p.name, "kind": "universe", "available": p.usage_tracking, "used": stats["used"], "not_used": stats["not_used"], "expired": stats["expired"]}
            if not p.usage_tracking:
                card.update(available=False, message=f"{p.name}: usage tracking is switched off.")
            elif not tele["available"]:
                card.update(complete=False, note=tele["reason"])
            cards.append(card)
            for r in usage.customer_rows(p, now):
                rows.append({"project": p.code, "email": r["email"], "state": "active" if r["status"] in ("active", "not_activated") else r["status"] if r["status"] in ("expired",) else "other",
                             "trial": r["license_type"] == "TRIAL", "expiry": r["expires_at"] if r["license_type"] != "LIFETIME" else None})
            for reg in Reg.objects.filter(project=p, status__in=[Reg.PENDING_REVIEW, Reg.PENDING_TALLY, Reg.HELD]):
                rows.append({"project": p.code, "email": reg.email, "state": "pending", "trial": False, "expiry": None})
        return Response({"cards": cards, "rows": rows})


# ------------------------------------------------------------------ project dashboard (GSTR-style)
class UniverseDashboardView(ProductView):
    def required(self, request, code):
        return [(f"{code}:dashboard", "view")]

    def get(self, request, code):
        p = self.project(code)
        if p.license_policy != "CALENDAR":
            raise NotFound("This is not a UNIVERSE project.")
        now = timezone.now()
        rows = usage.customer_rows(p, now)
        tele = telemetry(p)
        open_regs = Reg.objects.filter(project=p, status__in=[Reg.PENDING_REVIEW, Reg.PENDING_TALLY, Reg.HELD])
        week = now - timedelta(days=7)

        def count(cat):
            if cat == "new":  # needs only the registration date: always reliable
                return sum(1 for r in rows if r["usage_category"] == "new")
            return sum(1 for r in rows if r["usage_category"] == cat) if tele["available"] else None

        cards = [{"key": k, "label": label, "count": count(k), "available": k == "new" or tele["available"]}
                 for k, label in (("speed", "Speed User"), ("normal", "Normal User"), ("new", "New User"), ("one_time", "One-Time User"))]
        transfers = IntegrationUsageEvent.objects.filter(project=p, event_type="DEVICE_TRANSFER", occurred_at__gte=week)
        total = len(rows) + open_regs.count()
        last7 = sum(1 for r in rows if r["created_at"] >= week) + open_regs.filter(created_at__gte=week).count()
        return Response({
            "project": {"code": p.code, "name": p.name, "status": p.status, "tally_required": p.tally_serial_required},
            "cards": cards,
            "stats": {"total_customers": total, "last_7_days": last7, "pending_activation": open_regs.count(), "recent_device_transfers": transfers.count(),
                      "used": sum(r["used"] for r in rows) if tele["available"] else None,
                      "not_used": sum(not r["used"] for r in rows) if tele["available"] else None,
                      "expired": sum(r["status"] == "expired" for r in rows),
                      "last_used": max((r["last_opened"] for r in rows if r["last_opened"]), default=None)},
            "telemetry": tele,
            "recent_transfers": usage_reports.device_transfer_history(p, {"from": (now - timedelta(days=7)).astimezone(IST).date().isoformat()})["results"][:10],
            "rules": {**p.rules, "note": "New = registered within new_days. Speed = active on >= speed_min_days different days in the last 30 days. One-Time = active on exactly one day, ever. "
                                           "Normal = every other customer with activity. Not Used = no activity. A customer is in at most one class (New wins)."},
            "definitions": {"total_customers": "Approved customers + registrations awaiting a decision (rejected ones excluded).",
                            "last_7_days": "Customers / registrations received in the last 7 days.",
                            "pending_activation": "Registrations awaiting Review, Tally Verification or a decision (no licence yet).",
                            "recent_device_transfers": "Confirmed device transfers in the last 7 days."},
        })


# ------------------------------------------------------------------ customers list
def _reg_row(reg):
    return {"kind": "registration", "id": reg.pk, "name": reg.name, "company_name": reg.company_name, "email": reg.email, "contact_number": reg.contact_number,
            "project": reg.project.name, "user_id": json.loads(reg.tally_payload or "{}").get("serial") if reg.project.tally_serial_required and reg.status == Reg.PENDING_REVIEW else "",
            "license_type": "", "expires_at": None, "status": REG_LABEL.get(reg.status, reg.status), "status_key": reg.status, "usage_category": None,
            "registered_at": reg.created_at}


class UniverseCustomerListView(ProductView):
    def required(self, request, code):
        return [(code, "view")]

    def get(self, request, code):
        p = self.project(code)
        q = request.query_params
        now = timezone.now()
        companies = dict(IntegrationCustomer.objects.filter(project=p).values_list("pk", "company_name"))
        rows = [_reg_row(r) for r in Reg.objects.select_related("project").filter(project=p).exclude(status=Reg.APPROVED)]
        for r in usage.customer_rows(p, now):
            rows.append({"kind": "customer", "id": r["id"], "name": r["name"], "company_name": companies.get(r["id"], ""), "email": r["email"], "contact_number": r["contact_number"],
                         "project": p.name, "user_id": r["user_id"], "license_type": TYPE_LABEL.get(r["license_type"], r["license_type"]), "expires_at": r["expires_at"],
                         "status": STATUS_LABEL.get(r["status"], r["status"]), "status_key": r["status"], "usage_category": r["usage_category"] or ("not_used" if not r["used"] else None),
                         "used": bool(r["used"]), "registered_at": r["created_at"]})
        needle = (q.get("search") or "").strip().lower()
        if needle:
            rows = [r for r in rows if needle in " ".join(str(r.get(k) or "") for k in ("name", "company_name", "email", "contact_number", "user_id")).lower()]
        if q.get("status") and q["status"] != "all":
            rows = [r for r in rows if r["status"] == q["status"]]
        if q.get("license_type") and q["license_type"] != "all":
            rows = [r for r in rows if r["license_type"] == q["license_type"]]
        if q.get("usage") and q["usage"] != "all":
            if q["usage"] == "used":  # any recorded activity (the Overall Dashboard card's "Used")
                rows = [r for r in rows if r.get("used")]
            elif q["usage"] == "not_used":
                rows = [r for r in rows if r["kind"] == "customer" and not r.get("used")]
            else:
                rows = [r for r in rows if r["usage_category"] == q["usage"]]
        rows.sort(key=lambda r: r["registered_at"], reverse=True)
        try:
            page, size = max(int(q.get("page", 1)), 1), min(max(int(q.get("page_size", 25)), 1), 100)
        except ValueError:
            page, size = 1, 25
        return Response({"total": len(rows), "page": page, "page_size": size, "results": rows[(page - 1) * size: page * size],
                         "statuses": sorted({r["status"] for r in rows}), "project": {"code": p.code, "name": p.name, "tally_required": p.tally_serial_required, "manual": p.registration_method == "MANUAL"}})


def _manual_record(d):
    """Validated customer record for a manual add (Name, Contact, Email required; Company, GSTIN, Address optional)."""
    gstin = str(d.get("gstin") or "").strip().upper()
    if gstin and not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]", gstin):
        raise ValidationError({"gstin": ["Enter a valid 15-character GSTIN, or leave it blank."]})
    record = {"external_customer_id": f"MAN-{uuid.uuid4().hex[:12].upper()}", "customer_name": d.get("name"), "email": d.get("email"),
              "contact_number": d.get("contact_number"), "company_name": d.get("company_name"), "address": d.get("address"), "gstin": gstin}
    s._clean_customer(record)  # raises a field-level 422 before anything is written
    return record


def _password(d):
    pw = str(d.get("temporary_password") or "")
    if pw and not 8 <= len(pw) <= 128:
        raise ValidationError({"temporary_password": ["The temporary password must be 8-128 characters."]})
    return pw or None


def _manual_save(project, d, actor, app_url):
    record = _manual_record(d)
    reg, _ = universe.ingest(project, record, actor="manual")
    return universe.approve(reg.pk, d.get("license_type"), d.get("trial_days"), d.get("years"), actor=actor, app_url=app_url, password=_password(d))


class UniverseManualCustomerView(ProductView):
    """POST: add a customer by hand to a MANUAL (Non-Tally) project: validated, registered and approved in one step (no external API involved).
    The User ID / serial is allocated by the server; the temporary password is returned once and stored only as a hash."""

    def required(self, request, code):
        return [(code, "approve")]

    def post(self, request, code):
        p = self.project(code)
        if p.registration_method != "MANUAL":
            raise ValidationError({"project": ["This project is API integrated; customers are synchronised, not added by hand."]})
        with transaction.atomic():
            out = _manual_save(p, request.data, self.actor(), request.build_absolute_uri("/"))
        return Response(out, status=201)


class UniverseManualProjectView(AdminView):
    """POST: Non-Tally -> Add Project. Creates the MANUAL project and its first customer in ONE atomic save: if the customer is invalid, no project exists."""

    def post(self, request):
        d = request.data
        _manual_record(d)  # validate the customer first
        clean, errors = s.validate_project_data({"name": d.get("project_name"), "tally_serial_required": False, "developer_email": d.get("developer_email", "")})
        if errors:
            return Response({"message": "Please correct the highlighted fields.", "errors": {("project_name" if k == "name" else k): v for k, v in errors.items()}}, status=400)
        universe.term(IntegrationProject(trial_days=3, validity_years=1), d.get("license_type"), d.get("trial_days"), d.get("years"))  # licence term validated BEFORE anything is created
        project = None
        try:
            with transaction.atomic():
                project, _cred, _secret = s.create_project(clean, self.actor())
                out = _manual_save(project, d, self.actor(), request.build_absolute_uri("/"))
        except Exception:
            if project is not None and not IntegrationCustomer.objects.filter(project=project).exists():  # belt and braces if the DB did not roll back
                IntegrationProject.objects.filter(pk=project.pk).delete()
            raise
        out["project"] = {"code": project.code, "name": project.name}
        return Response(out, status=201)


# ------------------------------------------------------------------ customer popup
def _tally_block(project, reg, customer):
    if not project.tally_serial_required:
        return None
    if customer is not None and hasattr(customer, "tally_info"):
        t = customer.tally_info
        return {"verified": True, "serial": t.tally_serial, "edition": t.edition, "tss_active": t.tss_active, "tss_expiry": t.tss_expiry, "license_admin": t.license_admin,
                "virtual_user_limit": t.virtual_user_limit, "gateway_server": t.gateway_server, "verified_at": t.verified_at, "missing": []}
    payload = json.loads(reg.tally_payload or "{}") if reg else {}
    c = {"external_customer_id": reg.external_customer_id if reg else "", "customer_name": "", "email": reg.email if reg else "", "contact_number": ""}
    clean, problems = tally.validate_tally(payload, c)
    missing = [label for key, label in (("serial", "Tally Serial Number"), ("edition", "Tally Edition"), ("tss_active", "Tally Software Services (TSS)"), ("license_admin", "License Administrator"))
               if not payload.get(key)]
    verified = not problems and not (reg and reg.tally_error)
    return {"verified": verified, "serial": payload.get("serial", ""), "edition": payload.get("edition", ""), "tss_active": payload.get("tss_active"), "tss_expiry": payload.get("tss_expiry"),
            "license_admin": payload.get("license_admin", ""), "virtual_user_limit": payload.get("virtual_user_limit"), "gateway_server": payload.get("gateway_server", ""),
            "verified_at": None, "missing": missing, "problems": problems + ([reg.tally_error] if reg and reg.tally_error and reg.tally_error not in problems else []),
            "title": tally.TITLE, "message": tally.MESSAGE}


def customer_detail(p, customer, user):
    now = timezone.now()
    lic = customer.license
    reg = getattr(customer, "registration", None)
    row = next((r for r in usage.customer_rows(p, now) if r["id"] == customer.pk), {})
    key = IntegrationActivationKey.objects.filter(license=lic, internal=True).exists()
    sessions = list(IntegrationSession.objects.filter(license=lic).order_by("-id")[:50])
    active = next((x for x in sessions if x.status == IntegrationSession.ACTIVE), None)
    events = IntegrationUsageEvent.objects.filter(license=lic)
    rep = usage_reports.usage_report(p, {"customer": str(customer.pk)})
    cust = rep["customers"][0] if rep["customers"] else {}
    today = now.astimezone(IST).date().isoformat()
    month = today[:7]
    daily = [d for d in rep["daily"] if d["customer_id"] == customer.pk]
    renewals = list(IntegrationRenewal.objects.filter(license=lic).order_by("-id")[:30])
    last_term = renewals[0] if renewals else None
    audit = IntegrationAudit.objects.filter(project=p, subject__in=[customer.user_id, customer.external_customer_id]).order_by("-id")[:60]
    transfers = events.filter(event_type="DEVICE_TRANSFER").order_by("-occurred_at")[:20]
    years = next((r.years for r in renewals if r.years), None)
    return {
        "kind": "customer", "id": customer.pk, "project": {"code": p.code, "name": p.name, "tally_required": p.tally_serial_required},
        "registration": {"name": customer.name, "company_name": customer.company_name, "email": customer.email, "contact_number": customer.contact_number, "address": customer.address, "gstin": customer.gstin,
                         "external_customer_id": customer.external_customer_id, "project": p.name, "registered_at": reg.created_at if reg else customer.created_at,
                         "source": reg.source if reg else "PUSH", "approval_status": "Approved", "approved_by": reg.decided_by if reg else "", "approved_at": reg.decided_at if reg else None},
        "tally": _tally_block(p, reg, customer),
        "account": {"user_id": customer.user_id, "serial_number": customer.serial_number, "password_status": "Setup Required (temporary password)" if customer.must_change_password else "Active",
                    "activation_key_status": "Generated (internal, hashed)" if key else "Pending", "account_status": "Active" if customer.active else "Suspended",
                    "locked": bool(customer.locked_until and customer.locked_until > now)},
        "license": {"license_type": lic.license_type, "type_label": TYPE_LABEL.get(lic.license_type), "trial_days": s.trial_days(lic), "years": years if lic.license_type == "ONE_YEAR" else None,
                    "starts_at": lic.starts_at, "expires_at": lic.expires_at, "remaining_days": _remaining(lic.expires_at, now), "state": s.license_state(lic, now),
                    "status": STATUS_LABEL.get(row.get("status"), row.get("status")), "renewal_required": s.license_state(lic, now) == "EXPIRED",
                    "last_renewal_at": next((r.created_at for r in renewals if r.action == "RENEWED"), None), "last_term_change_at": last_term.created_at if last_term else None},
        "usage": {"last_login": row.get("last_login"), "last_used": cust.get("last_used") or row.get("last_opened"), "total_logins": row.get("login_count", 0), "total_opens": row.get("opens", 0),
                  "active_days": row.get("active_days", 0), "category": row.get("usage_category") or ("not_used" if not row.get("used") else None),
                  "daily_minutes": next((d["active_minutes"] for d in daily if d["date"] == today), None) if cust.get("duration_available") else None,
                  "monthly_minutes": round(sum(d["active_minutes"] or 0 for d in daily if d["date"].startswith(month)), 1) if cust.get("duration_available") else None,
                  "duration_available": bool(cust.get("duration_available")), "note": "" if cust.get("duration_available") else "Duration is shown only when the project sends heartbeat events."},
        "devices": {"current": (active.device_name or active.device_id) if active else None, "active_session": bool(active), "last_seen_at": active.last_seen_at if active else None,
                    "last_transfer_at": transfers[0].occurred_at if transfers else None,
                    "history": [{"device": x.device_name or x.device_id, "status": x.status, "authorized_at": x.created_at, "ended_at": x.revoked_at, "reason": x.revoked_reason} for x in sessions]},
        "history": {"terms": [{"at": r.created_at, "action": r.action, "type": TYPE_LABEL.get(r.license_type), "years": r.years, "from": r.previous_expires_at, "to": r.new_expires_at, "by": r.actor} for r in renewals],
                    "audit": [{"at": a.created_at, "action": a.action, "detail": a.detail, "by": a.actor, "ok": a.ok} for a in audit]},
        "can": _caps(user, p.code),
    }


def _caps(user, code):
    f = lambda a: get_role(user) == Role.SUPERADMIN or user_can(user, code, a)  # noqa: E731
    return {"edit": f("edit"), "approve": f("approve"), "extend_trial": f("extend_trial"), "renew": f("renew"), "inactivate": f("inactivate"), "reset_password": f("edit"), "set_license": f("edit"), "delete": f("delete")}


def registration_detail(p, reg, user):
    now = timezone.now()
    row = universe.row(reg, now)
    return {
        "kind": "registration", "id": reg.pk, "project": {"code": p.code, "name": p.name, "tally_required": p.tally_serial_required},
        "registration": {"name": reg.name, "company_name": reg.company_name, "email": reg.email, "contact_number": reg.contact_number, "address": reg.address, "gstin": reg.gstin,
                         "external_customer_id": reg.external_customer_id, "project": p.name, "registered_at": reg.created_at, "source": reg.source,
                         "approval_status": REG_LABEL.get(reg.status, reg.status.title()), "approval_key": reg.status, "decided_by": reg.decided_by, "decided_at": reg.decided_at},
        "tally": _tally_block(p, reg, None),
        "account": {"user_id": row["user_id"], "user_id_rule": "Unique 9-digit serial starting with 9 (Nexus User ID" + ("; the verified Tally Serial Number is kept separately)" if p.tally_serial_required else ")"),
                    "suggested_user_id": universe.new_user_id() if reg.status != "APPROVED" else None,  # a suggestion only: uniqueness is re-checked at Save & Allow
                    "password_status": "Generated on approval unless set here (3-128 characters, any characters; must be changed at first login); sent once in the account email, stored only as a hash",
                    "activation_key_status": "Generated on approval (internal 9-character reference, hashed, never shown or emailed)"},
        "license_defaults": {"trial_days": p.trial_days, "years": p.validity_years},
        "preview": row["preview"],
        "can": _caps(user, p.code),
    }


class UniverseCustomerView(ProductView):
    """GET popup data / PATCH edit. `kind` is registration (not yet approved) or customer (approved)."""

    def required(self, request, code):
        return [(code, {"GET": "view", "DELETE": "delete"}.get(request.method, "edit"))]

    def _target(self, code, kind, pk):
        p = self.project(code)
        if kind == "registration":
            reg = Reg.objects.select_related("project", "customer").filter(project=p, pk=pk).first()  # scoped by the URL project: no cross-project access
            if not reg:
                raise NotFound("Unknown registration.")
            return p, reg, None
        if kind == "customer":
            c = IntegrationCustomer.objects.select_related("license", "project").filter(project=p, pk=pk).first()
            if not c:
                raise NotFound("Unknown customer.")
            return p, None, c
        raise NotFound("Unknown record type.")

    def get(self, request, code, kind, pk):
        p, reg, c = self._target(code, kind, pk)
        q = request.query_params
        data = customer_detail(p, c, request.user) if c else registration_detail(p, reg, request.user)
        if reg and q.get("license_type"):
            t = universe.term(p, q["license_type"], q.get("trial_days"), q.get("years"))
            data["preview"] = {"license_type": t["license_type"], "starts_at": t["starts_at"], "expires_at": t["expires_at"]}
        return Response({"record": data})

    def patch(self, request, code, kind, pk):
        p, reg, c = self._target(code, kind, pk)
        body = request.data
        who = self.actor()
        if reg:
            if reg.status in (Reg.APPROVED,):
                raise IntegrationError("INVALID_STATE", "Edit the approved customer instead.", 409)
            clean = s._clean_customer({"external_customer_id": reg.external_customer_id, **{k: body[k] for k in ("customer_name", "email", "contact_number") if k in body}}, require_all=False)
            if "email" in clean and (IntegrationCustomer.objects.filter(project=p, email=clean["email"]).exists() or Reg.objects.filter(project=p, email=clean["email"]).exclude(pk=reg.pk).exists()):
                raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this project already uses this email address.", 409)
            for src, dst in (("customer_name", "name"), ("email", "email"), ("contact_number", "contact_number")):
                if src in clean:
                    setattr(reg, dst, clean[src])
            for f, n in (("company_name", 150), ("address", 300), ("gstin", 15)):
                if f in body:
                    setattr(reg, f, str(body[f] or "").strip()[:n])
            reg.save()
            s.audit(p, "registration.edited", reg.external_customer_id, actor=who)
            return Response({"record": registration_detail(p, reg, request.user)})
        if "email" in body and Reg.objects.filter(project=p, email=str(body["email"]).strip().lower()).exclude(customer=c).exists():
            raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this project already uses this email address.", 409)
        s.edit_customer(c, body, actor=who)
        for f, n in (("company_name", 150), ("address", 300), ("gstin", 15)):
            if f in body:
                setattr(c, f, str(body[f] or "").strip()[:n])
        c.save()
        s.audit(p, "customer.edited", c.user_id, actor=who)
        c.refresh_from_db()
        return Response({"record": customer_detail(p, c, request.user)})


def _delete_customer(self, request, code, kind, pk):
    """Project-scoped customer delete. Only an approved customer with NO sessions or usage history; anything else is protected (409): inactivate instead."""
    p = self.project(code)
    if kind != "customer":
        raise ValidationError({"kind": ["Only an approved customer can be deleted."]})
    c = IntegrationCustomer.objects.select_related("license").filter(project=p, pk=pk).first()  # scoped by the URL project
    if not c:
        raise NotFound("Unknown customer.")
    lic = getattr(c, "license", None)
    if lic and (IntegrationSession.objects.filter(license=lic).exists() or IntegrationUsageEvent.objects.filter(license=lic).exists()):
        raise IntegrationError("CUSTOMER_HAS_HISTORY", "This customer has device sessions or usage history, so it cannot be deleted. Inactivate the customer instead; nothing is lost.", 409)
    with transaction.atomic():
        s.audit(p, "customer.deleted", c.user_id, actor=self.actor())
        Reg.objects.filter(project=p, customer=c).delete()
        c.delete()
    return Response(status=204)


UniverseCustomerView.delete = _delete_customer


class UniverseCustomerActionView(UniverseCustomerView):
    NEEDS = {"approve": "approve", "hold": "approve", "reject": "approve", "retry-tally": "approve", "extend-trial": "extend_trial", "renew": "renew", "set-license": "edit",
             "inactivate": "inactivate", "reactivate": "inactivate", "reset-password": "edit", "resend-setup": "edit"}
    REG_ACTIONS = ("approve", "hold", "reject", "retry-tally")

    def required(self, request, code):
        need = self.NEEDS.get(self.kwargs.get("action"))
        return [(code, need)] if need else None

    def post(self, request, code, kind, pk, action):
        p, reg, c = self._target(code, kind, pk)
        who, body = self.actor(), request.data
        if (action in self.REG_ACTIONS) != (kind == "registration"):
            raise ValidationError({"action": [f"'{action}' does not apply to a {kind}."]})
        if kind == "registration":
            out = None
            if action == "approve":
                out = universe.approve(reg.pk, body.get("license_type"), body.get("trial_days"), body.get("years"), actor=who, app_url=request.headers.get("Origin", ""),
                                       password=body.get("password") if "password" in body else None, user_id=body.get("user_id") or None, custom_days=body.get("custom_days"))
                c = IntegrationCustomer.objects.select_related("license", "project").get(pk=out["customer_id"])
                return Response({"result": out, "record": customer_detail(p, c, request.user)})
            if action == "hold":
                universe.hold(reg.pk, who, str(body.get("note") or ""))
            elif action == "reject":
                universe.reject(reg.pk, who, str(body.get("note") or ""))
            else:
                universe.retry_tally(reg, pull.fetch_tally, who)
            reg.refresh_from_db()
            return Response({"record": registration_detail(p, reg, request.user)})
        lic, result = c.license, None
        if action == "extend-trial":
            s.extend_trial(lic, body.get("days"), who)
        elif action == "renew":
            s.renew_license(lic, who)
        elif action == "set-license":
            s.set_license_type(lic, body.get("license_type"), body.get("trial_days"), who, years=body.get("years"))
        elif action == "inactivate":
            s.set_customer_active(c, False, who)
        elif action == "reactivate":
            s.set_customer_active(c, True, who)
        elif action == "reset-password":
            result = universe.reset_password(c, who)
        elif action == "resend-setup":
            result = {"setup_email": universe.send_setup_email(c, request.headers.get("Origin", ""), who)}
        else:
            raise NotFound("Unknown action.")
        c = IntegrationCustomer.objects.select_related("license", "project").get(pk=c.pk)
        return Response({"record": customer_detail(p, c, request.user), "result": result})


# ------------------------------------------------------------------ Project Hub (server-side search / filter / pagination)
class UniverseHubView(UniverseProjectsView):
    """Projects list for UNIVERSE -> Projects. Filtered by the caller's authorisation FIRST, then search / type / status, then paginated in the database."""

    def get(self, request):
        from django.db.models import Count, Q
        q = request.query_params
        qs = IntegrationProject.objects.filter(license_policy="CALENDAR")
        if get_role(request.user) != Role.SUPERADMIN:
            qs = qs.filter(pk__in=[p.pk for p in universe_projects(request.user)])
        term = (q.get("search") or "").strip()
        if term:
            qs = qs.filter(Q(name__icontains=term) | Q(code__icontains=term))
        if q.get("type") == "tally":
            qs = qs.exclude(registration_method="MANUAL")  # every API-integrated project (Tally Required Yes or No) is a Tally SL project
        elif q.get("type") == "non-tally":
            qs = qs.filter(registration_method="MANUAL")  # only manual-customer projects are Non-Tally SL
        summary_qs = qs  # counts for the summary cards: this category, ignoring the status filter and paging
        status = q.get("status")
        if status == "archived":
            qs = qs.filter(status=IntegrationProject.ARCHIVED) if hasattr(IntegrationProject, "ARCHIVED") else qs.filter(status="ARCHIVED")
        elif status == "active":
            qs = qs.filter(status=IntegrationProject.ACTIVE, enabled=True)
        elif status == "pending":
            qs = qs.filter(status=IntegrationProject.SETUP_PENDING, enabled=True)
        elif status == "suspended":
            qs = qs.filter(Q(enabled=False) | Q(status=IntegrationProject.DISABLED))
        try:
            page, size = max(int(q.get("page", 1)), 1), min(max(int(q.get("page_size", 20)), 1), 100)
        except ValueError:
            page, size = 1, 20
        total = qs.count()
        rows = (qs.annotate(n_customers=Count("customers", distinct=True),
                            n_open=Count("registrations", filter=Q(registrations__status__in=[Reg.PENDING_REVIEW, Reg.PENDING_TALLY, Reg.HELD]), distinct=True))
                .select_related("sync_checkpoint").order_by("name", "code")[(page - 1) * size: page * size])
        is_super = get_role(request.user) == Role.SUPERADMIN
        out = []
        for p in rows:
            cp = getattr(p, "sync_checkpoint", None)
            out.append({"code": p.code, "name": p.name, "type": "Non-Tally" if p.registration_method == "MANUAL" else "Tally", "tally_required": p.tally_serial_required, "manual": p.registration_method == "MANUAL",
                        "status": "Archived" if p.status == "ARCHIVED" else "Suspended" if (not p.enabled or p.status == IntegrationProject.DISABLED) else "Active" if p.status == IntegrationProject.ACTIVE else "Pending",
                        "api_status": {"integration": p.status, "sync": cp.status if cp else "NEVER", "last_error": cp.last_error if cp else ""},
                        "customer_count": p.n_customers + p.n_open, "last_sync_at": cp.last_success_at if cp else None,
                        "can": {"dashboard": _can(request.user, p.code, ":dashboard", "view"), "customers": _can(request.user, p.code, "", "view"), **project_actions(request.user, p)}})
        legacy = legacy_project_rows(request.user) if q.get("type") == "tally" and page == 1 else []
        summary = {"total": summary_qs.count(),
                   "active": summary_qs.filter(status=IntegrationProject.ACTIVE, enabled=True).count(),
                   "pending": summary_qs.filter(status=IntegrationProject.SETUP_PENDING).count(),
                   "archived": summary_qs.filter(status="ARCHIVED").count()}
        return Response({"total": total, "page": page, "page_size": size, "results": out, "legacy": legacy, "can_add": is_super, "summary": summary})


class UniverseProjectDetailView(UniverseProjectsView):
    """One project for the workspace header: 404 when it does not exist (or is not UNIVERSE), 403 when this staff member may not see it."""

    def get(self, request, code):
        p = IntegrationProject.objects.filter(code=code.upper(), license_policy="CALENDAR").first()
        if not p:
            raise NotFound("Project not found.")
        if p.pk not in [x.pk for x in universe_projects(request.user)]:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have access to this project.")
        return Response({"project": {"code": p.code, "name": p.name, "tally_required": p.tally_serial_required, "status": p.status, "enabled": p.enabled},
                         "can": {"dashboard": _can(request.user, p.code, ":dashboard", "view"), "customers": _can(request.user, p.code, "", "view"),
                                 **project_actions(request.user, p)}})
