"""Business rules for Tally SL licensing. Every state change runs in a transaction and writes an audit entry."""
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q
from django.utils import timezone

from .models import (
    TallyAuditLog, TallyCustomer, TallyDevice, TallyDeviceTransfer, TallyInfo, TallyLicense, TallyPasswordReset,
    TallySession, TallyUsageEvent, TallyVerificationCode,
)

IST = ZoneInfo("Asia/Kolkata")
SERIAL_RE = re.compile(r"[A-Za-z0-9]{5,30}")
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PWD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
TRANSFER_WARNING = (
    "This license is currently active on another device. Continuing will sign out the previous device and activate this device."
)


# Public "License Plan" vocabulary. Stored values never change (TRIAL / LICENSED / LIFETIME); ANNUAL is the API name of LICENSED.
PLAN_OF = {"TRIAL": "TRIAL", "LICENSED": "ANNUAL", "LIFETIME": "LIFETIME"}
STORED_OF = {"TRIAL": "TRIAL", "ANNUAL": "LICENSED", "LICENSED": "LICENSED", "LIFETIME": "LIFETIME"}


def plan_of(lic):
    return PLAN_OF[lic.license_type]


class TallyError(Exception):
    """A rule failure with the HTTP status and a stable machine-readable `code`."""

    def __init__(self, message, status=400, code="bad_request", errors=None):
        super().__init__(message)
        self.status, self.code, self.errors = status, code, errors or {}


def conf(name, default):
    return getattr(settings, name, default)


# ------------------------------------------------------------------ hashing helpers
def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _keyed(scope, value):
    key = (conf("DEVICE_EVIDENCE_KEY", "") or settings.SECRET_KEY).encode()
    return hmac.new(key, f"tallysl|{scope}|{value}".encode(), hashlib.sha256).hexdigest()


def normalize_key(raw):
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def new_activation_key():
    while True:
        raw = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(9))
        if any(c.isalpha() for c in raw) and any(c.isdigit() for c in raw):
            return raw


def temp_password(length=10):
    return "".join(secrets.choice(_PWD_ALPHABET) for _ in range(length))


def client_ip(request):
    """The caller's address. Behind the TLS proxy (DJANGO_BEHIND_PROXY, which sets USE_X_FORWARDED_HOST) the real client is the
    RIGHT-most X-Forwarded-For entry (the one the trusted proxy appended); a client-supplied left part is never trusted.
    Direct connections use REMOTE_ADDR."""
    if request is None:
        return ""
    meta = request.META
    if getattr(settings, "USE_X_FORWARDED_HOST", False) and meta.get("HTTP_X_FORWARDED_FOR"):
        return meta["HTTP_X_FORWARDED_FOR"].split(",")[-1].strip()[:45]
    return (meta.get("REMOTE_ADDR") or "")[:45]


# ------------------------------------------------------------------ audit
def audit(action, detail="", *, customer=None, product=None, actor="system", ip=""):
    TallyAuditLog.objects.create(
        product=product or getattr(customer, "product", None), customer=customer, action=action,
        detail=detail[:300], actor=(actor or "system")[:150], ip_address=ip,
    )


def actor_name(user):
    return getattr(user, "username", "") or "system"


# ------------------------------------------------------------------ licence state
def license_state(lic, now=None):
    """pending | active | expired | cancelled | inactive"""
    now = now or timezone.now()
    if lic.status == TallyLicense.PENDING:
        return "pending"
    if lic.status == TallyLicense.INACTIVE:
        return "inactive"
    if lic.status == TallyLicense.TRIAL_CANCELLED:
        return "cancelled"
    if lic.license_type != TallyLicense.LIFETIME and lic.expires_at and lic.expires_at <= now:
        return "expired"
    return "active"


def active_device(customer):
    return customer.devices.filter(revoked_at__isnull=True).first()


