"""The customer-facing side of Nexus, called by connected projects' backends (see portal_views.py).

Everything here is keyed by (project, customer): a customer is looked up only inside the project the caller's
credential belongs to, so a User ID or token from one project means nothing in another.
"""
import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import audit, mailer, services
from .models import PROJECTS, ProjectCustomer, RegistrationRequest

logger = logging.getLogger(__name__)

TOKEN_SALT = "nexus.customer-token"
RESET_SALT = "nexus.customer-reset"


class PortalError(Exception):
    """A rule failure with the HTTP status to answer with."""

    def __init__(self, message, status=400, code=""):
        super().__init__(message)
        self.status, self.code = status, code


# ---------------------------------------------------------------- email
def _send(subject, body, to, kind="notice", customer=None, project=""):
    """Best-effort notice: one attempt, so a mail outage never stalls the request that triggered it. True when the
    message was handed to the mail server."""
    return _send_status(subject, body, to, kind=kind, customer=customer, project=project, attempts=1) == "accepted"


def _send_status(subject, body, to, html=None, kind="notice", customer=None, project="", attempts=None, event_id=""):
    """accepted | not_delivered | failed - see mailer.deliver (retries + delivery log). Logs never contain the body."""
    return mailer.deliver(
        subject, body, to, html=html, kind=kind, customer=customer, project=project or getattr(customer, "project", ""),
        attempts=attempts, event_id=event_id,
    )


EMAIL_STATUS_LABELS = {
    "": "Pending",
    "sending": "Sending",
    "accepted": "Submitted",  # handed to the SMTP server; SMTP gives no delivery receipt, so inbox delivery is never claimed
    "not_delivered": "Failed (no mail server configured)",
    "failed": "Failed",
}


def format_activation_key(raw):
    """How a key is shown to people: the current 9-character keys as they are (A7K9P2X4M); older 16-character keys in
    groups of four (ABCD-EFGH-JKLM-NPQR), as their customers already know them. Matching ignores dashes either way."""
    key = services.normalize_key(raw)
    if len(key) == services.ACTIVATION_KEY_LENGTH:
        return key
    return "-".join(key[i : i + 4] for i in range(0, len(key), 4))


def _date(value):
    return timezone.localtime(value).strftime("%d/%m/%Y")


def license_rows(customer):
    """License Plan / Activation Date / Expiry Date rows for an email. A Trial shows only "Trial" (its length is never
    stated); Lifetime has no expiry; a 1 Year licence shows its expiry date, or - for a device product, whose year only
    starts when the customer first activates their computer - says so instead of guessing a date."""
    rows = [("License Plan", customer.get_license_type_display()), ("Activation Date", _date(customer.activated_at))]
    if customer.license_type == ProjectCustomer.ONE_YEAR:
        expiry = services.effective_expiry(customer)
        rows.append(("Expiry Date", _date(expiry) if expiry else "One year from the day you first activate on your computer"))
    return rows


def _record_email(customer, status):
    customer.credentials_email_status = status
    customer.credentials_emailed_at = timezone.now() if status == "accepted" else None
    ProjectCustomer.objects.filter(pk=customer.pk).update(
        credentials_email_status=customer.credentials_email_status, credentials_emailed_at=customer.credentials_emailed_at
    )


