"""Dashboard numbers and customer rows for connected projects, computed from real customer / licence / session / usage records.

Usage classes are explicit, ordered and therefore NON-OVERLAPPING; the first matching rule wins:
  1. NEW       licence created within `new_days` days
  2. (unused)  no activity event ever  -> counted as "Not Used", belongs to no class
  3. ONE_TIME  activity on exactly one calendar day, ever
  4. SPEED     activity on >= `speed_min_days` different days in the last 30 days
  5. NORMAL    every other used customer
Activity = APP_OPEN, LOGIN, HEARTBEAT or DEVICE_ACTIVATION (LOGOUT / DEVICE_TRANSFER are not usage).

Licence status shown to staff: pending | not_activated | active | expired | cancelled | inactive
(not_activated = licence is valid but no device was ever activated).
"""
from datetime import timedelta

from django.db.models import Count, Max, Min
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import IntegrationCustomer, IntegrationLicense, IntegrationProject, IntegrationSession, IntegrationUsageEvent
from .services import ACTIVITY_EVENTS, license_state, trial_days

STATUS_OF = {"PENDING_APPROVAL": "pending", "INACTIVE": "inactive", "CANCELLED": "cancelled", "EXPIRED": "expired", "SUSPENDED": "inactive"}
CATEGORY_FILTERS = ("used", "not_used", "expired", "expiring", "trial", "annual", "lifetime", "active_devices", "speed", "normal", "new", "one_time", "pending")


def classify(age_days, days_all, days_30, rules):
    if age_days <= rules["new_days"]:
        return "new"
    if days_all == 0:
        return None
    if days_all == 1:
        return "one_time"
    if days_30 >= rules["speed_min_days"]:
        return "speed"
    return "normal"


def _month_end(now):
    return (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)).replace(day=1)


def customer_rows(project, now=None):
    """One dict per customer with every derived field; the dashboard counts and the customer list both come from this."""
    now = now or timezone.now()
    lics = list(IntegrationLicense.objects.filter(customer__project=project).select_related("customer"))
    base = IntegrationUsageEvent.objects.filter(project=project, event_type__in=ACTIVITY_EVENTS)
    days_all = dict(base.values("license_id").annotate(d=Count(TruncDate("occurred_at"), distinct=True)).values_list("license_id", "d"))
    days_30 = dict(base.filter(occurred_at__gte=now - timedelta(days=30)).values("license_id").annotate(d=Count(TruncDate("occurred_at"), distinct=True)).values_list("license_id", "d"))
    last_login = dict(IntegrationUsageEvent.objects.filter(project=project, event_type="LOGIN").values("license_id").annotate(m=Max("occurred_at")).values_list("license_id", "m"))
    first_active = dict(IntegrationSession.objects.filter(license__customer__project=project).values("license_id").annotate(m=Min("created_at")).values_list("license_id", "m"))
    opens = dict(IntegrationUsageEvent.objects.filter(project=project, event_type="APP_OPEN").values("license_id").annotate(n=Count("id")).values_list("license_id", "n"))
    last_used = dict(base.values("license_id").annotate(m=Max("occurred_at")).values_list("license_id", "m"))
    logins = dict(IntegrationUsageEvent.objects.filter(project=project, event_type="LOGIN").values("license_id").annotate(n=Count("id")).values_list("license_id", "n"))
    active_device = {}
    for sess in IntegrationSession.objects.filter(license__customer__project=project, status="ACTIVE").order_by("id"):
        active_device[sess.license_id] = sess.device_name or sess.device_id
    devices = dict(IntegrationSession.objects.filter(license__customer__project=project, status="ACTIVE").values("license_id").annotate(n=Count("id")).values_list("license_id", "n"))
    rules, month_end, rows = project.rules, _month_end(now), []
    for lic in lics:
        c, state = lic.customer, license_state(lic, now)
        status = STATUS_OF.get(state) or ("active" if lic.pk in first_active else "not_activated")
        used = days_all.get(lic.pk, 0) > 0
        rows.append({
            "id": c.pk, "license_id": lic.pk, "external_customer_id": c.external_customer_id, "name": c.name, "email": c.email,
            "contact_number": c.contact_number, "serial_number": c.serial_number, "user_id": c.user_id, "license_type": lic.license_type,
            "status": status, "state": state, "activated_at": first_active.get(lic.pk), "expires_at": lic.expires_at, "starts_at": lic.starts_at, "trial_days": trial_days(lic),
            "last_login": last_login.get(lic.pk), "used": used, "opens": opens.get(lic.pk, 0), "login_count": logins.get(lic.pk, 0), "active_device": active_device.get(lic.pk, ""), "last_opened": last_used.get(lic.pk), "active_days": days_all.get(lic.pk, 0), "active_devices": devices.get(lic.pk, 0),
            "usage_category": classify((now - lic.created_at).days, days_all.get(lic.pk, 0), days_30.get(lic.pk, 0), rules),
            "expiring_this_month": state == "ACTIVE" and bool(lic.expires_at) and lic.expires_at < month_end,
            "welcome_email_status": c.welcome_email_status, "created_at": c.created_at,
        })
    return rows


def matches(row, key):
    """The Dashboard count -> Customers filter: the same rule counts a customer and lists it."""
    return {
        "used": row["used"], "not_used": not row["used"], "expired": row["status"] == "expired", "expiring": row["expiring_this_month"],
        "trial": row["license_type"] == "TRIAL", "annual": row["license_type"] == "ONE_YEAR", "lifetime": row["license_type"] == "LIFETIME",
        "active_devices": row["active_devices"] > 0, "pending": row["status"] == "pending",
        "speed": row["usage_category"] == "speed", "normal": row["usage_category"] == "normal", "new": row["usage_category"] == "new",
        "one_time": row["usage_category"] == "one_time",
    }[key]


def project_stats(project, now=None):
    now = now or timezone.now()
    rows = customer_rows(project, now)
    n = lambda key: sum(1 for r in rows if matches(r, key))  # noqa: E731
    return {
        "code": project.code, "name": project.name, "status": project.status, "enabled": project.enabled, "total_customers": len(rows),
        "used": n("used"), "not_used": n("not_used"), "expired": n("expired"), "expiring_this_month": n("expiring"),
        "trial_licenses": n("trial"), "annual_licenses": n("annual"), "lifetime_licenses": n("lifetime"), "pending_approval": n("pending"),
        "active_devices": sum(r["active_devices"] for r in rows),
        "speed_users": n("speed"), "normal_users": n("normal"), "new_users": n("new"), "one_time_users": n("one_time"),
    }


def dashboard(now=None):
    now = now or timezone.now()
    projects = [project_stats(p, now) for p in IntegrationProject.objects.all()]
    overall = {
        "total_unique_customers": IntegrationCustomer.objects.values("email").distinct().count(),
        "total_licenses": sum(p["total_customers"] for p in projects),
        "used": sum(p["used"] for p in projects), "not_used": sum(p["not_used"] for p in projects),
        "expired": sum(p["expired"] for p in projects), "expiring_this_month": sum(p["expiring_this_month"] for p in projects),
        "active_devices": sum(p["active_devices"] for p in projects),
    }
    return {"overall": overall, "projects": projects}