def _matches(device, client_device_id, fingerprint):
    return bool(
        device and client_device_id and fingerprint
        and device.client_device_id == client_device_id
        and hmac.compare_digest(device.fingerprint_hash, _keyed("fp", fingerprint))
    )


def license_payload(customer, device=None, now=None):
    """What the client needs on every licence check. `authorized` needs an active licence AND that `device` is the
    customer's current enrolled device. `lease_until` bounds how long an OFFLINE client may rely on the answer."""
    now = now or timezone.now()
    lic = customer.license
    state = license_state(lic, now)
    current = active_device(customer)
    reason = state
    authorized = state == "active"
    if authorized:
        if current is None:
            authorized, reason = False, "device_not_enrolled"
        elif device is None or device.pk != current.pk:
            authorized, reason = False, "device_not_authorized"
    lease_until = None
    if authorized:
        lease_until = now + timedelta(days=conf("TALLY_LEASE_DAYS", conf("LICENSE_LEASE_DAYS", 7)))
        if lic.license_type != TallyLicense.LIFETIME and lic.expires_at and lic.expires_at < lease_until:
            lease_until = lic.expires_at
    return {
        "product": customer.product.code,
        "tally_serial": customer.tally_serial,
        "authorized": authorized,
        "state": reason,
        "license_plan": plan_of(lic),
        "license_type": lic.license_type,
        "license_status": lic.status,
        "activated_at": lic.activated_at,
        "expires_at": None if lic.license_type == TallyLicense.LIFETIME else lic.expires_at,
        "must_change_password": customer.must_change_password,
        "checked_at": now,
        "lease_until": lease_until,
    }


# ------------------------------------------------------------------ registration
def check_password_rule(raw):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 128:
        raise TallyError("Password is required.", 400, "weak_password", {"password": ["Password is required."]})


@transaction.atomic
def register_customer(product, data, *, actor="customer", ip=""):
    """Creates the customer + a PENDING licence (no access until a Superadmin activates it). Returns (customer, temp_password)."""
    serial = data["tally_serial"].strip()
    if not SERIAL_RE.fullmatch(serial):
        raise TallyError("Enter a valid Tally Serial Number.", 400, "invalid_serial", {"tally_serial": ["Enter a valid Tally Serial Number."]})
    if TallyCustomer.objects.filter(product=product, tally_serial__iexact=serial).exists():
        raise TallyError("An account already exists for this Tally Serial Number.", 409, "account_exists")
    raw = temp_password()
    try:
        with transaction.atomic():
            customer = TallyCustomer.objects.create(
                product=product, tally_serial=serial, name=data["name"].strip(), email=data["email"].strip().lower(),
                contact_number=data["contact_number"].strip(), password_hash=make_password(raw), must_change_password=True,
            )
    except IntegrityError:
        raise TallyError("An account already exists for this Tally Serial Number.", 409, "account_exists")
    TallyLicense.objects.create(customer=customer, license_type=TallyLicense.TRIAL, status=TallyLicense.PENDING)
    TallyInfo.objects.create(customer=customer)
    audit("REGISTERED", f"{customer.name} <{customer.email}>", customer=customer, actor=actor, ip=ip)
    return customer, raw


# ------------------------------------------------------------------ passwords / login
def find_customer(product, user_id):
    return TallyCustomer.objects.select_related("product", "license").filter(product=product, tally_serial__iexact=(user_id or "").strip()).first()


def _register_failure(customer):
    TallyCustomer.objects.filter(pk=customer.pk).update(failed_logins=F("failed_logins") + 1)
    customer.refresh_from_db(fields=["failed_logins"])
    if customer.failed_logins >= conf("CUSTOMER_MAX_FAILED_LOGINS", 5):
        until = timezone.now() + timedelta(minutes=conf("CUSTOMER_LOCKOUT_MINUTES", 15))
        TallyCustomer.objects.filter(pk=customer.pk).update(failed_logins=0, locked_until=until)
        audit("LOCKED", f"until {until:%H:%M}", customer=customer)