def send_credentials(customer, raw_password, activation_key, event_id=None):
    """Emails the new customer everything needed to start: project, User ID, the one-time temporary password, Serial
    Number, Activation Key, licence plan and dates. The password and key are never stored in readable form or logged.
    The delivery status is recorded on the customer and in EmailLog; returns True only when a real mail server accepted it.
    The one place a credentials email is sent from. Pass the `event_id` from claim_credentials_email when the caller already
    claimed the send (Retry / Resend claim it inside their locked transaction); otherwise it is claimed here. If another
    request is already sending, nothing is sent and False is returned."""
    current = ProjectCustomer.objects.filter(pk=customer.pk).values("password_changed_at", "password_hash").first()
    if current is None or current["password_changed_at"] or not services.verify_password(customer, raw_password):
        return False
    if current["password_hash"] != customer.password_hash:
        return False
    if event_id is None:
        event_id = services.claim_credentials_email(customer)
    if not event_id:
        logger.info("Credentials email for customer %s is already being sent; not sending a second copy", customer.pk)
        return False
    label = PROJECTS[customer.project]
    rows = [
        ("Customer Name", customer.name),
        ("Product Name", label),
        ("User ID", customer.user_id),
        ("Temporary Password", raw_password),
        ("Serial Number", customer.serial_number or customer.user_id),
        (
            "Activation Key",
            format_activation_key(activation_key)
            if activation_key
            else f"Unchanged - use the key from your first email (ending {customer.activation_key_suffix})",
        ),
    ] + license_rows(customer)
    steps = [
        f"Install and open {label}.",
        "Sign in with your User ID and the temporary password above (this computer is registered automatically).",
        "You can go straight to the dashboard. To choose a password of your own, open My Profile at any time.",
    ]
    notes = [
        "Keep the Activation Key for your records and for support. Do not share the Activation Key or the temporary password.",
    ]
    intro = f"Your {label} account has been created."
    status = _send_status(
        f"Your {label} account - SAI INFOSYS",
        mailer.render_text(f"Hello {customer.name},", intro, rows, steps, notes),
        customer.email,
        html=mailer.render_html("Welcome to " + label, intro, rows, steps, notes),
        kind="credentials",
        customer=customer,
        event_id=event_id,
    )
    _record_email(customer, status)
    return status == "accepted"


def send_new_activation_key(customer, activation_key):
    """Emails a re-issued Activation Key (never the password). Same status handling as send_credentials."""
    label = PROJECTS[customer.project]
    body = "\n".join(
        [
            f"Hello {customer.name},",
            "",
            f"A new Activation Key has been issued for your {label} account. Any earlier key no longer works.",
            "",
            f"User ID / Serial Number: {customer.user_id}",
            f"Activation Key: {format_activation_key(activation_key)}",
            "",
            f"Open {label}, choose \"I Already Have an Account\" and enter both to activate this computer.",
            "",
            "Sai Infosys Nexus",
        ]
    )
    status = _send_status(f"Your {label} activation key", body, customer.email, kind="activation_key", customer=customer)
    _record_email(customer, status)
    return status == "accepted"


def send_reset_credentials(customer, raw_password):
    """Emails a new temporary password after a Super Admin reset (or legacy-account link). Never stored or logged."""
    label = PROJECTS[customer.project]
    body = "\n".join(
        [
            f"Hello {customer.name},",
            "",
            f"Your {label} password has been reset by an administrator.",
            "",
            f"User ID: {customer.user_id}",
            f"Temporary password: {raw_password}",
            "",
            "You will be asked to choose a new password when you next sign in.",
            "If you did not expect this, contact Sai Infosys.",
            "",
            "Sai Infosys Nexus",
        ]
    )
    return _send(f"Your {label} password was reset", body, customer.email, kind="password_reset_admin", customer=customer)


# ---------------------------------------------------------------- registration requests
def create_request(project, data):
    email = data["email"].strip().lower()
    if ProjectCustomer.objects.filter(project=project, email__iexact=email).exists():
        raise PortalError("An account already exists for this email.", 409, "account_exists")
    if RegistrationRequest.objects.filter(project=project, email=email, status=RegistrationRequest.PENDING).exists():
        raise PortalError("A registration request for this email is already waiting for approval.", 409, "request_pending")
    req = RegistrationRequest.objects.create(
        project=project, name=data["name"], contact_number=data["contact_number"], email=email
    )
    audit.record(None, "REQUEST_RECEIVED", f"{req.name} <{email}>", project)
    notify_new_request(req)
    return req


def request_status(project, request_id, email):
    """Where a sign-up stands: pending | approved | rejected. Needs BOTH the request id and the email it was made with, so
    one cannot be guessed from the other; any mismatch answers the same 404. Reveals no other data (no User ID, no key)."""
    try:
        req = RegistrationRequest.objects.get(pk=int(request_id), project=project, email=(email or "").strip().lower())
    except (RegistrationRequest.DoesNotExist, TypeError, ValueError):
        raise PortalError("Registration request not found.", 404, "not_found")
    status = {"PENDING": "pending", "PROCESSED": "approved", "REJECTED": "rejected"}[req.status]
    label = {"pending": "Pending Approval", "approved": "Approved", "rejected": "Rejected"}[status]
    return {"id": req.id, "status": status, "status_label": label, "processed_at": req.processed_at}


