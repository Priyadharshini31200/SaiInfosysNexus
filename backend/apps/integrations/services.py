"""Business rules of the Universal Licensing API. Views stay thin; everything transactional lives here."""
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.registrations import mailer

from .models import (
    IntegrationActivationKey, IntegrationApiConfig, IntegrationAudit, IntegrationCredential, IntegrationCustomer, IntegrationIdempotency,
    IntegrationLicense, IntegrationProject, IntegrationRenewal, IntegrationSession, IntegrationSyncLog, IntegrationTallyInfo, IntegrationUsageEvent,
)

USAGE_EVENTS = ("APP_OPEN", "LOGIN", "HEARTBEAT", "LOGOUT", "DEVICE_ACTIVATION", "DEVICE_TRANSFER", "IDLE_START", "IDLE_END")
ACTIVITY_EVENTS = ("APP_OPEN", "LOGIN", "HEARTBEAT", "DEVICE_ACTIVATION")  # events that count as "the customer used the app"
SYNC_EVENTS = ("CUSTOMER_UPDATED", "EMAIL_CHANGED", "CONTACT_UPDATED", "CUSTOMER_INACTIVATED", "CUSTOMER_REACTIVATED", "LICENSE_STATUS_UPDATED")
MAX_LOGIN_FAILURES, LOCK_MINUTES = 5, 15
MAX_KEY_ATTEMPTS = 5
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


class IntegrationError(Exception):
    """Carries the stable machine-readable error code returned to the calling project."""

    def __init__(self, code, message, status=400, data=None):
        super().__init__(message)
        self.code, self.message, self.status, self.data = code, message, status, data or {}


# ------------------------------------------------------------------ small helpers
def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _hmac(value):
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def mask_email(address):
    user, _, domain = (address or "").partition("@")
    return f"{user[:2]}***@{domain}" if domain else "***"


def audit(project, action, subject="", ok=True, detail="", actor="", ip=""):
    try:
        IntegrationAudit.objects.create(project=project, action=action[:60], subject=str(subject)[:120], ok=ok, detail=str(detail)[:300], actor=actor[:80], ip=(ip or "")[:45])
    except Exception:  # an audit failure must never change the API outcome
        pass


def mark_step(project, step):
    """Records that the project's backend really called `step`; the integration turns ACTIVE only when all required steps were seen."""
    steps = project.steps
    if step in steps:
        return
    steps.append(step)
    project.verified_steps = json.dumps(steps)
    if project.status == IntegrationProject.SETUP_PENDING and all(s in steps for s in IntegrationProject.REQUIRED_STEPS):
        project.status = IntegrationProject.ACTIVE
    project.save(update_fields=["verified_steps", "status", "updated_at"])


# ------------------------------------------------------------------ credentials / projects
def issue_credential(project):
    """Retires any current credential of the project and returns (credential, secret). The secret is shown exactly once."""
    secret = "nxs_" + secrets.token_urlsafe(32)
    with transaction.atomic():
        IntegrationCredential.objects.filter(project=project, active=True).update(active=False, revoked_at=timezone.now())
        cred = IntegrationCredential.objects.create(
            project=project, client_id="nxc_" + secrets.token_hex(8), secret_hash=_sha(secret), secret_hint=secret[-4:])
    return cred, secret


# ------------------------------------------------------------------ developer credential handoff (one-time link + out-of-band code)
HANDOFF_SALT, HANDOFF_HOURS = "nexus.int.dev-credential-handoff", 24


def issue_handoff(project):
    """ONE short-lived, single-use, signed link bound to the project's CURRENT credential (and the tail of its secret hash, so it dies after use or
    after any rotation). No separate code. Nothing is stored; no secret is recovered or created here."""
    from django.core import signing
    cred = IntegrationCredential.objects.filter(project=project, active=True).first()
    if cred is None:
        raise IntegrationError("NO_CREDENTIALS", "This project has no active Client ID. Issue credentials first.", 409)
    token = signing.dumps({"c": cred.pk, "p": project.code, "h": cred.secret_hash[-16:]}, salt=HANDOFF_SALT)
    return cred, token


def redeem_handoff(token, ip=""):
    """The developer opens the link and confirms: Nexus generates a NEW secret for the SAME Client ID, shows it once, and the previous secret
    stops working. Expired, used, rotated, tampered or wrong-project links are refused."""
    from django.core import signing
    try:
        data = signing.loads(str(token or ""), salt=HANDOFF_SALT, max_age=HANDOFF_HOURS * 3600)
    except signing.SignatureExpired:
        raise IntegrationError("HANDOFF_EXPIRED", "This credential link has expired. Ask the Nexus administrator to send a new one.", 400)
    except signing.BadSignature:
        raise IntegrationError("HANDOFF_INVALID", "This credential link is invalid.", 400)
    with transaction.atomic():
        cred = IntegrationCredential.objects.select_for_update().select_related("project").filter(pk=data.get("c"), active=True).first()
        if cred is None or cred.project.code != data.get("p") or cred.secret_hash[-16:] != data.get("h"):
            raise IntegrationError("HANDOFF_USED", "This credential link was already used or the credentials were rotated. Ask for a new link.", 400)
        secret = "nxs_" + secrets.token_urlsafe(32)
        cred.secret_hash, cred.secret_hint = _sha(secret), secret[-4:]
        cred.save(update_fields=["secret_hash", "secret_hint"])
        audit(cred.project, "credentials.handoff_redeemed", cred.client_id, ip=ip)  # never the secret or the token
    return {"project_code": cred.project.code, "project_name": cred.project.name, "client_id": cred.client_id, "api_secret": secret,
            "note": "Shown once. Store it in your backend's secret store. The previous secret no longer works."}


def authenticate_client(client_id, secret):
    cred = IntegrationCredential.objects.select_related("project").filter(client_id=client_id or "", active=True).first()
    if not cred or not secret or not hmac.compare_digest(cred.secret_hash, _sha(secret)):
        return None
    return cred


PROJECT_FIELDS = (
    "name", "frontend_url", "backend_url", "auth_method", "framework", "health_path", "registration_method", "tally_serial_required",
    "serial_prefix", "serial_length", "activation_key_length", "device_limit", "default_license_type", "trial_days",
    "transfer_policy", "auto_sync", "usage_tracking", "welcome_email_mode", "activation_email_mode", "validity_years",
)