def authenticate(product, user_id, password):
    customer = find_customer(product, user_id)
    if customer and customer.locked_until and customer.locked_until > timezone.now():
        raise TallyError("Too many failed attempts. Try again later or reset the password.", 429, "account_locked")
    if customer is None or not check_password(password or "", customer.password_hash):
        if customer:
            _register_failure(customer)
        raise TallyError("Invalid User ID or password.", 401, "invalid_credentials")
    if customer.failed_logins or customer.locked_until:
        TallyCustomer.objects.filter(pk=customer.pk).update(failed_logins=0, locked_until=None)
    return customer


def _set_password(customer, raw):
    check_password_rule(raw)
    customer.password_hash = make_password(raw)
    customer.must_change_password = False
    customer.password_changed_at = timezone.now()
    customer.failed_logins, customer.locked_until = 0, None
    customer.save(update_fields=["password_hash", "must_change_password", "password_changed_at", "failed_logins", "locked_until", "updated_at"])
    # A new password signs out every session (other laptops, stolen tokens). Callers issue a fresh one if needed.
    customer.sessions.filter(revoked_at__isnull=True).update(revoked_at=timezone.now(), revoked_reason="password_changed")


@transaction.atomic
def change_password(customer, current, new):
    if not check_password(current or "", customer.password_hash):
        raise TallyError("Current password is incorrect.", 400, "wrong_password")
    if check_password(new or "", customer.password_hash):
        raise TallyError("Choose a password different from the current one.", 400, "same_password")
    _set_password(customer, new)
    audit("PASSWORD_CHANGED", customer=customer, actor="customer")