def notify_new_request(req):
    """Tells every active Super Admin (and NEW_REQUEST_NOTIFY_EMAILS) that a sign-up is waiting. Best effort: the
    request is already saved, so a mail failure is logged and never fails the customer's sign-up."""
    from apps.accounts.constants import Role

    recipients = set(
        get_user_model()
        .objects.filter(is_active=True, profile__role=Role.SUPERADMIN)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    recipients.update(settings.NEW_REQUEST_NOTIFY_EMAILS)
    label = PROJECTS[req.project]
    body = "\n".join(
        [
            f"A new {label} sign-up request is waiting for approval.",
            "",
            f"Name: {req.name}",
            f"Contact number: {req.contact_number}",
            f"Email: {req.email}",
            f"Received: {timezone.localtime(req.created_at):%d/%m/%Y %H:%M}",
            "",
            f"Open Nexus -> {label} -> Customer List to review it (request #{req.id}).",
            "",
            "Sai Infosys Nexus",
        ]
    )
    for to in sorted(recipients):
        _send(f"New {label} sign-up request", body, to)


# ---------------------------------------------------------------- tokens
def _fingerprint(customer):
    """Changes whenever the password does, so tokens and reset links die with the password they were issued for."""
    return hashlib.sha256(customer.password_hash.encode()).hexdigest()[:16]


def issue_token(customer, device_id=None):
    """A token issued to a device (device_id given) is only accepted from that same device."""
    data = {"c": customer.pk, "p": customer.project, "v": _fingerprint(customer)}
    if device_id:
        data["d"] = str(device_id)
    return signing.dumps(data, salt=TOKEN_SALT)


def _load(token, salt, project, max_age, device_id=None):
    try:
        data = signing.loads(token or "", salt=salt, max_age=max_age)
        customer = ProjectCustomer.objects.get(pk=data["c"], project=project)
    except (signing.BadSignature, ProjectCustomer.DoesNotExist, KeyError, TypeError):
        raise PortalError("Invalid or expired token.", 401, "invalid_token")
    if data.get("p") != project or data.get("v") != _fingerprint(customer):
        raise PortalError("Invalid or expired token.", 401, "invalid_token")
    if device_id is not None and data.get("d") != str(device_id):
        raise PortalError("Invalid or expired token.", 401, "invalid_token")
    return customer


def customer_from_token(token, project, device_id=None):
    return _load(token, TOKEN_SALT, project, settings.CUSTOMER_TOKEN_MAX_AGE, device_id)


# ---------------------------------------------------------------- sign in / status
def license_status(customer):
    """What a project needs to decide access. `lease_until` is how long an offline client may rely on this answer:
    never past the license's own expiry, and at most LICENSE_LEASE_DAYS. Revocation reaches an offline client by then."""
    now = timezone.now()
    state = services.state_of(customer, now)
    authorized = state == "active"
    lease_until = None
    if authorized:
        lease_until = now + timedelta(days=settings.LICENSE_LEASE_DAYS)
        if customer.expires_at and customer.expires_at < lease_until:
            lease_until = customer.expires_at
    return {
        "authorized": authorized,
        "state": state,
        "license_type": customer.license_type,
        "expires_at": customer.expires_at,
        "checked_at": now,
        "lease_until": lease_until,
    }


def login(project, username, password):
    customer = ProjectCustomer.objects.filter(project=project, user_id__iexact=(username or "").strip()).first()
    if customer and customer.locked_until and customer.locked_until > timezone.now():
        raise PortalError("Too many failed attempts. Try again later or reset the password.", 429, "account_locked")
    if not customer or not services.verify_password(customer, password or ""):
        if customer:
            _register_failure(customer)
        raise PortalError("Invalid username or password.", 401, "invalid_credentials")
    if customer.failed_logins or customer.locked_until:
        ProjectCustomer.objects.filter(pk=customer.pk).update(failed_logins=0, locked_until=None)
    return customer


def _register_failure(customer):
    """Counts a wrong password; the account locks for a while after too many in a row."""
    ProjectCustomer.objects.filter(pk=customer.pk).update(failed_logins=F("failed_logins") + 1)
    customer.refresh_from_db(fields=["failed_logins"])
    if customer.failed_logins >= settings.CUSTOMER_MAX_FAILED_LOGINS:
        until = timezone.now() + timedelta(minutes=settings.CUSTOMER_LOCKOUT_MINUTES)
        ProjectCustomer.objects.filter(pk=customer.pk).update(failed_logins=0, locked_until=until)
        audit.record(None, "CUSTOMER_LOCKED", f"{customer.user_id} until {until:%H:%M}", customer.project, customer)


def account_data(customer):
    return {
        "name": customer.name,
        "company_name": customer.company_name,
        "contact_number": customer.contact_number,
        "email": customer.email,
        "user_id": customer.user_id,
        "serial_number": customer.serial_number,
        "must_change_password": customer.password_change_required,
    }


def session_data(customer, device_id=None):
    return {
        "token": issue_token(customer, device_id),
        "must_change_password": customer.password_change_required,
        "account": account_data(customer),
        "license": license_status(customer),
    }


def check_new_password(customer, raw):
    """Same rule as the staff accounts: any non-empty password is accepted (no length or complexity rules); case-insensitive."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 128:
        raise PortalError("Password is required.", 400, "weak_password")


def _set_password(customer, raw):
    check_new_password(customer, raw)
    with transaction.atomic():
        customer.password_hash = services.hash_password(raw)
        customer.must_change_password = False
        customer.password_changed_at = timezone.now()
        customer.failed_logins, customer.locked_until = 0, None
        customer.save(
            update_fields=["password_hash", "must_change_password", "password_changed_at", "failed_logins", "locked_until", "updated_at"]
        )


def change_password(customer, current, new):
    """The customer picks a new password (from Barcode AutoPrint). It replaces the one Nexus holds at once, so the next
    login, here or anywhere else, uses it; tokens issued for the old password stop working."""
    if not services.verify_password(customer, current or ""):
        raise PortalError("Current password is incorrect.", 400, "wrong_password")
    if services.verify_password(customer, new or ""):
        raise PortalError("Choose a password different from the current one.", 400, "same_password")
    _set_password(customer, new)
    audit.record(None, "CUSTOMER_PASSWORD_CHANGED", customer.user_id, customer.project, customer)
    send_password_changed(customer)


# ---------------------------------------------------------------- recovery
def recover_username(project, email):
    """Answers the same whether or not the email is known, so it cannot be used to find accounts."""
    email = (email or "").strip()
    if not email:
        return
    for customer in ProjectCustomer.objects.filter(project=project, email__iexact=email):
        _send(
            f"Your {PROJECTS[project]} User ID",
            f"Hello {customer.name},\n\nYour {PROJECTS[project]} User ID is: {customer.user_id}\n\nSai Infosys Nexus",
            customer.email,
        )


def request_password_reset(project, identifier, client):
    identifier = (identifier or "").strip()
    if not identifier:
        return
    customer = ProjectCustomer.objects.filter(project=project, email__iexact=identifier).first() or ProjectCustomer.objects.filter(
        project=project, user_id__iexact=identifier
    ).first()
    if not customer or not customer.email or not client.reset_url:
        return
    token = signing.dumps({"c": customer.pk, "p": project, "v": _fingerprint(customer)}, salt=RESET_SALT)
    sep = "&" if "?" in client.reset_url else "?"
    _send(
        f"Reset your {PROJECTS[project]} password",
        f"Hello {customer.name},\n\nUse this link to choose a new password (valid for "
        f"{settings.PASSWORD_RESET_MAX_AGE // 60} minutes, one use):\n{client.reset_url}{sep}token={token}\n\n"
        "If you did not ask for this, ignore this email.\n\nSai Infosys Nexus",
        customer.email,
    )


def reset_password(project, token, new):
    customer = _load(token, RESET_SALT, project, settings.PASSWORD_RESET_MAX_AGE)
    _set_password(customer, new)  # changes the fingerprint, so the link cannot be used twice
    audit.record(None, "CUSTOMER_PASSWORD_RESET", customer.user_id, project, customer)
    send_password_changed(customer)


def send_password_changed(customer):
    """No passwords in notices. Failure is logged by the existing mailer and cannot undo a password change."""
    if not customer.email:
        return False
    return _send_status(
        f"{PROJECTS[customer.project]} Password Changed",
        f"Hello {customer.name},\n\nYour password has been changed. Sign in using your new password. "
        "If you did not make this change, use Forgot Password in your application and contact support.\n\nSai Infosys Nexus",
        customer.email, kind="password_changed", customer=customer, attempts=1,
    ) == "accepted"
