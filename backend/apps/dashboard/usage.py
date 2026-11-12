"""Project usage counts and lists for the Super Admin dashboard.

Each project keeps its own database on the same SQL Server instance. This module reads them
(read-only, through the default connection and three-part names) and reduces both projects to one
shape: a `base` set with exactly ONE row per registered customer/device and the time of its latest
successful usage event (NULL when it never succeeded). Used and Not Used counts/lists are
filters over that one set, so the summary always matches the lists and nothing is counted twice:

    Used        last_used IS NOT NULL
    Not Used    last_used IS NULL

Expiring This Month is separate: ProjectCustomer.expires_at (Nexus's own records) inside the current calendar month in
Asia/Kolkata, for ACTIVE-status Trial and 1-Year customers of the project (Lifetime excluded; see _expiring).

GSTR2Tally (database Gstr_db)
    registered   ACTIVE rows of licensed_device_tbl (one per device), plus unmatched ACTIVE Nexus registrations
    usage event  tally_import_job_tbl.status = 'COMPLETED', attributed to a device through the job's
                 local agent (or, for a job with no agent, through its batch's license when that license
                 has exactly one device)
    customer     superadmin_customerprofile.business_name, else the company registered for the
                 license's GSTIN, else the customer's username

Barcode AutoPrint (Nexus, plus the optional legacy database PRNPRINT)
    registered   the ACTIVE-status customers Nexus holds (authoritative), merged by serial number with the legacy
                 Customers / non-revoked Licenses (one per license) ONLY when that database is configured, readable and
                 verified (legacy_state); otherwise Nexus alone, and the summary says it is not complete
    usage event  the license was activated, validated, or seen by a device activation, or (in Nexus) the enrolled
                 device was activated/enrolled or reported real use (never merely record creation)
"""
import logging
import re
from functools import wraps
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection
from django.db.models import Q
from django.utils import timezone

from apps.registrations import services
from apps.registrations.models import ProjectCustomer
from apps.registrations.serializers import customer_data

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
CATEGORIES = ("used", "not-used", "expiring", "expired")
PAGE_SIZES = (10, 25, 50, 100)

_DB_NAME = re.compile(r"[A-Za-z0-9_]{1,128}")


LEGACY_NOTES = {
    "not_configured": "Counts are for customers registered in Nexus. The legacy PRN database is not configured.",
    "invalid_configuration": "Counts are for customers registered in Nexus. The legacy PRN database setting is invalid.",
    "inaccessible": "Counts are for customers registered in Nexus. The legacy PRN database cannot be read, so older licences are not included.",
}


class ProjectUnavailable(Exception):
    """The project's database cannot be read right now."""


def _source_errors(function):
    """Keep configuration, runtime SQL and Nexus expiry failures in the same error contract."""
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (DatabaseError, ImproperlyConfigured) as exc:
            raise ProjectUnavailable(str(exc)) from exc
    return guarded


def _gstr_base(db):
    return f"""
    job_use AS (
        SELECT a.device_id AS device_id, COALESCE(j.completed_at, j.started_at) AS used_at
        FROM [{db}].dbo.tally_import_job_tbl j
        JOIN [{db}].dbo.local_tally_agent_tbl a ON a.id = j.local_agent_id
        WHERE j.status = 'COMPLETED' AND a.device_id IS NOT NULL
        UNION ALL
        SELECT d.id, COALESCE(j.completed_at, j.started_at)
        FROM [{db}].dbo.tally_import_job_tbl j
        JOIN [{db}].dbo.gst_import_batch_tbl b ON b.id = j.batch_id
        JOIN [{db}].dbo.licensed_device_tbl d ON d.license_id = b.product_license_id
        WHERE j.status = 'COMPLETED' AND j.local_agent_id IS NULL
          AND (SELECT COUNT(*) FROM [{db}].dbo.licensed_device_tbl x WHERE x.license_id = d.license_id) = 1
    ),
    device_use AS (
        SELECT device_id, MAX(used_at) AS last_used FROM job_use GROUP BY device_id
    ),
    runtime AS (
        SELECT d.id AS unit_id,
               COALESCE(NULLIF(cp.business_name, ''), NULLIF(co.company_name, ''), NULLIF(u.username, ''), '') AS customer_name,
               COALESCE(NULLIF(d.device_name, ''), LEFT(d.device_fingerprint, 12)) AS device_name,
               COALESCE(pl.licensed_tally_serial, '') AS serial,
               du.last_used AS last_used
        FROM [{db}].dbo.licensed_device_tbl d
        JOIN [{db}].dbo.product_license_tbl pl ON pl.id = d.license_id
        LEFT JOIN [{db}].dbo.auth_user u ON u.id = pl.customer_id
        OUTER APPLY (SELECT TOP 1 business_name FROM [{db}].dbo.superadmin_customerprofile p
                     WHERE p.user_id = pl.customer_id ORDER BY p.id) cp
        OUTER APPLY (SELECT TOP 1 company_name FROM [{db}].dbo.companydetails_tbl c
                     WHERE pl.licensed_gstin <> '' AND c.gstin = pl.licensed_gstin ORDER BY c.id) co
        LEFT JOIN device_use du ON du.device_id = d.id
        WHERE d.status = 'ACTIVE'
    ),
    {_nexus_units("gstr2tally")},
    base AS (
        SELECT r.unit_id, r.customer_name, r.device_name, r.serial, r.last_used
        FROM runtime r
        UNION ALL
        SELECT -n.unit_id, n.customer_name, n.device_name, n.serial, n.last_used
        FROM nexus n WHERE n.serial = '' OR NOT EXISTS (SELECT 1 FROM runtime r WHERE r.serial = n.serial)
    )"""