def validate_project_data(data, creating=True):
    errors, clean = {}, {}
    code = str(data.get("code", "")).strip().upper()
    auto = creating and not code  # the Add Project form never sends a code: the server allocates PREFIX001 atomically
    if auto:
        name = normalize_project_name(data.get("name", ""))
        if not name:
            errors["name"] = ["Project Name is required."]
        elif not re.fullmatch(r"[A-Z0-9][A-Z0-9 &.\-_/()]{1,119}", name):
            errors["name"] = ["Use 2-120 letters, digits, spaces and & . - _ / ( )."]
        elif IntegrationProject.objects.filter(name__iexact=name).exists():
            errors["name"] = ["A project with this name already exists."]
        clean["name"] = name
        email = str(data.get("developer_email", "")).strip()
        tally_on = str(data.get("tally_serial_required", "")).strip().lower() in ("true", "1", "yes", "on")
        tally_sl = str(data.get("category", "")).lower() == "tally"  # created from Tally SL Projects: API integration whether Tally Required is Yes or No
        if tally_on or tally_sl or email:  # Developer Email optional only for manual Non-Tally projects
            try:
                validate_email(email)
            except DjangoValidationError:
                errors["developer_email"] = ["Developer Email is invalid."]
        if tally_sl and not tally_on:
            clean["registration_method"] = "API_TALLY"  # Tally SL category, Tally verification not required; stays API-integrated, Setup Pending
        elif not tally_on:  # new Non-Tally projects: customers are managed manually in Nexus, no API integration required
            clean["registration_method"], clean["status"] = "MANUAL", IntegrationProject.ACTIVE
        clean["_developer_email"] = email
        clean["_auto_code"] = True
        cp = str(data.get("customer_path", "")).strip()
        if cp and not cp.startswith("/"):
            errors["customer_path"] = ["The endpoint must start with /"]
        clean["_customer_path"] = cp or "/customers"
    elif creating:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", code):
            errors["code"] = ["Project code must be 2-40 characters: capital letters, digits and underscore, starting with a letter."]
        elif IntegrationProject.objects.filter(code=code).exists():
            errors["code"] = ["A project with this code already exists."]
        clean["code"] = code
    for f in ("name", "frontend_url", "backend_url"):
        if f == "name" and auto:
            continue
        if f == "frontend_url" and not creating and "frontend_url" in data:  # optional for backend-only integrations
            clean[f] = str(data.get(f, "")).strip()
            continue
        if f in ("frontend_url", "backend_url") and auto:
            clean[f] = str(data.get(f, "")).strip()  # may stay empty until the API workspace (project is saved as Setup Pending)
            continue
        if f in data or creating:
            v = str(data.get(f, "")).strip()
            if not v:
                errors[f] = ["This field is required."]
            clean[f] = v
    for f in ("frontend_url", "backend_url"):
        if clean.get(f) and not re.match(r"^https?://[^\s/]+", clean[f]):
            errors[f] = ["Enter a valid backend API URL beginning with https://"]
    for f in PROJECT_FIELDS:
        if f in data and f not in clean:
            clean[f] = data[f]
    for f in ("tally_serial_required", "auto_sync", "usage_tracking"):
        if f in clean:  # a form posts "true"/"false" strings: never treat the text "false" as True
            clean[f] = clean[f].strip().lower() in ("true", "1", "yes", "on") if isinstance(clean[f], str) else bool(clean[f])
    for f, lo, hi in (("serial_length", 6, 18), ("activation_key_length", 6, 12), ("device_limit", 1, 20), ("trial_days", 1, 365), ("validity_years", 1, 10)):
        if f in clean:
            try:
                clean[f] = int(clean[f])
                if not lo <= clean[f] <= hi:
                    raise ValueError
            except (TypeError, ValueError):
                errors[f] = [f"Enter a number between {lo} and {hi}."]
    if "serial_prefix" in clean and not re.fullmatch(r"\d{1,3}", str(clean["serial_prefix"])):
        errors["serial_prefix"] = ["The serial prefix must be 1-3 digits."]
    if "serial_prefix" in clean and "serial_length" in clean and "serial_prefix" not in errors and "serial_length" not in errors:
        if len(str(clean["serial_prefix"])) >= clean["serial_length"]:
            errors["serial_prefix"] = ["The prefix must be shorter than the serial length."]
    if clean.get("default_license_type", "TRIAL") not in ("TRIAL", "ONE_YEAR", "LIFETIME"):
        errors["default_license_type"] = ["Choose Trial, 1 Year or Lifetime."]
    if clean.get("transfer_policy", "SELF") not in ("SELF", "DISABLED"):
        errors["transfer_policy"] = ["Choose Self transfer or Disabled."]
    rules = data.get("usage_rules")
    if rules is not None:
        try:
            nd, sd = int(rules["new_days"]), int(rules["speed_min_days"])
            if nd < 1 or not 1 <= sd <= 30:
                raise ValueError
            clean["usage_rules"] = json.dumps({"new_days": nd, "speed_min_days": sd})
        except (KeyError, TypeError, ValueError):
            errors["usage_rules"] = ["Provide new_days (>=1) and speed_min_days (1-30)."]
    return clean, errors


def normalize_project_name(raw):
    return re.sub(r"\s+", " ", str(raw or "")).strip().upper()


def code_prefix(name):
    """First meaningful alphabetic token of the name, at most 6 letters (GSTR2TALLY -> GSTR, MOTHYS INVENTORY -> MOTHYS, PMP RICE MILL -> PMP)."""
    for token in re.split(r"[^A-Za-z]+", str(name or "")):
        if token:
            return token.upper()[:6]
    return "PRJ"


def next_project_code(name):
    """The next free PREFIX### (preview; the authoritative value is allocated inside create_project). Continues past 999 as PREFIX1000."""
    prefix = code_prefix(name)
    used = [int(m.group(1)) for c in IntegrationProject.objects.filter(code__istartswith=prefix).values_list("code", flat=True)
            if (m := re.fullmatch(rf"{prefix}(\d{{3,}})", c.upper()))]
    return f"{prefix}{(max(used) + 1 if used else 1):03d}"


def project_config_defaults(developer_email, customer_path="/customers"):
    return {
        "customer_path": customer_path,
        "developer_email": developer_email,
        "field_mapping": json.dumps({"external_customer_id": "id", "customer_name": "name", "email": "email", "contact_number": "phone"}),
        "pagination": json.dumps({"type": "page", "page_param": "page", "size_param": "page_size", "page_size": 100, "results_path": "results"}),
    }


def create_project(clean, actor=""):
    if clean.pop("_auto_code", False):
        dev_email = clean.pop("_developer_email", "")
        cust_path = clean.pop("_customer_path", "/customers")
        for _ in range(8):  # unique constraint + retry = collision-free under concurrent creation
            clean["code"] = next_project_code(clean["name"])
            try:
                with transaction.atomic():
                    project, cred, secret = _create_project(clean, actor)
                    IntegrationApiConfig.objects.get_or_create(project=project, defaults=project_config_defaults(dev_email, cust_path))
                return project, cred, secret
            except IntegrityError:
                continue
        raise IntegrationError("CODE_ALLOCATION_FAILED", "Generated Project Code already exists; retry allocation.", 409)
    return _create_project(clean, actor)


