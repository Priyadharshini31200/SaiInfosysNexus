"""UNIVERSE: the complete, configuration-driven lifecycle for NEW Universal projects (project.license_policy == "CALENDAR").

    external registration -> Incoming Registrations queue (Pending Review / Pending Tally Verification) -> staff notification
    -> Superadmin "Save & Allow" (credentials + licence + internal activation key, one transaction) -> Nexus login verification
    -> single-device authorisation with confirmed transfer.

Legacy products (PRN, GSTR2Tally, Mothys) are never routed here: `is_universe()` is false for them and their code paths are untouched."""
import json
import re
import secrets
import string
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import services as s
from . import tally
from .models import (IntegrationActivationKey, IntegrationCustomer, IntegrationLicense, IntegrationNotification, IntegrationRegistration,
                     IntegrationSession, IntegrationTallyInfo)
from .services import IntegrationError

Reg = IntegrationRegistration
OFFLINE_LEASE_SECONDS = 24 * 3600  # an offline client may keep working at most this long without a successful introspection / authorisation
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LICENSE_TYPES = ("TRIAL", "ONE_YEAR", "LIFETIME", "CUSTOM")
USER_ID_RE = re.compile(r"^9\d{8}$")


def new_user_id():
    """Nexus User ID for Universal projects: 9 digits starting with 9, unique across every customer User ID and serial."""
    for _ in range(50):
        uid = "9" + "".join(str(secrets.randbelow(10)) for _ in range(8))
        if not IntegrationCustomer.objects.filter(Q(user_id=uid) | Q(serial_number=uid)).exists():
            return uid
    raise IntegrationError("SERIAL_EXHAUSTED", "Could not allocate a free User ID.", 503)


def check_user_id(uid):
    uid = str(uid or "").strip()
    if not USER_ID_RE.match(uid):
        raise IntegrationError("VALIDATION_ERROR", "User ID must be exactly 9 digits starting with 9.", 422, {"fields": {"user_id": ["Exactly 9 digits, starting with 9."]}})
    if IntegrationCustomer.objects.filter(Q(user_id=uid) | Q(serial_number=uid)).exists():
        raise IntegrationError("DUPLICATE_USER_ID", "This User ID is already in use. Choose another or regenerate.", 409, {"fields": {"user_id": ["Already in use."]}})
    return uid


def check_password(pw):
    """Admin-chosen temporary password for a NEW UNIVERSE customer: 3-128 characters, any characters, no complexity rule.
    It is stored only as a hash, must be changed at first login, and login rate limiting / lockout still apply.
    Legacy MOTHYS / GSTR2Tally / Barcode password policies are separate and unchanged."""
    pw = "" if pw is None else str(pw)
    if not 3 <= len(pw) <= 128:
        msg = "Password is required." if not pw else "Temporary password: 3-128 characters."
        raise IntegrationError("VALIDATION_ERROR", msg, 422, {"fields": {"password": [msg]}})
    return pw


def is_universe(project):
    return project.license_policy == "CALENDAR"