def _nexus_units(code):
    """CTE `nexus`: one row per ACTIVE-status customer registered in Nexus for a project, with the last time its
    enrolled device really used the app (device usage reports). Cancelled and inactive licences are not units.
    `code` is one of our own project codes, never user input."""
    last_used = "COALESCE(d.last_used_at, c.first_activated_at, d.enrolled_at)" if code == "prn-auto-print" else "d.last_used_at"
    return f"""
    nexus AS (
        SELECT c.id AS unit_id,
               COALESCE(NULLIF(c.company_name, ''), c.name) AS customer_name,
               COALESCE(d.device_name, '') AS device_name,
               c.serial_number AS serial,
               {last_used} AS last_used
        FROM registrations_projectcustomer c
        LEFT JOIN registrations_deviceregistration d ON d.customer_id = c.id AND d.revoked_at IS NULL
        WHERE c.project = '{code}' AND c.status = 'ACTIVE'
    )"""


def _prn_base(db, legacy=True):
    """Barcode AutoPrint units. Nexus is authoritative: every ACTIVE-status customer registered in Nexus is a unit, with the usage
    Nexus itself records for the enrolled device. The PRN project's own database (historical, not-yet-linked licences) is merged
    by serial number ONLY when `legacy` is True, i.e. it is configured, readable and verified (see legacy_state)."""
    if not legacy or not db:
        return f"""
    {_nexus_units("prn-auto-print")},
    base AS (
        SELECT n.unit_id, n.customer_name, n.device_name, n.serial, n.last_used FROM nexus n
    )"""
    return f"""
    legacy AS (
        SELECT c.id * 1000000000 + COALESCE(l.id, 0) AS unit_id,
               c.name AS customer_name,
               COALESCE(NULLIF(l.activated_device_name, ''), NULLIF(act.device_name, ''), '') AS device_name,
               COALESCE(l.serial_number, '') AS serial,
               (SELECT MAX(v) FROM (VALUES (l.last_validated_at), (l.activated_at), (act.last_seen)) AS t(v)) AS last_used
        FROM [{db}].dbo.Customers c
        LEFT JOIN [{db}].dbo.Licenses l ON l.customer_id = c.id AND l.status <> 'REVOKED'
        OUTER APPLY (SELECT MAX(a.last_seen_at) AS last_seen, MAX(a.device_name) AS device_name
                     FROM [{db}].dbo.LicenseActivations a WHERE a.license_id = l.id) act
    ),
    {_nexus_units("prn-auto-print")},
    base AS (
        SELECT g.unit_id, g.customer_name,
               COALESCE(NULLIF(g.device_name, ''), n.device_name, '') AS device_name, g.serial,
               (SELECT MAX(v) FROM (VALUES (g.last_used), (n.last_used)) AS t(v)) AS last_used
        FROM legacy g
        LEFT JOIN nexus n ON g.serial <> '' AND n.serial = g.serial
        WHERE NOT (g.serial <> '' AND EXISTS (
            SELECT 1 FROM registrations_projectcustomer x
            WHERE x.project = 'prn-auto-print' AND x.serial_number = g.serial AND x.status <> 'ACTIVE'))
        UNION ALL
        SELECT -n.unit_id, n.customer_name, n.device_name, n.serial, n.last_used
        FROM nexus n
        WHERE n.serial = '' OR NOT EXISTS (SELECT 1 FROM legacy g WHERE g.serial = n.serial)
    )"""