def _create_project(clean, actor=""):
    clean.setdefault("license_policy", "CALENDAR")  # new Universal projects use exact calendar years; existing ones keep their rule
    clean.setdefault("trial_days", 3)
    with transaction.atomic():
        project = IntegrationProject.objects.create(**clean)
        cred, secret = issue_credential(project)
        audit(project, "project.created", project.code, actor=actor)
    return project, cred, secret


# ------------------------------------------------------------------ serial / passwords
def generate_serial(project):
    prefix, length = str(project.serial_prefix), project.serial_length
    for _ in range(50):
        serial = prefix + "".join(str(secrets.randbelow(10)) for _ in range(length - len(prefix)))
        if not IntegrationCustomer.objects.filter(serial_number=serial).exists():
            return serial
    raise IntegrationError("SERIAL_EXHAUSTED", "Could not allocate a free serial number.", 503)


def generate_password(length=10):
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def generate_activation_key(length):
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


# ------------------------------------------------------------------ licence state
def license_state(lic, now=None):
    now = now or timezone.now()
    if not lic.customer.active:
        return "INACTIVE"
    if lic.status == IntegrationLicense.PENDING:
        return "PENDING_APPROVAL"
    if lic.status == IntegrationLicense.SUSPENDED:
        return "SUSPENDED"
    if lic.status == IntegrationLicense.CANCELLED:
        return "CANCELLED"
    if lic.expires_at and lic.expires_at <= now:
        return "EXPIRED"
    return "ACTIVE"


def license_payload(lic):
    return {
        "license_id": lic.pk, "customer_id": lic.customer_id, "license_type": lic.license_type, "state": license_state(lic),
        "starts_at": lic.starts_at, "expires_at": lic.expires_at, "serial_number": lic.customer.serial_number, "user_id": lic.customer.user_id,
    }


def add_calendar_years(moment, years):
    """Exact calendar-year arithmetic. Leap-day rule: a licence starting on 29 February that ends in a non-leap year ends on 28 February."""
    try:
        return moment.replace(year=moment.year + years)
    except ValueError:  # 29 Feb -> non-leap year
        return moment.replace(year=moment.year + years, day=28)


def record_term(lic, action, previous, actor="", years=None):
    IntegrationRenewal.objects.create(license=lic, action=action, license_type=lic.license_type, years=years, previous_expires_at=previous,
                                      new_expires_at=lic.expires_at, actor=actor[:80])


def _years_expiry(lic, base, years=None):
    project = lic.customer.project
    if project.license_policy == "CALENDAR":
        return add_calendar_years(base, years or project.validity_years)
    return base + timedelta(days=365)  # LEGACY projects (Mothys): the existing rule, unchanged


def grant(lic, license_type, now=None, days=None, years=None, actor=""):
    now = now or timezone.now()
    previous = lic.expires_at
    lic.license_type, lic.status, lic.starts_at = license_type, IntegrationLicense.ACTIVE, now
    if license_type == IntegrationLicense.LIFETIME:
        lic.expires_at = None
    elif license_type == IntegrationLicense.TRIAL:
        lic.expires_at = now + timedelta(days=days or lic.customer.project.trial_days)
    elif license_type == "CUSTOM":  # new Universal projects only: an approved number of days from the start date
        lic.expires_at = now + timedelta(days=int(days))
    else:
        lic.expires_at = _years_expiry(lic, now, years)
    lic.save()
    record_term(lic, "GRANTED", previous, actor, years)


# ------------------------------------------------------------------ validation
def _clean_customer(data, require_all=True):
    errors, out = {}, {}
    for field, label in (("external_customer_id", "External customer id"), ("customer_name", "Customer name"), ("email", "Email"), ("contact_number", "Contact number")):
        if field not in data and not require_all:
            continue
        value = str(data.get(field) or "").strip()
        if not value:
            errors[field] = [f"{label} is required."]
        out[field] = value
    if out.get("email") and "email" not in errors:
        try:
            validate_email(out["email"])
            out["email"] = out["email"].lower()
        except DjangoValidationError:
            errors["email"] = ["Enter a valid email address."]
    if out.get("contact_number") and "contact_number" not in errors:
        digits = re.sub(r"\D", "", out["contact_number"])
        if not re.fullmatch(r"[+\d][\d\s\-()]*", out["contact_number"]) or not 7 <= len(digits) <= 15:
            errors["contact_number"] = ["Enter a valid contact number (7-15 digits)."]
    if out.get("customer_name") and not 2 <= len(out["customer_name"]) <= 150:
        errors["customer_name"] = ["Customer name must be 2-150 characters."]
    if out.get("external_customer_id") and len(out["external_customer_id"]) > 100:
        errors["external_customer_id"] = ["External customer id is too long."]
    if errors:
        raise IntegrationError("VALIDATION_ERROR", "Please correct the request fields.", 422, {"fields": errors})
    return out


# ------------------------------------------------------------------ welcome email
SHORT_NAMES = {"MOTHYS": "Mothys Inventory"}  # name used in customer e-mails
PLAN_LABEL = {"TRIAL": "Trial", "ONE_YEAR": "1 Year", "LIFETIME": "Lifetime", "CUSTOM": "Custom"}


def trial_days(lic):
    """Length of a Trial in days (start -> expiry, so an extension is included); None for other types."""
    if lic.license_type != IntegrationLicense.TRIAL or not lic.starts_at or not lic.expires_at:
        return None
    return max(round((lic.expires_at - lic.starts_at).total_seconds() / 86400), 1)


def _validity_text(lic):
    if lic.status == IntegrationLicense.PENDING:
        return "Pending approval – application access starts once Sai Infosys approves your licence"
    if lic.license_type == IntegrationLicense.LIFETIME:
        return "No expiry"
    when = timezone.localtime(lic.expires_at).strftime("%d/%m/%Y") if lic.expires_at else "—"
    days = trial_days(lic)
    return f"{days} days (expires {when})" if days else f"Expires {when}"


def send_welcome(customer, password):
    """Sends the credentials once. The password is only ever held in memory here and is never stored or logged."""
    project, lic = customer.project, customer.license
    name = SHORT_NAMES.get(project.code, project.name)
    plan, validity = PLAN_LABEL.get(lic.license_type, lic.license_type), _validity_text(lic)
    label = "Trial Days / Expiry Date" if lic.license_type == IntegrationLicense.TRIAL else "Expiry Date"
    rows = [("Serial Number", customer.serial_number), ("User ID", customer.user_id), ("Temporary Password", password), ("License Type", plan), (label, validity), ("Login", project.frontend_url)]
    text = (f"Dear {customer.name},\n\nYour {name} account has been created successfully.\n\n"
            f"Serial Number: {customer.serial_number}\n\nUser ID: {customer.user_id}\n\nTemporary Password: {password}\n\n"
            f"License Type: {plan}\n\n{label}: {validity}\n\nLogin:\n{project.frontend_url}\n\nRegards,\nSai Infosys")
    subject = f"{name} – Your Login Details"
    status = mailer.deliver(subject, text, customer.email, html=mailer.render_html(subject, f"Your {name} account has been created successfully.", rows),
                            kind="int_welcome", project=project.code, event_id=f"int-welcome-{customer.pk}-{secrets.token_hex(3)}")
    customer.welcome_email_status = status
    customer.save(update_fields=["welcome_email_status", "updated_at"])
    return status