def request_password_reset(product, identifier):
    """Always silent about whether the account exists. Returns (customer, raw_token) when a mail should go out, else None."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    customer = TallyCustomer.objects.select_related("product").filter(product=product).filter(
        Q(email__iexact=identifier) | Q(tally_serial__iexact=identifier)
    ).first()
    if customer is None:
        return None
    recent = customer.password_resets.filter(created_at__gte=timezone.now() - timedelta(seconds=60)).exists()
    if recent:
        return None
    with transaction.atomic():
        customer.password_resets.filter(used_at__isnull=True).update(used_at=timezone.now())  # a newer link kills older ones
        raw = secrets.token_urlsafe(32)
        TallyPasswordReset.objects.create(
            customer=customer, token_hash=_digest(raw), expires_at=timezone.now() + timedelta(seconds=conf("PASSWORD_RESET_MAX_AGE", 3600))
        )
        audit("PASSWORD_RESET_REQUESTED", customer=customer, actor="customer")
    return customer, raw


@transaction.atomic
def reset_password(product, token, new):
    check_password_rule(new)
    row = TallyPasswordReset.objects.select_for_update().select_related("customer__product").filter(
        token_hash=_digest(token or ""), customer__product=product
    ).first()
    if row is None or row.used_at or row.expires_at <= timezone.now():
        raise TallyError("This reset link is invalid or has expired.", 400, "invalid_token")
    row.used_at = timezone.now()
    row.save(update_fields=["used_at"])
    _set_password(row.customer, new)
    audit("PASSWORD_RESET", customer=row.customer, actor="customer")
    return row.customer


# ------------------------------------------------------------------ sessions
def issue_session(customer, client_device_id="", device=None):
    raw = secrets.token_urlsafe(32)
    TallySession.objects.create(
        customer=customer, device=device, client_device_id=(client_device_id or "")[:64], token_hash=_digest(raw),
        expires_at=timezone.now() + timedelta(seconds=conf("TALLY_SESSION_MAX_AGE", 7 * 86400)),
    )
    return raw


def session_from_token(product, raw):
    session = TallySession.objects.select_related("customer__product", "customer__license", "device").filter(
        token_hash=_digest(raw or "")
    ).first()
    if session is None or session.customer.product_id != product.pk:
        raise TallyError("Invalid or expired session.", 401, "invalid_token")
    if session.revoked_at:
        reason = session.revoked_reason
        msg = {
            "device_replaced": "This device was signed out because the license was activated on another device.",
            "device_revoked": "This device's authorization was revoked. Contact Sai Infosys.",
            "password_changed": "Your password was changed. Please sign in again.",
        }.get(reason, "This session has ended. Please sign in again.")
        raise TallyError(msg, 401, "session_revoked", {"reason": [reason]})
    if session.expires_at <= timezone.now():
        raise TallyError("Invalid or expired session.", 401, "invalid_token")
    return session


def session_device(session):
    """The enrolled device this session belongs to, or None (not enrolled / replaced / revoked)."""
    device = session.device
    if device is not None and device.revoked_at is None:
        return device
    return None


def refresh_session(session):
    """Rotates a still-valid session token (short-lived tokens): the old one is revoked, a new one issued for the same device."""
    session.revoked_at, session.revoked_reason = timezone.now(), "refreshed"
    session.save(update_fields=["revoked_at", "revoked_reason"])
    return issue_session(session.customer, session.client_device_id, session.device)


def login(product, user_id, password, device_info, ip=""):
    customer = authenticate(product, user_id, password)
    info = device_info or {}
    current = active_device(customer)
    device = current if _matches(current, info.get("client_device_id"), info.get("fingerprint")) else None
    token = issue_session(customer, info.get("client_device_id", ""), device)
    audit("LOGIN", "device matched" if device else "no enrolled device match", customer=customer, actor="customer", ip=ip)
    payload = license_payload(customer, device)
    if payload["authorized"] and not customer.must_change_password:
        record_open(customer, device, "login", info.get("app_version", ""))
    return {
        "token": token, "must_change_password": customer.must_change_password,
        "account": account_data(customer), "license": payload,
    }


def account_data(customer):
    return {"name": customer.name, "email": customer.email, "contact_number": customer.contact_number,
            "user_id": customer.tally_serial, "tally_serial": customer.tally_serial, "product": customer.product.code}


# ------------------------------------------------------------------ tally info
_INFO_FIELDS = ("edition", "tss_details", "virtual_user_limit", "license_administrator", "gateway_server", "registered_email", "tss_expiry_date")


@transaction.atomic
def sync_tally_info(customer, device, data):
    """Stores the values the local client reports. Only fields the client marked verified are saved; the rest are recorded
    as 'unavailable' and never overwrite an earlier verified value with a blank."""
    info, _ = TallyInfo.objects.select_for_update().get_or_create(customer=customer)
    try:
        availability = json.loads(info.availability or "{}")
    except ValueError:
        availability = {}
    verified = set(data.get("verified_fields") or [])
    for field in _INFO_FIELDS:
        value = data.get(field)
        has_value = value not in (None, "")
        if has_value and (field in verified or not data.get("verified_fields")):
            setattr(info, field, value)
            availability[field] = "verified"
        elif availability.get(field) != "verified":
            availability[field] = "unavailable"  # a verified value is never replaced by a blank
    info.availability = json.dumps(availability)
    info.reported_at = timezone.now()
    info.reported_device = device
    info.save()
    audit("TALLY_INFO_SYNC", ", ".join(sorted(k for k, v in availability.items() if v == "verified")) or "nothing verified",
          customer=customer, actor="customer")
    return info


# ------------------------------------------------------------------ usage
def record_open(customer, device, kind="open", app_version=""):
    """One successful application open on an enrolled, authorised device. Opens within 60 s of the last one count once."""
    now = timezone.now()
    last = customer.usage_events.order_by("-occurred_at").values_list("occurred_at", flat=True).first()
    if last and (now - last).total_seconds() < conf("TALLY_USAGE_DEDUPE_SECONDS", 60):
        return False
    TallyUsageEvent.objects.create(
        customer=customer, device=device, kind=kind, app_version=app_version[:30], occurred_at=now, day=timezone.localtime(now, IST).date()
    )
    if device:
        TallyDevice.objects.filter(pk=device.pk).update(last_seen_at=now)
    return True


def usage_rule():
    """Classification thresholds come from backend configuration (settings / environment), not from a UI form."""
    from types import SimpleNamespace
    return SimpleNamespace(
        new_user_days=int(conf("TALLY_NEW_USER_DAYS", 7)),
        speed_window_days=int(conf("TALLY_SPEED_WINDOW_DAYS", 30)),
        speed_min_active_days=int(conf("TALLY_SPEED_MIN_ACTIVE_DAYS", 15)),
    )


CATEGORIES = ("speed", "normal", "new", "one_time")
CATEGORY_LABELS = {"speed": "Speed User", "normal": "Normal User", "new": "New User", "one_time": "One-Time User"}


def classify(registered_at, opens, active_days, rule, now):
    """Mutually exclusive, in this order: new -> one_time -> speed -> normal. Returns a category or 'zero_use' / 'dormant'
    (internal buckets, never shown as one of the four categories)."""
    if registered_at >= now - timedelta(days=rule.new_user_days):
        return "new"
    if opens == 0:
        return "zero_use"
    if opens == 1:
        return "one_time"
    if active_days >= rule.speed_min_active_days:
        return "speed"
    if active_days >= 1:
        return "normal"
    return "dormant"  # returned before, but nothing inside the window


def usage_rows(customers):
    """[(customer, {opens, last_opened, active_days, category})] for a customer queryset, classification applied."""
    rule = usage_rule()
    now = timezone.now()
    cutoff = timezone.localtime(now, IST).date() - timedelta(days=rule.speed_window_days - 1)
    qs = customers.select_related("product", "license").annotate(
        opens=Count("usage_events", distinct=True),
        last_opened=Max("usage_events__occurred_at"),
        active_days=Count("usage_events__day", distinct=True, filter=Q(usage_events__day__gte=cutoff)),
    )
    return [
        (c, {"opens": c.opens, "last_opened": c.last_opened, "active_days": c.active_days,
             "category": classify(c.registered_at, c.opens, c.active_days, rule, now)})
        for c in qs
    ]


# ------------------------------------------------------------------ Activate User / device replacement
def request_activation_code(product, user_id, ip=""):
    """Returns (customer, raw_code) when an email should be sent; None otherwise (unknown account, throttled)."""
    customer = find_customer(product, user_id)
    if customer is None or not customer.email:
        return None
    now = timezone.now()
    if customer.verification_codes.filter(created_at__gte=now - timedelta(seconds=60)).exists():
        return None
    if customer.verification_codes.filter(created_at__gte=now - timedelta(hours=1)).count() >= 5:
        return None
    raw = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(8))
    with transaction.atomic():
        customer.verification_codes.filter(consumed_at__isnull=True).update(consumed_at=now)  # only the newest code works
        TallyVerificationCode.objects.create(
            customer=customer, code_hash=_keyed("code", f"{customer.pk}|{raw}"),
            expires_at=now + timedelta(seconds=conf("TALLY_CODE_TTL_SECONDS", 600)),
        )
        audit("ACTIVATION_CODE_SENT", customer=customer, actor="customer", ip=ip)
    return customer, raw


def verify_activation(product, user_id, code, activation_key, ip=""):
    """Validates customer + licence + emailed code + Nexus activation key. Returns a single-use confirmation ticket."""
    customer = find_customer(product, user_id)
    invalid = TallyError("The verification code is invalid or has expired.", 400, "invalid_code")
    if customer is None:
        raise invalid
    now = timezone.now()
    row = customer.verification_codes.filter(consumed_at__isnull=True, verified_at__isnull=True, expires_at__gt=now).order_by("-id").first()
    if row is None or row.attempts >= conf("TALLY_CODE_MAX_ATTEMPTS", 5):
        raise invalid
    code_ok = hmac.compare_digest(row.code_hash, _keyed("code", f"{customer.pk}|{normalize_key(code)}"))
    lic = customer.license
    key_ok = bool(lic.activation_key_hash) and hmac.compare_digest(lic.activation_key_hash, _digest(normalize_key(activation_key)))
    if not (code_ok and key_ok):
        TallyVerificationCode.objects.filter(pk=row.pk).update(attempts=F("attempts") + 1)
        audit("ACTIVATION_VERIFY_FAILED", "code" if not code_ok else "activation key", customer=customer, actor="customer", ip=ip)
        raise invalid if not code_ok else TallyError("The activation key is not valid for this account.", 400, "invalid_activation_key")
    state = license_state(lic, now)
    if state != "active":
        raise TallyError(f"This license is not active ({state}). Contact Sai Infosys.", 403, "license_not_active", {"state": [state]})
    ticket = secrets.token_urlsafe(32)
    claimed = TallyVerificationCode.objects.filter(pk=row.pk, verified_at__isnull=True).update(
        verified_at=now, ticket_hash=_digest(ticket), ticket_expires_at=now + timedelta(seconds=conf("TALLY_TICKET_TTL_SECONDS", 600))
    )
    if not claimed:  # a concurrent request already used this code: replay
        raise invalid
    current = active_device(customer)
    audit("ACTIVATION_VERIFIED", customer=customer, actor="customer", ip=ip)
    return {
        "ticket": ticket,
        "requires_confirmation": current is not None,
        "message": TRANSFER_WARNING if current else "",
        "current_device": {"name": current.device_name, "enrolled_at": current.enrolled_at} if current else None,
        "expires_in": conf("TALLY_TICKET_TTL_SECONDS", 600),
    }


def confirm_activation(product, ticket, device_info, confirm_transfer, ip=""):
    """Atomically enrols this computer, revoking the previous one. Returns (session token, licence payload, replaced?)."""
    info = device_info or {}
    if not info.get("client_device_id") or not info.get("fingerprint"):
        raise TallyError("Device identity is required.", 400, "device_required", {"device": ["client_device_id and fingerprint are required."]})
    now = timezone.now()
    with transaction.atomic():
        row = TallyVerificationCode.objects.select_for_update().select_related("customer__product", "customer__license").filter(
            ticket_hash=_digest(ticket or ""), customer__product=product
        ).first()
        if row is None or row.consumed_at or not row.ticket_expires_at or row.ticket_expires_at <= now:
            raise TallyError("This activation request is invalid, already used or has expired. Start again.", 400, "invalid_ticket")
        customer = TallyCustomer.objects.select_for_update().select_related("product", "license").get(pk=row.customer_id)
        if license_state(customer.license, now) != "active":
            raise TallyError("This license is not active. Contact Sai Infosys.", 403, "license_not_active")
        current = active_device(customer)
        same = _matches(current, info["client_device_id"], info["fingerprint"])
        if current is not None and not same and not confirm_transfer:
            raise TallyError(TRANSFER_WARNING, 409, "confirmation_required")  # ticket stays valid for the Confirm
        if not TallyVerificationCode.objects.filter(pk=row.pk, consumed_at__isnull=True).update(consumed_at=now):
            raise TallyError("This activation request was already used.", 400, "invalid_ticket")  # replay
        replaced = False
        if same:
            device = current
            TallyDevice.objects.filter(pk=device.pk).update(device_name=(info.get("device_name") or device.device_name)[:100],
                                                            app_version=(info.get("app_version") or "")[:30], last_seen_at=now)
        else:
            generation = (customer.devices.aggregate(m=Max("generation"))["m"] or 0) + 1
            if current is not None:
                replaced = True
                current.revoked_at, current.revoked_reason = now, "Replaced by a new device"
                current.save(update_fields=["revoked_at", "revoked_reason"])
                customer.sessions.filter(revoked_at__isnull=True).update(revoked_at=now, revoked_reason="device_replaced")
            device = TallyDevice.objects.create(
                customer=customer, client_device_id=info["client_device_id"][:64], fingerprint_hash=_keyed("fp", info["fingerprint"]),
                device_name=(info.get("device_name") or "")[:100], app_version=(info.get("app_version") or "")[:30],
                generation=generation, last_seen_at=now,
            )
            TallyDeviceTransfer.objects.create(
                customer=customer, kind=TallyDeviceTransfer.REPLACED if replaced else TallyDeviceTransfer.ENROLLED,
                from_device=current, to_device=device, performed_by="customer", ip_address=ip,
            )
            audit("DEVICE_REPLACED" if replaced else "DEVICE_ENROLLED", f"{device.device_name or device.client_device_id}",
                  customer=customer, actor="customer", ip=ip)
        token = issue_session(customer, info["client_device_id"], device)
        payload = license_payload(customer, device, now)
    if payload["authorized"] and not customer.must_change_password:
        record_open(customer, device, "login", info.get("app_version", ""))
    return {"token": token, "license": payload, "account": account_data(customer), "device_replaced": replaced,
            "must_change_password": customer.must_change_password}, customer, replaced


# ------------------------------------------------------------------ staff: licence management
def _issue_key(lic):
    raw = new_activation_key()
    lic.activation_key_hash = _digest(normalize_key(raw))
    lic.activation_key_suffix = raw[-4:]
    lic.activation_key_issued_at = timezone.now()
    return raw


def _lock(customer):
    return TallyCustomer.objects.select_for_update().select_related("product", "license").get(pk=customer.pk)


@transaction.atomic
def activate_license(customer, license_type, trial_days, user, ip=""):
    """Superadmin activates (or re-activates) the licence and issues a NEW activation key (returned once, and emailed)."""
    license_type = STORED_OF.get(license_type)
    if license_type is None:
        raise TallyError("License Plan must be TRIAL, ANNUAL or LIFETIME.", 400, "invalid_license_plan")
    customer = _lock(customer)
    lic = customer.license
    now = timezone.now()
    lic.license_type, lic.status, lic.activated_at, lic.activated_by = license_type, TallyLicense.ACTIVE, now, user
    if license_type == TallyLicense.TRIAL:
        days = int(trial_days or customer.product.default_trial_days or 3)
        if not 1 <= days <= 365:
            raise TallyError("Trial days must be between 1 and 365.", 400, "invalid_trial_days")
        lic.trial_days, lic.expires_at = days, now + timedelta(days=days)
    elif license_type == TallyLicense.LICENSED:
        lic.trial_days, lic.expires_at = None, now + timedelta(days=365)
    else:
        lic.trial_days, lic.expires_at = None, None
    raw = _issue_key(lic)
    lic.save()
    audit("LICENSE_ACTIVATED", f"{lic.get_license_type_display()}" + (f", {lic.trial_days} days" if lic.trial_days else ""),
          customer=customer, actor=actor_name(user), ip=ip)
    return customer, raw


@transaction.atomic
def extend_trial(customer, days, user, ip=""):
    customer = _lock(customer)
    lic = customer.license
    if lic.license_type != TallyLicense.TRIAL or lic.status != TallyLicense.ACTIVE:
        raise TallyError("Only an active trial can be extended.", 409, "not_a_trial")
    days = int(days or 0)
    if not 1 <= days <= 365:
        raise TallyError("Extension must be between 1 and 365 days.", 400, "invalid_trial_days")
    lic.expires_at = max(lic.expires_at or timezone.now(), timezone.now()) + timedelta(days=days)
    lic.trial_days = (lic.trial_days or 0) + days
    lic.save()
    audit("TRIAL_EXTENDED", f"+{days} days, now ends {lic.expires_at:%d/%m/%Y}", customer=customer, actor=actor_name(user), ip=ip)
    return customer


@transaction.atomic
def cancel_trial(customer, user, ip=""):
    customer = _lock(customer)
    lic = customer.license
    if lic.license_type != TallyLicense.TRIAL or lic.status != TallyLicense.ACTIVE:
        raise TallyError("Only an active trial can be cancelled.", 409, "not_a_trial")
    lic.status = TallyLicense.TRIAL_CANCELLED
    lic.save()
    audit("TRIAL_CANCELLED", customer=customer, actor=actor_name(user), ip=ip)
    return customer


@transaction.atomic
def renew_license(customer, user, ip=""):
    customer = _lock(customer)
    lic = customer.license
    if lic.license_type != TallyLicense.LICENSED or lic.status == TallyLicense.PENDING:
        raise TallyError("Only a 1-Year licensed customer can be renewed.", 409, "not_renewable")
    now = timezone.now()
    previous = lic.expires_at
    lic.expires_at = max(lic.expires_at or now, now) + timedelta(days=365)
    lic.status = TallyLicense.ACTIVE
    lic.save()
    audit("LICENSE_RENEWED", f"{previous:%d/%m/%Y} -> {lic.expires_at:%d/%m/%Y}" if previous else f"until {lic.expires_at:%d/%m/%Y}",
          customer=customer, actor=actor_name(user), ip=ip)
    return customer


@transaction.atomic
def inactivate_license(customer, user, ip=""):
    customer = _lock(customer)
    lic = customer.license
    if lic.status == TallyLicense.INACTIVE:
        raise TallyError("This license is already inactive.", 409, "already_inactive")
    lic.status = TallyLicense.INACTIVE
    lic.save()
    audit("LICENSE_INACTIVATED", customer=customer, actor=actor_name(user), ip=ip)
    return customer


@transaction.atomic
def reissue_key(customer, user, ip=""):
    """Only a hash of the key is stored, so 'resend' means issuing a new key (the old one stops working) and emailing it."""
    customer = _lock(customer)
    lic = customer.license
    if license_state(lic) != "active":
        raise TallyError("Activate the license before generating an activation key.", 409, "license_not_active")
    raw = _issue_key(lic)
    lic.save()
    audit("KEY_REISSUED", f"ending {lic.activation_key_suffix}", customer=customer, actor=actor_name(user), ip=ip)
    return customer, raw


@transaction.atomic
def revoke_device(customer, user, reason="Revoked by an administrator", ip=""):
    customer = _lock(customer)
    device = active_device(customer)
    if device is None:
        raise TallyError("No device is enrolled for this customer.", 409, "no_device")
    now = timezone.now()
    device.revoked_at, device.revoked_reason = now, reason[:200]
    device.save(update_fields=["revoked_at", "revoked_reason"])
    customer.sessions.filter(revoked_at__isnull=True).update(revoked_at=now, revoked_reason="device_revoked")
    TallyDeviceTransfer.objects.create(customer=customer, kind=TallyDeviceTransfer.REVOKED, from_device=device,
                                       performed_by=actor_name(user), ip_address=ip)
    audit("DEVICE_REVOKED", reason, customer=customer, actor=actor_name(user), ip=ip)
    return customer


@transaction.atomic
def update_customer(customer, data, user, ip=""):
    customer = _lock(customer)
    changed = []
    for field in ("name", "email", "contact_number"):
        if field in data and data[field] != getattr(customer, field):
            setattr(customer, field, data[field].strip().lower() if field == "email" else data[field].strip())
            changed.append(field)
    if changed:
        customer.save()
        audit("CUSTOMER_EDITED", ", ".join(changed), customer=customer, actor=actor_name(user), ip=ip)
    return customer


@transaction.atomic
def delete_customer(customer, user, ip=""):
    customer = _lock(customer)
    audit("CUSTOMER_DELETED", f"{customer.tally_serial} {customer.name}", product=customer.product, actor=actor_name(user), ip=ip)
    customer.delete()