# ------------------------------------------------------------------ secrets
def temp_password():
    """9 characters, cryptographically random, at least one upper-case letter, one lower-case letter and one digit (no look-alike characters)."""
    upper, lower, digits = "ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789"
    chars = [secrets.choice(upper), secrets.choice(lower), secrets.choice(digits)] + [secrets.choice(upper + lower + digits) for _ in range(6)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def internal_key():
    return "".join(secrets.choice(KEY_ALPHABET) for _ in range(9))


# ------------------------------------------------------------------ ingest
def _notify(reg):
    IntegrationNotification.objects.create(project=reg.project, registration=reg, title=f"New Customer Registration – {reg.project.name}")


def _evaluate(reg, c, tally_data):
    """Sets status / tally_error from the data. A failed Tally check never turns the project into Non-Tally."""
    if reg.status in (Reg.APPROVED, Reg.REJECTED, Reg.HELD):
        return
    if reg.project.tally_serial_required:
        clean, problems = tally.validate_tally(tally_data, c)
        if not problems and IntegrationCustomer.objects.filter(serial_number=clean["tally_serial"]).exists():
            problems.append("This Tally Serial Number is already registered in Nexus.")
        reg.status, reg.tally_error = (Reg.PENDING_TALLY, " ".join(problems)[:300]) if problems else (Reg.PENDING_REVIEW, "")
    else:
        reg.status, reg.tally_error = Reg.PENDING_REVIEW, ""


def ingest(project, record, tally_data=None, actor="sync"):
    """Idempotent upsert of an incoming registration. Returns (registration, created). Never creates a customer or licence."""
    c = s._clean_customer(record)
    existing = IntegrationCustomer.objects.filter(project=project, external_customer_id=c["external_customer_id"]).first()
    if existing:
        raise IntegrationError("CUSTOMER_ALREADY_REGISTERED", "This external customer id is already registered.", 409, {"customer_id": existing.pk})
    clash = (IntegrationCustomer.objects.filter(project=project, email=c["email"]).exists()
             or Reg.objects.filter(project=project, email=c["email"]).exclude(external_customer_id=c["external_customer_id"]).exists())
    if clash:
        raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this project already uses this email address.", 409)
    with transaction.atomic():
        extra = {"company_name": str(record.get("company_name") or "").strip()[:150], "address": str(record.get("address") or "").strip()[:300], "gstin": str(record.get("gstin") or "").strip().upper()[:15]}
        reg, created = Reg.objects.get_or_create(project=project, external_customer_id=c["external_customer_id"],
                                                 defaults={"name": c["customer_name"], "email": c["email"], "contact_number": c["contact_number"],
                                                           "source": {"manual": "MANUAL", "sync": "API SYNC", "push": "PUSH", "webhook": "WEBHOOK", "scheduler": "API SYNC"}.get(actor, "API SYNC"), **extra})
        if not created and reg.status == Reg.APPROVED:
            raise IntegrationError("CUSTOMER_ALREADY_REGISTERED", "This registration was already approved.", 409)
        reg.name, reg.email, reg.contact_number = c["customer_name"], c["email"], c["contact_number"]
        if not created and reg.status not in (Reg.APPROVED,):
            reg.company_name, reg.address, reg.gstin = extra["company_name"] or reg.company_name, extra["address"] or reg.address, extra["gstin"] or reg.gstin
        payload = tally_data if isinstance(tally_data, dict) and tally_data else json.loads(reg.tally_payload or "{}")
        reg.tally_payload = json.dumps(payload, default=str)
        _evaluate(reg, c, payload)
        reg.save()
        if created:
            _notify(reg)
            s.audit(project, "registration.received", reg.external_customer_id, actor=actor, detail=reg.status)
    return reg, created


def ingest_response(reg):
    if reg.status == Reg.PENDING_TALLY:
        problems = [reg.tally_error] if reg.tally_error else []
        return 202, {"status": "PENDING_TALLY_VERIFICATION", "title": tally.TITLE, "message": tally.MESSAGE, "problems": problems,
                     "pending_id": reg.pk, "license_issued": False}
    return 202, {"status": reg.status, "registration_id": reg.pk, "license_issued": False,
                 "message": "Registration received. Credentials and the licence are issued only after approval in Nexus."}


def submit_tally(project, data, actor="connector"):
    """Secure connector / agent path: the project backend submits the customer's Tally details for a pending registration."""
    reg = Reg.objects.filter(project=project, external_customer_id=str(data.get("external_customer_id") or "")).first()
    if not reg:
        raise IntegrationError("REGISTRATION_NOT_FOUND", "No pending registration has this external_customer_id.", 404)
    if reg.status == Reg.APPROVED:
        raise IntegrationError("CUSTOMER_ALREADY_REGISTERED", "This registration was already approved.", 409)
    c = {"external_customer_id": reg.external_customer_id, "customer_name": reg.name, "email": reg.email, "contact_number": reg.contact_number}
    reg.tally_payload = json.dumps(data.get("tally") or {}, default=str)
    reg.tally_attempts += 1
    _evaluate(reg, c, data.get("tally") or {})
    reg.save()
    s.audit(project, "tally.submitted", reg.external_customer_id, ok=reg.status != Reg.PENDING_TALLY, actor=actor, detail=reg.tally_error[:200])
    return ingest_response(reg)


def retry_tally(reg, fetch, actor=""):
    """Authorised Retry Verification: re-read the details from the project's configured Tally License Details API."""
    if not reg.project.tally_serial_required:
        raise IntegrationError("TALLY_NOT_REQUIRED", "This project does not require Tally verification.", 409)
    fresh = fetch(reg.project, reg.external_customer_id)
    if fresh is None:
        raise IntegrationError("TALLY_SOURCE_NOT_CONFIGURED", "The Tally License Details API is not configured for this project.", 409)
    payload = {**json.loads(reg.tally_payload or "{}"), **{k: v for k, v in fresh.items() if v not in (None, "")}}
    c = {"external_customer_id": reg.external_customer_id, "customer_name": reg.name, "email": reg.email, "contact_number": reg.contact_number}
    reg.tally_payload, reg.tally_attempts = json.dumps(payload, default=str), reg.tally_attempts + 1
    _evaluate(reg, c, payload)
    reg.save()
    s.audit(reg.project, "tally.retry", reg.external_customer_id, ok=reg.status != Reg.PENDING_TALLY, actor=actor, detail=reg.tally_error[:200])
    return reg


# ------------------------------------------------------------------ licence terms
def term(project, license_type, trial_days=None, years=None, now=None, custom_days=None):
    """Validated licence term + start/expiry preview. Trial: positive days (default = project default, 3). Yearly: positive calendar years.
    Custom: a positive number of days from the start date. Lifetime: no expiry."""
    now = now or timezone.now()
    if license_type not in LICENSE_TYPES:
        raise IntegrationError("VALIDATION_ERROR", "Choose Trial, Yearly, Lifetime or Custom.", 422, {"fields": {"license_type": ["Choose Trial, Yearly, Lifetime or Custom."]}})
    out = {"license_type": license_type, "starts_at": now, "expires_at": None, "trial_days": None, "years": None}
    if license_type == "CUSTOM":
        try:
            days = int(custom_days)
            if not 1 <= days <= 3650:
                raise ValueError
        except (TypeError, ValueError):
            raise IntegrationError("VALIDATION_ERROR", "Custom duration must be a whole number of days (1-3650).", 422, {"fields": {"custom_days": ["Enter 1-3650 days."]}})
        out.update(trial_days=days, expires_at=now + timedelta(days=days))  # days travel in trial_days (the licence grant field for day counts)
    if license_type == "TRIAL":
        try:
            days = int(trial_days if trial_days not in (None, "") else project.trial_days)
            if not 1 <= days <= 3650:
                raise ValueError
        except (TypeError, ValueError):
            raise IntegrationError("VALIDATION_ERROR", "Trial days must be a positive whole number.", 422, {"fields": {"trial_days": ["Enter a positive whole number."]}})
        out.update(trial_days=days, expires_at=now + timedelta(days=days))
    elif license_type == "ONE_YEAR":
        try:
            y = int(years if years not in (None, "") else project.validity_years)
            if not 1 <= y <= 10:
                raise ValueError
        except (TypeError, ValueError):
            raise IntegrationError("VALIDATION_ERROR", "Years must be a positive whole number (1-10).", 422, {"fields": {"years": ["Enter a whole number from 1 to 10."]}})
        out.update(years=y, expires_at=s.add_calendar_years(now, y))
    return out


# ------------------------------------------------------------------ decisions
def _decide(reg, status, actor, note=""):
    reg.status, reg.decided_by, reg.decided_at, reg.note = status, actor[:80], timezone.now(), note[:300]
    reg.save()
    s.audit(reg.project, f"registration.{status.lower()}", reg.external_customer_id, actor=actor, detail=note[:200])


def hold(reg_id, actor, note=""):
    with transaction.atomic():
        reg = Reg.objects.select_for_update().get(pk=reg_id)
        if reg.status not in (Reg.PENDING_REVIEW, Reg.PENDING_TALLY, Reg.HELD):
            raise IntegrationError("INVALID_STATE", f"A {reg.status.lower()} registration cannot be put on hold.", 409)
        _decide(reg, Reg.HELD, actor, note)
    return reg


def reject(reg_id, actor, note=""):
    with transaction.atomic():
        reg = Reg.objects.select_for_update().get(pk=reg_id)
        if reg.status == Reg.APPROVED:
            raise IntegrationError("INVALID_STATE", "An approved registration cannot be rejected; suspend the licence instead.", 409)
        _decide(reg, Reg.REJECTED, actor, note)
    return reg


def approve(reg_id, license_type, trial_days=None, years=None, actor="", app_url="", password=None, user_id=None, custom_days=None):
    """Save & Allow. One transaction: revalidate, allocate the User ID, create the customer with a hashed temporary password, the licence,
    the internal activation key (hash only) and the audit record. Idempotent: approving twice returns the same customer without a password."""
    with transaction.atomic():
        reg = Reg.objects.select_for_update().select_related("project").get(pk=reg_id)
        project = reg.project
        if reg.status == Reg.APPROVED and reg.customer_id:
            lic = reg.customer.license
            return {"already_approved": True, "customer_id": reg.customer_id, "user_id": reg.customer.user_id, "license": s.license_payload(lic), "temporary_password": None}
        if reg.status == Reg.PENDING_TALLY:
            raise IntegrationError("TALLY_VERIFICATION_REQUIRED", f"{tally.TITLE}. Verify the Tally details before approving.", 409, {"title": tally.TITLE, "problems": [reg.tally_error]})
        if reg.status not in (Reg.PENDING_REVIEW, Reg.HELD):
            raise IntegrationError("INVALID_STATE", f"A {reg.status.lower().replace('_', ' ')} registration cannot be approved.", 409)
        t = term(project, license_type, trial_days, years, custom_days=custom_days)
        password = check_password(password) if password is not None else None  # admin-chosen temporary password (validated, hashed below)
        uid = check_user_id(user_id) if user_id not in (None, "") else new_user_id()  # admin-edited or generated Nexus User ID
        c = s._clean_customer({"external_customer_id": reg.external_customer_id, "customer_name": reg.name, "email": reg.email, "contact_number": reg.contact_number})
        if IntegrationCustomer.objects.filter(project=project, email=c["email"]).exists():
            raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this project already uses this email address.", 409)
        info = None
        if project.tally_serial_required:  # revalidated at approval time, not trusted from the earlier check
            clean, problems = tally.validate_tally(json.loads(reg.tally_payload or "{}"), c)
            if problems:
                raise IntegrationError("TALLY_VERIFICATION_REQUIRED", f"{tally.TITLE}: " + " ".join(problems), 409, {"title": tally.TITLE, "problems": problems})
            serial = clean["tally_serial"]
            info = {k: clean[k] for k in ("tally_serial", "edition", "tss_active", "tss_expiry", "license_admin", "virtual_user_limit", "gateway_server")}
            if IntegrationCustomer.objects.filter(serial_number=serial).exists():
                raise IntegrationError("DUPLICATE_TALLY_SERIAL", "This Tally Serial Number is already registered in Nexus.", 409)
        else:
            serial = uid  # Non-Tally: the Nexus User ID is also the product serial
        # Tally projects keep the verified Tally serial in serial_number (+ IntegrationTallyInfo) and get a separate 9-digit Nexus User ID.
        password, key = password or temp_password(), internal_key()  # stored only as a hash; key = internal activation reference (hash only)
        try:
            customer = IntegrationCustomer.objects.create(project=project, external_customer_id=reg.external_customer_id, name=c["customer_name"], email=c["email"],
                                                          contact_number=c["contact_number"], serial_number=serial, user_id=uid,
                                                          company_name=reg.company_name, address=reg.address, gstin=reg.gstin,
                                                          password_hash=make_password(password), must_change_password=True)
        except IntegrityError:
            raise IntegrationError("DUPLICATE_USER_ID", "The User ID is already in use; try again.", 409)
        lic = IntegrationLicense(customer=customer, license_type=license_type, status=IntegrationLicense.PENDING)
        s.grant(lic, license_type, days=t["trial_days"], years=t["years"], actor=actor)
        IntegrationActivationKey.objects.create(license=lic, key_hash=s._hmac(f"{lic.pk}:{key}"), expires_at=timezone.now() + timedelta(days=36500), internal=True)
        if info:
            IntegrationTallyInfo.objects.create(customer=customer, **info)
        reg.customer = customer
        _decide(reg, Reg.APPROVED, actor, f"{license_type}")
        s.audit(project, "registration.save_and_allow", customer.user_id, actor=actor, detail=f"{license_type} until {lic.expires_at}")
    del key
    setup_email = send_setup_email(customer, app_url, actor, temporary_password=password)  # AFTER the commit: an SMTP failure never undoes the approval
    return {"already_approved": False, "customer_id": customer.pk, "user_id": customer.user_id, "serial_number": customer.serial_number,
            "license": s.license_payload(lic), "temporary_password": password, "setup_email": setup_email,
            "password_notice": "Shown once here and sent once in the account email. Stored only as a hash; the customer must change it at first login."}


SETUP_SALT, SETUP_HOURS = "nexus.int.customer-setup", 72


def setup_token(customer):
    """Signed, expiring, SINGLE-USE token: it embeds the tail of the current password hash, so setting the password invalidates it."""
    return signing.dumps({"c": customer.pk, "h": customer.password_hash[-16:]}, salt=SETUP_SALT)


def read_setup_token(token):
    try:
        data = signing.loads(str(token or ""), salt=SETUP_SALT, max_age=SETUP_HOURS * 3600)
        customer = IntegrationCustomer.objects.select_related("project", "license").get(pk=data["c"])
    except (signing.BadSignature, IntegrationCustomer.DoesNotExist, KeyError, TypeError):
        raise IntegrationError("SETUP_LINK_INVALID", "This password setup link is invalid or has expired. Ask Sai Infosys to resend it.", 400)
    if customer.password_hash[-16:] != data["h"]:
        raise IntegrationError("SETUP_LINK_USED", "This password setup link has already been used. Ask Sai Infosys to resend it.", 400)
    return customer


def send_setup_email(customer, app_url="", actor="", temporary_password=None):
    """Account-ready email: User ID, licence and - on the first Save & Allow only - the one-time temporary password (must be changed at first
    login), plus the password-setup link. NEVER the internal activation key, the API secret or any stored hash. Returns the mail status."""
    from django.conf import settings
    from apps.registrations import mailer
    project, lic = customer.project, customer.license
    from urllib.parse import urlparse
    base = (app_url or getattr(settings, "NEXUS_PUBLIC_APP_URL", "") or "").rstrip("/")
    local = (urlparse(base).hostname or "").lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or (urlparse(base).hostname or "").endswith(".local")
    # The first Save & Allow emails the temporary password, so no setup link is needed; a link is never built from a local/dev address.
    link = f"{base}/setup-customer-password?token={setup_token(customer)}" if base and not local and not temporary_password else ""
    plan = {"ONE_YEAR": "Yearly"}.get(lic.license_type) or s.PLAN_LABEL.get(lic.license_type, lic.license_type)  # duration is shown separately
    if lic.license_type == IntegrationLicense.LIFETIME:
        duration, expiry = "Lifetime", "No expiry"
    else:
        span = (lic.expires_at - lic.starts_at).days if lic.expires_at and lic.starts_at else None
        years = round(span / 365) if span and lic.license_type == "ONE_YEAR" else None
        duration = f"{years} year{'s' if years != 1 else ''}" if years else (f"{span} days" if span is not None else "—")
        expiry = timezone.localtime(lic.expires_at).strftime("%d/%m/%Y") if lic.expires_at else "—"
    subject = f"Your {project.name} Account Is Ready – Sai Infosys Nexus"
    rows = [("Customer Name", customer.name), ("Product Name", project.name), ("User ID / Serial Number", customer.user_id)]
    if temporary_password:
        rows.append(("Temporary Password", temporary_password))
    rows += [("License Type", plan), ("License Duration", duration), ("Expiry Date", expiry)]
    if project.frontend_url:
        rows.append(("Login URL", project.frontend_url))
    if temporary_password:
        rows.append(("First login", "Sign in with your User ID and temporary password; you will be asked to set your own password."))
    if link:
        rows.append(("Set your password", link))
    note = f"\n\nThe password setup link is valid for {SETUP_HOURS} hours and can be used once." if link else ("\n\nKeep this email private and change the temporary password at your first login." if temporary_password else "")
    text = (f"Dear {customer.name},\n\nYour {project.name} account is ready.\n\n" + "\n".join(f"{k}: {v}" for k, v in rows) + note + "\n\nRegards,\nSai Infosys")
    status = mailer.deliver(subject, text, customer.email, html=mailer.render_html(subject, f"Your {project.name} account is ready.", rows),
                            kind="int_setup", project=project.code, event_id=f"int-setup-{customer.pk}-{secrets.token_hex(3)}")
    customer.welcome_email_status = status
    customer.save(update_fields=["welcome_email_status", "updated_at"])
    s.audit(project, "customer.setup_email", customer.user_id, ok=status in ("accepted", "not_delivered"), actor=actor, detail=f"{status} -> {customer.email}")
    return status


def complete_setup(token, password):
    customer = read_setup_token(token)
    if len(str(password or "")) < 8:
        raise IntegrationError("WEAK_PASSWORD", "Use at least 8 characters.", 400)
    customer.password_hash, customer.must_change_password = make_password(str(password)), False
    customer.failed_logins, customer.locked_until = 0, None
    customer.save(update_fields=["password_hash", "must_change_password", "failed_logins", "locked_until", "updated_at"])
    s.audit(customer.project, "customer.password_set", customer.user_id, actor="customer")
    return customer


def reset_password(customer, actor=""):
    """Authorised Reset Password Setup: a NEW 9-character temporary password (hashed, shown once to the operator, never emailed); the customer must change it
    at next login and every active session is revoked."""
    pw = temp_password()
    with transaction.atomic():
        customer.password_hash, customer.must_change_password = make_password(pw), True
        customer.failed_logins, customer.locked_until = 0, None
        customer.save(update_fields=["password_hash", "must_change_password", "failed_logins", "locked_until", "updated_at"])
        s._revoke_all(customer.license, "password_reset")
        s.audit(customer.project, "customer.password_reset", customer.user_id, actor=actor)
    return {"temporary_password": pw, "password_notice": "Shown once. It is stored only as a hash and is never emailed; the customer must change it at first login."}


def admin_set_password(customer, password, actor=""):
    """Authorised administrator-chosen password: hashed, customer must change it at next login, every session revoked."""
    if not 3 <= len(str(password or "")) <= 128:
        raise IntegrationError("WEAK_PASSWORD", "Password must be 3-128 characters.", 400)
    with transaction.atomic():
        customer.password_hash, customer.must_change_password = make_password(str(password)), True
        customer.failed_logins, customer.locked_until = 0, None
        customer.save(update_fields=["password_hash", "must_change_password", "failed_logins", "locked_until", "updated_at"])
        s._revoke_all(customer.license, "password_reset")
        s.audit(customer.project, "customer.password_set_by_admin", customer.user_id, actor=actor)


def ensure_expiry_notifications(now=None, days=7):
    """Idempotent: one notification per (licence, expiry date) for UNIVERSE licences expiring within `days`. Safe to call from any request or scheduled job."""
    now = now or timezone.now()
    made = 0
    for lic in (IntegrationLicense.objects.select_related("customer__project")
                .filter(customer__project__license_policy="CALENDAR", customer__project__enabled=True, expires_at__isnull=False, expires_at__gt=now, expires_at__lte=now + timedelta(days=days))):
        if s.license_state(lic, now) != "ACTIVE":
            continue
        key = f"expiry:{lic.pk}:{lic.expires_at.date().isoformat()}"
        if IntegrationNotification.objects.filter(dedupe_key=key).exists():
            continue
        try:
            with transaction.atomic():
                IntegrationNotification.objects.create(project=lic.customer.project, customer=lic.customer, kind="EXPIRY", dedupe_key=key,
                                                       title=f"License expiring – {lic.customer.project.name}: {lic.customer.name}")
            made += 1
        except IntegrityError:
            pass
    return made


# ------------------------------------------------------------------ device authorisation (single device, confirmed transfer)
TRANSFER_TITLE = "Account Already Active on Another System"
TRANSFER_MESSAGE = "Continuing will log out the previous system and activate this system. Do you want to continue?"


def authorize_device(project, data, ip=""):
    customer = s.verify_credentials(project, data.get("user_id"), data.get("password"), ip)
    device_id = str(data.get("device_id") or "").strip()[:120]
    if not device_id:
        raise IntegrationError("VALIDATION_ERROR", "device_id is required.", 422, {"fields": {"device_id": ["Required."]}})
    device_name = str(data.get("device_name") or "")[:150]
    confirm = data.get("confirm_transfer")
    if confirm not in (None, True, False):
        raise IntegrationError("VALIDATION_ERROR", "confirm_transfer must be true (OK - Continue), false (Cancel) or omitted.", 422)
    with transaction.atomic():
        lic = IntegrationLicense.objects.select_for_update().select_related("customer__project").get(pk=customer.license.pk)
        state = s.license_state(lic)
        if state != "ACTIVE":
            s.audit(project, "device.denied", customer.user_id, ok=False, ip=ip, detail=state)
            raise IntegrationError(f"LICENSE_{state}", "License Expired - renewal required." if state == "EXPIRED" else f"The licence is {state.lower().replace('_', ' ')}.", 403,
                                   {"license_state": state, "authorized": False})
        if customer.must_change_password:
            raise IntegrationError("PASSWORD_CHANGE_REQUIRED", "Change the temporary password (auth/change-password) before the first device is authorised.", 403)
        active = list(lic.sessions.filter(status=IntegrationSession.ACTIVE))
        others = [x for x in active if x.device_id != device_id]
        transferred = False
        if others and len(others) >= project.device_limit:
            previous = others[0].device_name or others[0].device_id
            if confirm is None:
                raise IntegrationError("TRANSFER_CONFIRMATION_REQUIRED", TRANSFER_MESSAGE, 409,
                                       {"title": TRANSFER_TITLE, "previous_device": previous, "buttons": ["OK – Continue", "Cancel"], "authorized": False})
            if confirm is False:  # Cancel: nothing changes
                s.audit(project, "transfer.cancelled", customer.user_id, ip=ip)
                return {"status": "CANCELLED", "transferred": False, "previous_device_still_authorized": True}
            transferred = True
        s._revoke_all(lic, "device_transfer" if transferred else "replaced_on_same_device")
        token, session = s._new_session(lic, device_id, device_name, project)
        s._record(project, lic, "DEVICE_TRANSFER" if transferred else "DEVICE_ACTIVATION", session, device_id)
        body = s._session_body(token, session, lic)
        body.update(status="TRANSFERRED" if transferred else "AUTHORIZED", transferred=transferred, authorized=True, offline_lease_seconds=OFFLINE_LEASE_SECONDS,
                    revoked_sessions=[x.pk for x in active])
    if transferred:
        s.audit(project, "transfer.confirmed", customer.user_id, ip=ip, detail=f"from={previous[:100]};to={(device_name or device_id)[:100]}")
    return body


# ------------------------------------------------------------------ queue rows for the UI
def row(reg, now=None):
    p = reg.project
    c = {"external_customer_id": reg.external_customer_id, "customer_name": reg.name, "email": reg.email, "contact_number": reg.contact_number}
    tally_result = "Not required"
    if p.tally_serial_required:
        _, problems = tally.validate_tally(json.loads(reg.tally_payload or "{}"), c)
        tally_result = "Verified" if not problems and not reg.tally_error else "Tally License Details Required"  # same verdict as the stored status (incl. duplicate serial)
    t = term(p, "TRIAL", None, None, now)
    customer = reg.customer
    return {
        "id": reg.pk, "project": p.name, "project_code": p.code, "external_customer_id": reg.external_customer_id, "name": reg.name, "contact_number": reg.contact_number,
        "email": reg.email, "registered_at": reg.created_at, "tally_required": p.tally_serial_required, "tally_result": tally_result, "tally_error": reg.tally_error,
        "user_id": customer.user_id if customer else "Assigned on approval",  # Nexus User ID (9 digits, starts with 9) - separate from the Tally serial
        "tally_serial": (customer.serial_number if customer else json.loads(reg.tally_payload or "{}").get("serial")) if p.tally_serial_required and (customer or tally_result == "Verified") else None,
        "password_status": "Set (temporary, hashed)" if customer else "Generated on approval",
        "activation_status": "Provisioned (internal)" if customer else "Generated on approval",
        "status": reg.status, "default_trial_days": p.trial_days, "default_years": p.validity_years,
        "preview": {"license_type": "TRIAL", "starts_at": t["starts_at"], "expires_at": t["expires_at"]}, "decided_by": reg.decided_by, "decided_at": reg.decided_at,
    }