def resend_welcome(customer, actor=""):
    """Used when the first welcome email failed or was lost: issues a NEW temporary password (the old one is not recoverable)."""
    password = generate_password()
    customer.password_hash = make_password(password)
    customer.must_change_password, customer.failed_logins, customer.locked_until = True, 0, None
    customer.save(update_fields=["password_hash", "must_change_password", "failed_logins", "locked_until", "updated_at"])
    status = send_welcome(customer, password)
    audit(customer.project, "customer.welcome_resent", customer.user_id, ok=status != "failed", actor=actor, detail=status)
    return status


# ------------------------------------------------------------------ idempotency
def idem_lookup(project, endpoint, key, payload):
    if not key:
        return None
    rec = IntegrationIdempotency.objects.filter(project=project, endpoint=endpoint, key=key).first()
    if not rec:
        return None
    if rec.request_hash != _sha(json.dumps(payload, sort_keys=True, default=str)):
        raise IntegrationError("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was already used with a different request.", 422)
    return rec.status_code, json.loads(rec.response)


# ------------------------------------------------------------------ registration
def register_customer(project, data, idem_key="", send_email=True, actor="", serial_override=None, tally_info=None):
    """Creates customer + licence + credentials atomically, then emails the credentials. Returns (http_status, body)."""
    c = _clean_customer(data)
    payload = {k: c[k] for k in sorted(c)}
    replay = idem_lookup(project, "register", idem_key, payload)
    if replay:
        return replay[0], {**replay[1], "idempotent_replay": True}
    if IntegrationCustomer.objects.filter(project=project, external_customer_id=c["external_customer_id"]).exists():
        existing = IntegrationCustomer.objects.select_related("license").get(project=project, external_customer_id=c["external_customer_id"])
        raise IntegrationError("CUSTOMER_ALREADY_REGISTERED", "This external customer id is already registered.", 409,
                               {"customer_id": existing.pk, "license_id": existing.license.pk, "serial_number": existing.serial_number})
    if IntegrationCustomer.objects.filter(project=project, email=c["email"]).exists():
        raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this project already uses this email address.", 409)
    if serial_override and IntegrationCustomer.objects.filter(serial_number=serial_override).exists():
        raise IntegrationError("DUPLICATE_TALLY_SERIAL", "This Tally Serial Number is already registered in Nexus.", 409)
    password = generate_password()
    for attempt in range(5):
        try:
            with transaction.atomic():
                customer = IntegrationCustomer.objects.create(
                    project=project, external_customer_id=c["external_customer_id"], name=c["customer_name"], email=c["email"],
                    contact_number=c["contact_number"], serial_number=(serial := serial_override or generate_serial(project)), user_id=serial,
                    password_hash=make_password(password), must_change_password=True)
                lic = IntegrationLicense(customer=customer, license_type=project.default_license_type, status=IntegrationLicense.PENDING)
                if project.default_license_type == IntegrationLicense.TRIAL:  # a trial needs no payment: usable at once
                    now = timezone.now()
                    lic.status, lic.starts_at, lic.expires_at = IntegrationLicense.ACTIVE, now, now + timedelta(days=project.trial_days)
                lic.save()  # paid types stay PENDING_APPROVAL until a Super Admin / verified entitlement approves them
                if tally_info:
                    IntegrationTallyInfo.objects.create(customer=customer, **tally_info)
                body = {
                    "status": "REGISTERED", "customer_id": customer.pk, "license_id": lic.pk, "serial_number": serial, "user_id": serial,
                    "license_type": lic.license_type, "license_state": license_state(lic), "expires_at": lic.expires_at,
                    "welcome_email": "queued" if send_email else "skipped",
                }
                if idem_key:
                    IntegrationIdempotency.objects.create(project=project, endpoint="register", key=idem_key,
                                                          request_hash=_sha(json.dumps(payload, sort_keys=True, default=str)),
                                                          status_code=201, response=json.dumps(body, default=str))
            break
        except IntegrityError:
            replay = idem_lookup(project, "register", idem_key, payload)
            if replay:
                return replay[0], {**replay[1], "idempotent_replay": True}
            if IntegrationCustomer.objects.filter(project=project, external_customer_id=c["external_customer_id"]).exists():
                raise IntegrationError("CUSTOMER_ALREADY_REGISTERED", "This external customer id is already registered.", 409)
            if IntegrationCustomer.objects.filter(project=project, email=c["email"]).exists():
                raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this project already uses this email address.", 409)
            if attempt == 4:
                raise IntegrationError("SERIAL_EXHAUSTED", "Could not allocate a free serial number.", 503)
    audit(project, "customer.registered", customer.user_id, actor=actor, detail=f"external={customer.external_customer_id}")
    if send_email:
        body["welcome_email"] = send_welcome(customer, password)
    del password
    return 201, body


def bulk_import(project, items, send_email=True, actor=""):
    results = []
    for i, item in enumerate(items):
        try:
            _, body = register_customer(project, item, idem_key="", send_email=send_email, actor=actor)
            results.append({"index": i, "external_customer_id": item.get("external_customer_id"), "ok": True, "serial_number": body["serial_number"]})
        except IntegrationError as exc:
            log_sync(project, "BULK_IMPORT", item.get("external_customer_id", ""), "FAILED", exc, {k: item.get(k) for k in ("external_customer_id", "customer_name", "email", "contact_number")})
            results.append({"index": i, "external_customer_id": item.get("external_customer_id"), "ok": False, "error": {"code": exc.code, "message": exc.message}})
    return results


# ------------------------------------------------------------------ sync
def log_sync(project, event, external_id, status, exc=None, payload=None):
    IntegrationSyncLog.objects.create(
        project=project, event=event[:30], external_customer_id=str(external_id or "")[:100], status=status,
        error_code=getattr(exc, "code", "")[:40] if exc else "", error=(getattr(exc, "message", str(exc)) if exc else "")[:300],
        payload=json.dumps(payload or {}, default=str))
    project.last_sync_status, project.last_sync_at = status, timezone.now()
    project.save(update_fields=["last_sync_status", "last_sync_at", "updated_at"])


