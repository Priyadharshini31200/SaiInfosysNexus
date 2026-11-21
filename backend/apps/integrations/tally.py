"""Tally verification for Tally-integrated Universal projects (project.tally_serial_required = True). Isolated service: Non-Tally projects,
Mothys, PRN, GSTR2Tally and Tally SL never reach this code.

Mandatory (all four): Tally Serial Number, Tally Edition, Tally Software Services (TSS) active, License Administrator.
Optional: TSS expiry, Virtual User limit, Gateway Server. The Tally Serial Number becomes the Nexus User ID; the Product ID stays separate.
If anything mandatory is missing/invalid the registration is stored as PENDING_TALLY_VERIFICATION: no customer, no serial, no credentials,
no licence, and the project is never silently treated as Non-Tally."""
import json
import re
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from . import services as s
from .models import IntegrationCustomer, IntegrationTallyInfo, IntegrationTallyPending
from .services import IntegrationError

TITLE = "Tally License Details Required"
MESSAGE = "This project requires Tally integration. Please connect TallyPrime and provide valid Tally license details to continue."
SERIAL = re.compile(r"^\d{9}$")
TRUE = {"true", "yes", "1", "active", "y"}


def _truthy(v):
    return v is True or str(v).strip().lower() in TRUE


def validate_tally(tally, customer):
    """Returns (clean, problems). `customer` is the cleaned registration (email / external id) the details must belong to."""
    t = tally if isinstance(tally, dict) else {}
    problems, clean = [], {}
    serial = str(t.get("serial") or "").strip()
    if not SERIAL.match(serial):
        problems.append("Tally Serial Number is missing or is not a valid 9-digit serial.")
    clean["tally_serial"] = serial
    edition = str(t.get("edition") or "").strip()
    if not edition:
        problems.append("Tally Edition is missing.")
    clean["edition"] = edition[:40]
    if not _truthy(t.get("tss_active")):
        problems.append("Tally Software Services (TSS) is not active or was not provided.")
    clean["tss_active"] = _truthy(t.get("tss_active"))
    expiry = None
    if t.get("tss_expiry"):
        expiry = parse_date(str(t["tss_expiry"])[:10])
        if expiry is None:
            problems.append("TSS expiry is not a valid date.")
        elif expiry < date.today():
            problems.append("TSS has expired.")
    clean["tss_expiry"] = expiry
    admin = str(t.get("license_admin") or "").strip()
    if not admin:
        problems.append("License Administrator is missing.")
    clean["license_admin"] = admin[:120]
    limit = t.get("virtual_user_limit")
    try:
        clean["virtual_user_limit"] = int(limit) if limit not in (None, "") else None
        if clean["virtual_user_limit"] is not None and clean["virtual_user_limit"] < 0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("Virtual User limit must be a positive number.")
        clean["virtual_user_limit"] = None
    clean["gateway_server"] = str(t.get("gateway_server") or "")[:150]
    # The details must belong to this customer: when the source states an owner, it has to match the registration.
    owner_email, owner_id = str(t.get("customer_email") or "").strip().lower(), str(t.get("external_customer_id") or "").strip()
    if (owner_email and owner_email != customer["email"]) or (owner_id and owner_id != customer["external_customer_id"]):
        problems.append("These Tally details belong to a different customer.")
    return clean, problems


def _pending(project, c, tally, problems):
    row, created = IntegrationTallyPending.objects.get_or_create(
        project=project, external_customer_id=c["external_customer_id"],
        defaults={"name": c["customer_name"], "email": c["email"], "contact_number": c["contact_number"]})
    row.name, row.email, row.contact_number = c["customer_name"], c["email"], c["contact_number"]
    row.tally_payload, row.error, row.status = json.dumps(tally or {}, default=str), " ".join(problems)[:300], "PENDING_TALLY_VERIFICATION"
    if not created:
        row.attempts += 1
    row.save()
    s.audit(project, "tally.pending", c["external_customer_id"], ok=False, detail=row.error[:300])
    return row


def register_tally_customer(project, record, tally, idem_key="", send_email=True, actor=""):
    """Validated Tally details -> normal registration with serial = user id = Tally serial. Otherwise -> Pending Tally Verification (HTTP 202)."""
    if not project.tally_serial_required:
        raise IntegrationError("TALLY_NOT_REQUIRED", "This project does not require Tally verification.", 409)
    c = s._clean_customer(record)
    existing = IntegrationCustomer.objects.filter(project=project, external_customer_id=c["external_customer_id"]).first()
    if existing:
        raise IntegrationError("CUSTOMER_ALREADY_REGISTERED", "This external customer id is already registered.", 409,
                               {"customer_id": existing.pk, "license_id": existing.license.pk, "serial_number": existing.serial_number})
    clean, problems = validate_tally(tally, c)
    if not problems and IntegrationCustomer.objects.filter(serial_number=clean["tally_serial"]).exists():
        problems.append("This Tally Serial Number is already registered in Nexus.")
    if problems:
        row = _pending(project, c, tally, problems)
        return 202, {"status": "PENDING_TALLY_VERIFICATION", "title": TITLE, "message": MESSAGE, "problems": problems, "pending_id": row.pk,
                     "license_issued": False}
    info = {k: clean[k] for k in ("tally_serial", "edition", "tss_active", "tss_expiry", "license_admin", "virtual_user_limit", "gateway_server")}
    status, body = s.register_customer(project, record, idem_key=idem_key, send_email=send_email, actor=actor, serial_override=clean["tally_serial"], tally_info=info)
    IntegrationTallyPending.objects.filter(project=project, external_customer_id=c["external_customer_id"]).delete()
    return status, body


def retry_verification(pending, fetch, actor=""):
    """Authorised retry. `fetch(project, external_customer_id)` returns the Tally details from the project's configured Tally API
    (None when none is configured). The stored details are used only when the source has nothing new."""
    project = pending.project
    fresh = fetch(project, pending.external_customer_id)
    if fresh is None:
        raise IntegrationError("TALLY_SOURCE_NOT_CONFIGURED", "The Tally License Details API is not configured for this project.", 409)
    record = {"external_customer_id": pending.external_customer_id, "customer_name": pending.name, "email": pending.email, "contact_number": pending.contact_number}
    status, body = register_tally_customer(project, record, fresh or json.loads(pending.tally_payload or "{}"), idem_key=f"tally-retry:{pending.external_customer_id}", actor=actor)
    s.audit(project, "tally.retry", pending.external_customer_id, ok=status == 201, actor=actor)
    return status, body


def pending_rows(project):
    return [{"id": p.pk, "external_customer_id": p.external_customer_id, "name": p.name, "email": p.email, "status": p.status, "error": p.error,
             "attempts": p.attempts, "title": TITLE, "message": MESSAGE, "updated_at": p.updated_at} for p in project.tally_pending.order_by("-id")]