# Exactly what the legacy merge reads; probed with a real (zero-row) SELECT, which is authoritative for "accessible and verified".
_LEGACY_PROBES = (
    "SELECT TOP 0 id, name FROM [{db}].dbo.Customers",
    "SELECT TOP 0 id, customer_id, serial_number, status, activated_device_name, activated_at, last_validated_at FROM [{db}].dbo.Licenses",
    "SELECT TOP 0 license_id, device_name, last_seen_at FROM [{db}].dbo.LicenseActivations",
)


def legacy_state(code="prn-auto-print"):
    """{'included': bool, 'reason': str} for the optional legacy PRN database. Never raises: when it is not configured or cannot be
    read, the dashboard counts Nexus customers only and says so, instead of failing or presenting the counts as complete."""
    db = getattr(settings, PROJECTS[code]["db_setting"], "") or ""
    if not db:
        return {"included": False, "reason": "not_configured"}
    if not _DB_NAME.fullmatch(db):
        return {"included": False, "reason": "invalid_configuration"}
    try:
        with connection.cursor() as cursor:
            for probe in _LEGACY_PROBES:
                cursor.execute(probe.format(db=db))
    except DatabaseError as exc:
        logger.warning("Dashboard: legacy %s database is not readable (%s); counting Nexus customers only", db, exc)
        return {"included": False, "reason": "inaccessible"}
    return {"included": True, "reason": ""}


PROJECTS = {
    "gstr2tally": {"label": "GSTR2Tally", "db_setting": "GSTR_DB_NAME", "base": _gstr_base},
    "prn-auto-print": {"label": "Barcode AutoPrint", "db_setting": "PRN_DB_NAME", "base": _prn_base},
}


def _base_sql(code, legacy=None):
    project = PROJECTS[code]
    db = getattr(settings, project["db_setting"], "") or ""
    if code == "prn-auto-print":              # Nexus-only unless the legacy database is configured, readable and verified
        if legacy is None:
            legacy = legacy_state(code)["included"]
        if not legacy:
            return ";WITH" + project["base"](db, legacy=False)
    elif not _DB_NAME.fullmatch(db):
        raise ImproperlyConfigured(f"{project['db_setting']} must be a plain database name.")
    return ";WITH" + project["base"](db)


def month_bounds(now=None):
    """[first instant of the current calendar month, first instant of the next) in Asia/Kolkata (no DST)."""
    now = (now or datetime.now(IST)).astimezone(IST)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, following


def _expiring(code, now=None):
    """Customers registered in Nexus for this project whose effective license expiry falls in the current month (IST):
    a trial (PRN: first activation + its trial days) or a 1-Year licence. Lifetime licences never expire and are left out,
    as are cancelled and inactivated ones; a licence that already expired earlier this month still counts."""
    start, following = month_bounds(now)
    return services.with_expiry(ProjectCustomer.objects.filter(project=code, status=ProjectCustomer.ACTIVE)).filter(
        expires_at__gte=start, expires_at__lt=following
    )


def _expired(code, now=None):
    return services.with_expiry(ProjectCustomer.objects.filter(project=code, status=ProjectCustomer.ACTIVE)).filter(
        expires_at__lte=now or timezone.now())


def _like(term):
    escaped = re.sub(r"([\\%_\[])", r"\\\1", term)
    return f"%{escaped}%"


def _category_filter(category):
    if category == "used":
        return "last_used IS NOT NULL", []
    return "last_used IS NULL", []


def _run(sql, params):
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    except (DatabaseError, ImproperlyConfigured) as exc:
        raise ProjectUnavailable(str(exc)) from exc