def sync_customer(project, data, retry_of=None):
    external_id = str(data.get("external_customer_id") or "").strip()
    event = str(data.get("event") or "CUSTOMER_UPDATED").upper()
    sync_fields = {k: data[k] for k in ("customer_name", "email", "contact_number", "active", "license_status") if k in data}
    try:
        if event not in SYNC_EVENTS:
            raise IntegrationError("VALIDATION_ERROR", f"event must be one of {', '.join(SYNC_EVENTS)}.", 422)
        customer = IntegrationCustomer.objects.select_related("license").filter(project=project, external_customer_id=external_id).first()
        if not customer:
            raise IntegrationError("CUSTOMER_NOT_FOUND", "No customer is registered for this external customer id.", 404)
        with transaction.atomic():
            lic = IntegrationLicense.objects.select_for_update().get(pk=customer.license.pk)
            changed = []
            partial = {k: v for k, v in sync_fields.items() if k in ("customer_name", "email", "contact_number")}
            if partial:
                clean = _clean_customer({"external_customer_id": external_id, **partial}, require_all=False)
                if "customer_name" in clean:
                    customer.name = clean["customer_name"]
                if "email" in clean:
                    if IntegrationCustomer.objects.filter(project=project, email=clean["email"]).exclude(pk=customer.pk).exists():
                        raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this project already uses this email address.", 409)
                    customer.email = clean["email"]
                if "contact_number" in clean:
                    customer.contact_number = clean["contact_number"]
                changed += list(partial)
            if event == "CUSTOMER_INACTIVATED" or sync_fields.get("active") is False:
                customer.active = False
                changed.append("active")
                _revoke_all(lic, "customer_inactivated")
            elif event == "CUSTOMER_REACTIVATED" or sync_fields.get("active") is True:
                customer.active = True
                changed.append("active")
            if "license_status" in sync_fields:
                wanted = str(sync_fields["license_status"]).upper()
                if wanted == "SUSPENDED":
                    lic.status = IntegrationLicense.SUSPENDED
                    _revoke_all(lic, "license_suspended")
                elif wanted == "ACTIVE" and lic.status == IntegrationLicense.SUSPENDED:
                    lic.status = IntegrationLicense.ACTIVE
                else:  # a project can never grant or approve a licence through sync
                    raise IntegrationError("LICENSE_STATUS_NOT_ALLOWED", "Sync can only suspend a licence or lift a suspension.", 422)
                lic.save(update_fields=["status"])
                changed.append("license_status")
            customer.save()
        log_sync(project, event, external_id, "OK", payload=sync_fields)
        audit(project, f"sync.{event.lower()}", external_id, detail=",".join(changed))
        return {"status": "SYNCED", "customer_id": customer.pk, "changed": changed, "license_state": license_state(lic)}
    except IntegrationError as exc:
        if retry_of is None:
            log_sync(project, event, external_id, "FAILED", exc, {**sync_fields, "event": event})
        raise


def retry_sync(log):
    payload = json.loads(log.payload or "{}")
    payload.setdefault("event", log.event)
    if log.event == "BULK_IMPORT":
        _, _body = register_customer(log.project, payload, send_email=True)
        result = {"status": "SYNCED"}
    else:
        result = sync_customer(log.project, {"external_customer_id": log.external_customer_id, **payload}, retry_of=log.pk)
    log.status, log.retries, log.error, log.error_code = "RETRIED", log.retries + 1, "", ""
    log.save()
    return result


# ------------------------------------------------------------------ authentication
def _find_customer(project, user_id):
    customer = IntegrationCustomer.objects.select_related("license", "project").filter(project=project, user_id=str(user_id or "").strip()).first()
    return customer


def _verify(project, user_id, matches, ip=""):
    """Lock-out aware identity check. Identical error for an unknown user and a wrong secret."""
    customer = _find_customer(project, user_id)
    now = timezone.now()
    if customer and customer.locked_until and customer.locked_until > now:
        audit(project, "auth.locked", user_id, ok=False, ip=ip)
        raise IntegrationError("ACCOUNT_LOCKED", "Too many failed attempts. Try again later.", 423, {"retry_after_seconds": int((customer.locked_until - now).total_seconds())})
    if not customer or not matches(customer):
        if customer:
            customer.failed_logins += 1
            if customer.failed_logins >= MAX_LOGIN_FAILURES:
                customer.locked_until, customer.failed_logins = now + timedelta(minutes=LOCK_MINUTES), 0
            customer.save(update_fields=["failed_logins", "locked_until"])
        audit(project, "auth.failed", user_id, ok=False, ip=ip)
        raise IntegrationError("INVALID_CREDENTIALS", "The User ID or password is incorrect.", 401)
    if customer.failed_logins or customer.locked_until:
        customer.failed_logins, customer.locked_until = 0, None
        customer.save(update_fields=["failed_logins", "locked_until"])
    return customer


def verify_credentials(project, user_id, password, ip=""):
    """Checks User ID + password with lock-out."""
    return _verify(project, user_id, lambda c: bool(password) and check_password(str(password), c.password_hash), ip)


def verify_identity(project, data, ip=""):
    """Activate User: the customer proves who they are with the password OR with the e-mail address registered for the account
    (the key is then sent only to that address). A wrong e-mail counts as a failed attempt, exactly like a wrong password."""
    if data.get("password"):
        return verify_credentials(project, data.get("user_id"), data.get("password"), ip)
    email = str(data.get("email") or "").strip().lower()
    return _verify(project, data.get("user_id"), lambda c: bool(email) and c.email.lower() == email, ip)


def auth_verify(project, data, ip=""):
    customer = verify_credentials(project, data.get("user_id"), data.get("password"), ip)
    audit(project, "auth.verified", customer.user_id, ip=ip)
    state = license_state(customer.license)
    if state != "ACTIVE" and project.license_policy == "CALENDAR":
        # New Universal projects: an unambiguous denial (HTTP 403, no session, no device authorisation) - never a 200 that looks like a success.
        audit(project, "auth.license_denied", customer.user_id, ok=False, ip=ip, detail=state)
        raise IntegrationError(f"LICENSE_{state}", {"EXPIRED": "License Expired - renewal required.", "PENDING_APPROVAL": "The licence is awaiting approval."}.get(state, f"The licence is {state.lower().replace('_', ' ')}."), 403,
                               {"license_state": state, "authorized": False, "license": license_payload(customer.license)})
    device_id = str(data.get("device_id") or "").strip()
    mine = customer.license.sessions.filter(status="ACTIVE", device_id=device_id).first() if device_id else None
    return {
        # additive for every project: credentials are proven (`verified`) but protected use is allowed ONLY when `authorized` is true
        "authorized": state == "ACTIVE", "denial_reason": None if state == "ACTIVE" else state,
        # logging in again on the device that already holds the licence: no activation key and no transfer are needed
        "device_authorized": bool(mine), "session_id": mine.pk if mine else None,
        "verified": True, "customer_id": customer.pk, "external_customer_id": customer.external_customer_id, "user_id": customer.user_id,
        "name": customer.name, "email": customer.email, "must_change_password": customer.must_change_password,
        "license": license_payload(customer.license), "active_devices": customer.license.sessions.filter(status="ACTIVE").count(),
    }


def change_password(project, data):
    customer = verify_credentials(project, data.get("user_id"), data.get("current_password"))
    new = str(data.get("new_password") or "")
    if len(new) < 8:
        raise IntegrationError("VALIDATION_ERROR", "The new password must be at least 8 characters.", 422, {"fields": {"new_password": ["At least 8 characters."]}})
    if new == str(data.get("current_password")):
        raise IntegrationError("VALIDATION_ERROR", "Choose a password different from the current one.", 422)
    customer.password_hash, customer.must_change_password = make_password(new), False
    customer.save(update_fields=["password_hash", "must_change_password", "updated_at"])
    audit(project, "auth.password_changed", customer.user_id)
    return {"changed": True}


def validate_license(project, data):
    customer = _find_customer(project, data.get("user_id")) if data.get("user_id") else None
    if not customer and data.get("serial_number"):
        customer = IntegrationCustomer.objects.select_related("license").filter(project=project, serial_number=str(data["serial_number"]).strip()).first()
    if not customer and data.get("external_customer_id"):
        customer = IntegrationCustomer.objects.select_related("license").filter(project=project, external_customer_id=str(data["external_customer_id"])).first()
    if not customer:
        raise IntegrationError("CUSTOMER_NOT_FOUND", "No customer matches the supplied identifier.", 404)
    lic = customer.license
    payload = license_payload(lic)
    payload["valid"] = payload["state"] == "ACTIVE"
    payload["active_devices"] = lic.sessions.filter(status="ACTIVE").count()
    payload["device_limit"] = project.device_limit
    return payload


# ------------------------------------------------------------------ activation
def request_activation_key(project, data, ip=""):
    if project.license_policy == "CALENDAR":  # UNIVERSE: the activation key is internal and never issued to the customer; use devices/authorize
        raise IntegrationError("USE_DEVICE_AUTHORIZE", "This project authorises devices with devices/authorize; no activation key is issued.", 403)
    customer = verify_identity(project, data, ip)
    lic = customer.license
    if license_state(lic) not in ("ACTIVE",):
        raise IntegrationError("LICENSE_NOT_ACTIVE", f"The licence is {license_state(lic).lower().replace('_', ' ')}.", 403, {"license_state": license_state(lic)})
    now = timezone.now()
    limit = int(getattr(settings, "INTEGRATION_ACTIVATION_KEYS_PER_HOUR", 3))
    if IntegrationActivationKey.objects.filter(license=lic, created_at__gte=now - timedelta(hours=1)).count() >= limit:
        raise IntegrationError("RATE_LIMITED", "Too many activation keys were requested. Try again later.", 429)
    key = generate_activation_key(project.activation_key_length)
    ttl = int(getattr(settings, "INTEGRATION_ACTIVATION_KEY_MINUTES", 15))
    with transaction.atomic():
        IntegrationActivationKey.objects.filter(license=lic, used_at__isnull=True).update(used_at=now)  # an older unused key can no longer be used
        IntegrationActivationKey.objects.create(license=lic, key_hash=_hmac(f"{lic.pk}:{key}"), expires_at=now + timedelta(minutes=ttl))
    subject = f"{project.name} – Activation Key"
    text = (f"Dear {customer.name},\n\nYour activation key for {project.name} is: {key}\n\nIt is valid for {ttl} minutes and can be used once. "
            "If you did not request it, ignore this email.\n\nRegards,\nSai Infosys\nConnect. Simplify. Succeed.")
    status = mailer.deliver(subject, text, customer.email, html=mailer.render_html(subject, "Your one-time activation key.", [("Activation Key", key), ("Valid for", f"{ttl} minutes")]),
                            kind="int_activation", project=project.code, event_id=f"int-key-{lic.pk}-{secrets.token_hex(3)}")
    audit(project, "activation.requested", customer.user_id, ok=status != "failed", ip=ip, detail=status)
    if status == "failed":
        raise IntegrationError("EMAIL_FAILED", "The activation key could not be emailed. Please try again.", 502)
    return {"sent_to": mask_email(customer.email), "expires_in_seconds": ttl * 60, "email_status": status}


def _new_session(lic, device_id, device_name, project, reason=""):
    token = "nxt_" + secrets.token_urlsafe(32)
    session = IntegrationSession.objects.create(license=lic, token_hash=_sha(token), device_id=device_id, device_name=device_name, last_seen_at=timezone.now())
    return token, session


def _revoke_all(lic, reason, except_session=None):
    qs = lic.sessions.filter(status=IntegrationSession.ACTIVE)
    if except_session is not None:
        qs = qs.exclude(pk=except_session.pk)
    return qs.update(status=IntegrationSession.REVOKED, revoked_reason=reason, revoked_at=timezone.now())


def _record(project, lic, event, session=None, device_id=""):
    if project.usage_tracking:
        IntegrationUsageEvent.objects.create(project=project, license=lic, session=session, device_id=device_id, event_type=event,
                                             occurred_at=timezone.now(), event_id=secrets.token_hex(16))


def _session_body(token, session, lic):
    return {"session_token": token, "session_id": session.pk, "device_id": session.device_id, "license": license_payload(lic),
            "authorization": "ACTIVE", "revocation": "Server-side revocation is immediate; an offline client only learns of it when it next reconnects."}


def verify_activation(project, data, ip=""):
    device_id = str(data.get("device_id") or "").strip()
    device_name = str(data.get("device_name") or "").strip()[:150]
    if not device_id or len(device_id) > 120:
        raise IntegrationError("VALIDATION_ERROR", "device_id is required.", 422, {"fields": {"device_id": ["Required (max 120 characters)."]}})
    customer = verify_identity(project, data, ip)
    raw = re.sub(r"\D", "", str(data.get("activation_key") or ""))
    now, error, result = timezone.now(), None, None
    with transaction.atomic():
        lic = IntegrationLicense.objects.select_for_update().select_related("customer").get(pk=customer.license.pk)
        if license_state(lic) != "ACTIVE":
            raise IntegrationError("LICENSE_NOT_ACTIVE", f"The licence is {license_state(lic).lower().replace('_', ' ')}.", 403, {"license_state": license_state(lic)})
        key = IntegrationActivationKey.objects.select_for_update().filter(license=lic, used_at__isnull=True, expires_at__gt=now).order_by("-id").first()
        if not key or key.attempts >= MAX_KEY_ATTEMPTS:
            error = IntegrationError("ACTIVATION_KEY_INVALID", "The activation key is invalid or has expired. Request a new one.", 400)
        elif len(raw) != project.activation_key_length or not hmac.compare_digest(key.key_hash, _hmac(f"{lic.pk}:{raw}")):
            key.attempts += 1
            key.save(update_fields=["attempts"])
            error = IntegrationError("ACTIVATION_KEY_INVALID", "The activation key is invalid or has expired. Request a new one.", 400)
        else:
            others = lic.sessions.filter(status="ACTIVE").exclude(device_id=device_id)
            if others.count() >= project.device_limit:
                if project.transfer_policy == "DISABLED":
                    error = IntegrationError("TRANSFER_DISABLED", "This licence is active on another device and transfer is disabled for this project.", 403)
                else:  # all credentials verified: ask the customer to confirm instead of silently taking over
                    transfer_token = "nxx_" + secrets.token_urlsafe(24)
                    key.transfer_hash, key.pending_device_id, key.pending_device_name = _sha(transfer_token), device_id, device_name
                    key.pending_expires_at = now + timedelta(minutes=10)
                    key.save()
                    other = others.first()
                    error = IntegrationError("TRANSFER_CONFIRMATION_REQUIRED", "License Already Active on Another Device", 409, {
                        "title": "License Already Active on Another Device",
                        "message": "Your previous device session will be terminated and this device will become active. Do you want to continue?",
                        "actions": [{"id": "cancel", "label": "Cancel"}, {"id": "confirm", "label": "OK – Activate This Device"}],
                        "transfer_token": transfer_token, "expires_in_seconds": 600, "active_device_name": other.device_name})
            else:
                lic.sessions.filter(status="ACTIVE", device_id=device_id).update(  # the same device activating again replaces its own session
                    status=IntegrationSession.REVOKED, revoked_reason="reactivated_same_device", revoked_at=now)
                token, session = _new_session(lic, device_id, device_name, project)
                key.used_at = now
                key.save(update_fields=["used_at"])
                _record(project, lic, "DEVICE_ACTIVATION", session, device_id)
                result = _session_body(token, session, lic)
    if error:
        audit(project, "activation.verify", customer.user_id, ok=False, ip=ip, detail=error.code)
        raise error
    audit(project, "activation.verified", customer.user_id, ip=ip, detail=f"device={device_id}")
    mark_step(project, "activation_verify")
    return result