@_source_errors
def summary(code, now=None):
    """{used, not_used, expiring, total} for one project. Used / Not Used come from the project's own database;
    Expiring This Month from the license expiry dates Nexus holds. Raises ProjectUnavailable."""
    extra = {}
    if code == "prn-auto-print":
        legacy = legacy_state(code)
        extra = {"legacy_included": legacy["included"], "complete": legacy["included"]}
        if not legacy["included"]:
            extra["note"] = LEGACY_NOTES.get(legacy["reason"], LEGACY_NOTES["inaccessible"])
        base = _base_sql(code, legacy["included"])
    else:
        base = _base_sql(code)
    sql = base + """
    SELECT COUNT(*), COALESCE(SUM(CASE WHEN last_used IS NOT NULL THEN 1 ELSE 0 END), 0) FROM base"""
    total, used = _run(sql, [])[0]
    return {"total": total, "used": used, "not_used": total - used, "expiring": _expiring(code, now).count(), "expired": _expired(code, now).count(), **extra}


def _expiring_listing(code, search, page, page_size, now, category="expiring"):
    customers = _expired(code, now) if category == "expired" else _expiring(code, now)
    if search:
        customers = customers.filter(
            Q(name__icontains=search) | Q(company_name__icontains=search) | Q(serial_number__icontains=search) | Q(user_id__icontains=search)
        )
    customers = customers.select_related("created_by", "updated_by").order_by("expires_at", "id")
    offset = (page - 1) * page_size
    return {
        "total": customers.count(),
        # the full customer record (no password or key) so the dashboard can open the same View More drawer as the Customer List
        "results": [
            {"sno": offset + i + 1, "customer": f"{c.name} / {c.company_name}", "device": c.serial_number, "customer_record": customer_data(c)}
            for i, c in enumerate(customers[offset : offset + page_size])
        ],
    }


@_source_errors
def listing(code, category, search="", page=1, page_size=10, now=None):
    """One page of the units behind a count, plus the filtered total. Raises ProjectUnavailable."""
    if category in ("expiring", "expired"):
        return _expiring_listing(code, search, page, page_size, now, category)
    where, params = _category_filter(category)
    if search:
        like = _like(search)
        where += " AND (customer_name LIKE %s ESCAPE '\\' OR device_name LIKE %s ESCAPE '\\' OR serial LIKE %s ESCAPE '\\')"
        params += [like, like, like]
    base = _base_sql(code)
    total = _run(base + f" SELECT COUNT(*) FROM base WHERE {where}", params)[0][0]
    rows = _run(
        base
        + f""" SELECT customer_name, device_name, serial, last_used FROM base WHERE {where}
        ORDER BY CASE WHEN last_used IS NULL THEN 1 ELSE 0 END, last_used DESC, customer_name, unit_id
        OFFSET %s ROWS FETCH NEXT %s ROWS ONLY""",
        params + [(page - 1) * page_size, page_size],
    )
    results = []
    for index, (customer, device, serial, last_used) in enumerate(rows):
        results.append(
            {
                "sno": (page - 1) * page_size + index + 1,
                "customer": customer,
                "device": " · ".join(part for part in (device, serial) if part),
                "last_used": last_used.astimezone(IST).isoformat() if last_used else None,
                "status": "Used" if last_used else "Not Used",
            }
        )
    return {"total": total, "results": results}


@_source_errors
def report_rows(code, status="all", search=""):
    """Every unit of one project (optionally only Used or Not Used, and matching a search), newest use first. Same base set as
    the counts and lists above, so the Usage Report always agrees with the Dashboard. Raises ProjectUnavailable."""
    clauses, params = [], []
    if status in ("used", "not-used"):
        clauses.append(_category_filter(status)[0])
    if search:
        like = _like(search)
        clauses.append("(customer_name LIKE %s ESCAPE '\\' OR device_name LIKE %s ESCAPE '\\' OR serial LIKE %s ESCAPE '\\')")
        params += [like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _run(
        _base_sql(code)
        + f""" SELECT customer_name, device_name, serial, last_used FROM base {where}
        ORDER BY CASE WHEN last_used IS NULL THEN 1 ELSE 0 END, last_used DESC, customer_name, unit_id""",
        params,
    )
    return [
        {
            "customer": customer,
            "product": PROJECTS[code]["label"],
            "device": " · ".join(part for part in (device, serial) if part),
            "last_used": last_used.astimezone(IST).isoformat() if last_used else None,
            "status": "Used" if last_used else "Not Used",
        }
        for customer, device, serial, last_used in rows
    ]