def confirm_transfer(project, data, ip=""):
    token = str(data.get("transfer_token") or "")
    confirm = data.get("confirm")
    if confirm not in (True, False):
        raise IntegrationError("VALIDATION_ERROR", "confirm must be true (OK – Activate This Device) or false (Cancel).", 422)
    customer = _find_customer(project, data.get("user_id"))
    key = IntegrationActivationKey.objects.filter(license=customer.license, transfer_hash=_sha(token)).first() if customer and token else None
    if not key:
        raise IntegrationError("INVALID_TRANSFER_TOKEN", "The transfer request is invalid or has already been handled.", 400)
    now = timezone.now()
    with transaction.atomic():
        lic = IntegrationLicense.objects.select_for_update().select_related("customer").get(pk=customer.license.pk)
        key = IntegrationActivationKey.objects.select_for_update().get(pk=key.pk)
        if key.used_at:  # already confirmed: idempotent, the new session is simply reported (its token was returned once)
            existing = lic.sessions.filter(status="ACTIVE", device_id=key.pending_device_id).first()
            if existing and confirm:
                return {"status": "ALREADY_TRANSFERRED", "transferred": True, "session_id": existing.pk, "device_id": existing.device_id, "authorization": "ACTIVE"}
            raise IntegrationError("INVALID_TRANSFER_TOKEN", "The transfer request is invalid or has already been handled.", 400)
        if not key.pending_expires_at or key.pending_expires_at <= now:
            raise IntegrationError("TRANSFER_EXPIRED", "The transfer request expired. Start activation again.", 410)
        if not confirm:
            key.transfer_hash, key.pending_device_id = "", ""
            key.save()
            audit(project, "transfer.cancelled", customer.user_id, ip=ip)
            return {"status": "CANCELLED", "transferred": False, "previous_device_still_authorized": True}
        if license_state(lic) != "ACTIVE":
            raise IntegrationError("LICENSE_NOT_ACTIVE", "The licence is no longer active.", 403)
        previous = list(lic.sessions.filter(status="ACTIVE"))
        _revoke_all(lic, "device_transfer")
        raw_token, session = _new_session(lic, key.pending_device_id, key.pending_device_name, project)
        key.used_at = now
        key.save(update_fields=["used_at"])
        _record(project, lic, "DEVICE_TRANSFER", session, session.device_id)
        body = _session_body(raw_token, session, lic)
        body.update(status="TRANSFERRED", transferred=True, revoked_sessions=[s.pk for s in previous])
    audit(project, "transfer.confirmed", customer.user_id, ip=ip,
          detail=f"from={','.join(p.device_name or p.device_id for p in previous)[:150]};to={(key.pending_device_name or key.pending_device_id)[:100]}")
    return body


# ------------------------------------------------------------------ sessions
def _session_for(project, token):
    return IntegrationSession.objects.select_related("license__customer").filter(token_hash=_sha(str(token or "")), license__customer__project=project).first()


def introspect(project, data):
    session = _session_for(project, data.get("session_token"))
    if not session:
        return {"active": False, "reason": "unknown_session"}
    lic = session.license
    state = license_state(lic)
    if session.status != IntegrationSession.ACTIVE:
        return {"active": False, "reason": session.revoked_reason or "revoked", "revoked_at": session.revoked_at}
    if state != "ACTIVE":
        return {"active": False, "reason": f"license_{state.lower()}", "license_state": state}
    session.last_seen_at = timezone.now()
    session.save(update_fields=["last_seen_at"])
    mark_step(project, "session_introspect")
    return {"active": True, "session_id": session.pk, "device_id": session.device_id, "user_id": lic.customer.user_id, "customer_id": lic.customer_id,
            "license_state": state, "expires_at": lic.expires_at}


def logout(project, data):
    session = _session_for(project, data.get("session_token"))
    if not session:
        raise IntegrationError("SESSION_NOT_FOUND", "Unknown session.", 404)
    if session.status == IntegrationSession.ACTIVE:
        session.status, session.revoked_reason, session.revoked_at = IntegrationSession.REVOKED, "logout", timezone.now()
        session.save(update_fields=["status", "revoked_reason", "revoked_at"])
        _record(project, session.license, "LOGOUT", session, session.device_id)
        audit(project, "session.logout", session.license.customer.user_id)
    return {"revoked": True, "session_id": session.pk}


# ------------------------------------------------------------------ usage
def record_usage(project, data):
    if not project.usage_tracking:
        raise IntegrationError("USAGE_TRACKING_DISABLED", "Usage tracking is disabled for this project.", 403)
    events = data.get("events") if isinstance(data.get("events"), list) else [data]
    if not 1 <= len(events) <= 100:
        raise IntegrationError("VALIDATION_ERROR", "Send 1-100 events.", 422)
    recorded = 0
    for ev in events:
        etype = str(ev.get("event_type") or "").upper()
        if etype not in USAGE_EVENTS:
            raise IntegrationError("VALIDATION_ERROR", f"event_type must be one of {', '.join(USAGE_EVENTS)}.", 422)
        session = _session_for(project, ev.get("session_token")) if ev.get("session_token") else None
        customer = session.license.customer if session else _find_customer(project, ev.get("user_id"))
        if not customer:
            raise IntegrationError("CUSTOMER_NOT_FOUND", "Identify the customer with session_token or user_id.", 404)
        when = timezone.now()
        if ev.get("occurred_at"):
            from django.utils.dateparse import parse_datetime
            parsed = parse_datetime(str(ev["occurred_at"]))
            if not parsed:
                raise IntegrationError("VALIDATION_ERROR", "occurred_at must be an ISO-8601 timestamp.", 422)
            when = parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
            if when > timezone.now() + timedelta(minutes=5):
                when = timezone.now()
        event_id = str(ev.get("event_id") or "")[:100] or secrets.token_hex(16)
        if IntegrationUsageEvent.objects.filter(project=project, event_id=event_id).exists():
            continue  # a retry of an event already stored
        try:
            with transaction.atomic():
                IntegrationUsageEvent.objects.create(project=project, license=customer.license, session=session, device_id=str(ev.get("device_id") or (session.device_id if session else ""))[:120],
                                                     event_type=etype, occurred_at=when, event_id=event_id)
            recorded += 1
        except IntegrityError:
            continue
    mark_step(project, "usage_event")
    return {"recorded": recorded, "duplicates": len(events) - recorded}


# ------------------------------------------------------------------ admin actions
def approve_license(lic, license_type=None, actor=""):
    if lic.status != IntegrationLicense.PENDING:
        raise IntegrationError("INVALID_STATE", "Only a licence pending approval can be approved.", 409)
    grant(lic, license_type or lic.license_type)
    audit(lic.customer.project, "license.approved", lic.customer.user_id, actor=actor, detail=lic.license_type)
    return lic


# ------------------------------------------------------------------ staff licence management (Mothys Customers page)
def _lic_locked(lic):
    return IntegrationLicense.objects.select_for_update().select_related("customer__project").get(pk=lic.pk)


def extend_trial(lic, days, actor=""):
    try:
        days = int(days)
        if not 1 <= days <= 365:
            raise ValueError
    except (TypeError, ValueError):
        raise IntegrationError("VALIDATION_ERROR", "Enter 1-365 days.", 422, {"fields": {"days": ["Enter 1-365 days."]}})
    with transaction.atomic():
        lic = _lic_locked(lic)
        if lic.license_type != IntegrationLicense.TRIAL or lic.status not in (IntegrationLicense.ACTIVE,):
            raise IntegrationError("INVALID_STATE", "Only an active (or expired) Trial can be extended.", 409)
        now = timezone.now()
        lic.expires_at = max(lic.expires_at or now, now) + timedelta(days=days)
        lic.save(update_fields=["expires_at"])
    audit(lic.customer.project, "license.trial_extended", lic.customer.user_id, actor=actor, detail=f"+{days}d")
    return lic


def cancel_trial(lic, actor=""):
    with transaction.atomic():
        lic = _lic_locked(lic)
        if lic.license_type != IntegrationLicense.TRIAL or lic.status != IntegrationLicense.ACTIVE:
            raise IntegrationError("INVALID_STATE", "Only an active Trial can be cancelled.", 409)
        lic.status = IntegrationLicense.CANCELLED
        lic.save(update_fields=["status"])
        _revoke_all(lic, "trial_cancelled")
    audit(lic.customer.project, "license.trial_cancelled", lic.customer.user_id, actor=actor)
    return lic


def renew_license(lic, actor=""):
    with transaction.atomic():
        lic = _lic_locked(lic)
        if lic.license_type != IntegrationLicense.ONE_YEAR or lic.status != IntegrationLicense.ACTIVE:
            raise IntegrationError("INVALID_STATE", "Only an Annual licence can be renewed.", 409)
        now = timezone.now()
        previous = lic.expires_at
        lic.expires_at = _years_expiry(lic, max(lic.expires_at or now, now))
        lic.save(update_fields=["expires_at"])
        record_term(lic, "RENEWED", previous, actor, lic.customer.project.validity_years if lic.customer.project.license_policy == "CALENDAR" else 1)
    audit(lic.customer.project, "license.renewed", lic.customer.user_id, actor=actor)
    return lic


def set_license_type(lic, license_type, trial_days=None, actor="", years=None, custom_days=None, allow_custom=False):
    """allow_custom: UNIVERSE projects only (Trial | Lifetime | Yearly | Custom days). Legacy callers keep Trial / Annual / Lifetime."""
    allowed = (IntegrationLicense.TRIAL, IntegrationLicense.ONE_YEAR, IntegrationLicense.LIFETIME) + (("CUSTOM",) if allow_custom else ())
    if license_type not in allowed:
        raise IntegrationError("VALIDATION_ERROR", "Choose Trial, Lifetime, Yearly or Custom (Days)." if allow_custom else "Choose Trial, Annual or Lifetime.", 422)

    def _pos(v, name, top):
        try:
            n = int(str(v).strip())
            if not 1 <= n <= top:
                raise ValueError
            return n
        except (TypeError, ValueError):
            raise IntegrationError("VALIDATION_ERROR", f"{name} must be a whole number from 1 to {top}.", 422)
    if license_type == "CUSTOM":
        trial_days = _pos(custom_days, "Number of days", 3650)
    elif license_type == IntegrationLicense.TRIAL and trial_days not in (None, ""):
        trial_days = _pos(trial_days, "Trial days", 3650)
    elif license_type == IntegrationLicense.ONE_YEAR and years not in (None, ""):
        years = _pos(years, "Number of years", 10)
    with transaction.atomic():
        lic = _lic_locked(lic)
        if lic.customer.active is False:
            raise IntegrationError("INVALID_STATE", "Reactivate the customer first.", 409)
        grant(lic, license_type, days=int(trial_days) if trial_days else None, years=int(years) if years else None, actor=actor)
    audit(lic.customer.project, "license.type_set", lic.customer.user_id, actor=actor, detail=license_type)
    return lic


def set_customer_active(customer, active, actor=""):
    with transaction.atomic():
        customer.active = bool(active)
        customer.save(update_fields=["active", "updated_at"])
        if not active:
            _revoke_all(customer.license, "customer_inactivated")
    audit(customer.project, "customer.reactivated" if active else "customer.inactivated", customer.user_id, actor=actor)
    return customer


def edit_customer(customer, data, actor=""):
    clean = _clean_customer({"external_customer_id": customer.external_customer_id, **{k: data[k] for k in ("customer_name", "email", "contact_number") if k in data}}, require_all=False)
    if "email" in clean and IntegrationCustomer.objects.filter(project=customer.project, email=clean["email"]).exclude(pk=customer.pk).exists():
        raise IntegrationError("DUPLICATE_EMAIL", "Another customer of this product already uses this email address.", 409)
    for src, dst in (("customer_name", "name"), ("email", "email"), ("contact_number", "contact_number")):
        if src in clean:
            setattr(customer, dst, clean[src])
    customer.save()
    audit(customer.project, "customer.edited", customer.user_id, actor=actor)
    return customer
