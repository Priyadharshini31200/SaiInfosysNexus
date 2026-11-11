"""License rules for project customers. All state changes happen here, inside a transaction, with an audit entry."""
import hashlib
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import audit
from .models import DEVICE_PROJECTS, IssuedActivationKey, LicenseRenewal, ProjectCustomer, RegistrationRequest

logger = logging.getLogger(__name__)

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
# New activation keys are exactly this long (e.g. A7K9P2X4M). Keys issued earlier (16 characters) stay valid.
ACTIVATION_KEY_LENGTH = 9


class RuleError(Exception):
    """A license action that is not allowed in the customer's current state."""


def _password_secret(raw):
    """Customer passwords are case-insensitive, so they are normalised before hashing and before checking."""
    return raw.casefold()


def hash_password(raw):
    return make_password(_password_secret(raw))


def verify_password(customer, raw):
    """True when `raw` matches the customer's password, ignoring case. Hashes saved before passwords became
    case-insensitive were made from the exact text, so those still verify with it."""
    return check_password(_password_secret(raw), customer.password_hash) or check_password(raw, customer.password_hash)


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def normalize_key(key):
    return "".join(ch for ch in key.upper() if ch.isalnum())


def _new_key():
    """9 random characters from _ALPHABET (cryptographically secure), with at least one letter and one digit - so a key can
    never look like a Serial Number (7 + 8 digits), which client apps tell apart by that pattern."""
    while True:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(ACTIVATION_KEY_LENGTH))
        if any(c.isalpha() for c in raw) and any(c.isdigit() for c in raw):
            return raw


def generate_activation_key(project, user):
    """Returns a new unique key like A7K9P2X4M (exactly 9 characters). Stored only as a hash. Unique across every key ever
    issued (the hash is a unique column) and every customer's current key, including older 16-character ones."""
    for _ in range(5):
        raw = _new_key()
        key_hash = _hash(raw)
        if ProjectCustomer.objects.filter(activation_key_hash=key_hash).exists():
            continue
        try:
            with transaction.atomic():
                IssuedActivationKey.objects.create(project=project, key_hash=key_hash, key_suffix=raw[-4:], created_by=user)
        except IntegrityError:
            continue
        audit.record(user, "KEY_GENERATED", f"Activation key ending {raw[-4:]}", project)
        return raw
    raise RuleError("Could not generate a unique key. Try again.")


_SERIAL_DB = re.compile(r"[A-Za-z0-9_]{1,128}")


def _legacy_serial_in_use(serial):
    """Best effort: is the serial already a licence serial in the PRN project's own (legacy) database? Linking by serial
    number must never merge two different customers. A database that cannot be read counts as "not in use"."""
    from django.db import DatabaseError, connection

    db = getattr(settings, "PRN_DB_NAME", "")
    if not _SERIAL_DB.fullmatch(db or ""):
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT TOP 1 1 FROM [{db}].dbo.Licenses WHERE serial_number = %s", [serial])
            return cursor.fetchone() is not None
    except DatabaseError:
        return False


def serial_in_use(serial):
    """True when ANY Nexus customer of ANY product already has this number as Serial Number or User ID, or it is a licence
    serial in the PRN project's legacy database."""
    taken = ProjectCustomer.objects.filter(Q(serial_number=serial) | Q(user_id__iexact=serial)).exists()
    return taken or _legacy_serial_in_use(serial)


def generate_serial_number(project=None):
    """A random 9-digit serial starting with 7 that no customer account of any product uses. Saving re-checks it in SQL
    Server (unique per project), and a conflict there is retried with a new number (see register_with_unique_serial)."""
    for _ in range(30):
        serial = "7" + "".join(secrets.choice("0123456789") for _ in range(8))
        if not serial_in_use(serial):
            return serial
    raise RuleError("Could not generate a unique serial number. Try again.")


class SaveError(Exception):
    """The database refused a customer for a reason that is not a repeatable serial conflict. `kind` names it; `message`
    is safe to show to staff (it names the column or constraint, never a value)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind, self.message = kind, message


def classify_integrity_error(exc):
    """serial | user_id | activation_key | missing_value | other - from SQL Server's own message, so a NULL / length / key
    problem is never reported as a duplicate serial number."""
    text = str(exc)
    if "NULL" in text and "does not allow nulls" in text:
        column = re.search(r"column '([A-Za-z0-9_]+)'", text)
        return "missing_value", column.group(1) if column else ""
    lowered = text.lower()
    if "uniq_customer_serial_per_project" in lowered:
        return "serial", ""
    if "uniq_customer_user_id_per_project" in lowered:
        return "user_id", ""
    if "activation_key_hash" in lowered:
        return "activation_key", ""
    return "other", ""


def describe_save_error(exc):
    kind, detail = classify_integrity_error(exc)
    if kind == "missing_value":
        return SaveError(kind, f"The customer could not be saved: the database requires a value for '{detail}' that the running server did not supply. "
                               "Restart the Nexus backend so it matches the current database, then try again.")
    if kind == "activation_key":
        return SaveError(kind, "The customer could not be saved: that activation key is already assigned to another customer. Generate a new key.")
    return SaveError(kind, "The customer could not be saved because the database rejected it (constraint violation). Nothing was changed.")


MAX_SERIAL_ATTEMPTS = 5


def customer_for_key(project, raw_key):
    """The customer this activation key was already used to create, if any - so repeating a Save (double click, timeout and
    retry) returns the same customer instead of creating another."""
    return ProjectCustomer.objects.filter(project=project, activation_key_hash=_hash(normalize_key(raw_key))).first()


def register_with_unique_serial(project, data, user):
    """register() with a serial number that is guaranteed free: a taken number is replaced by a new unique one before saving, and a
    uniqueness conflict from SQL Server (two saves racing for one number) is retried a limited number of times with a fresh number.
    The existing customer, whoever holds the number, is never touched. Returns (customer, original serial if it was replaced).
    Anything else the database refuses is reported as it is (SaveError), not as a duplicate."""
    data = dict(data)
    requested = data.get("serial_number") or ""
    replaced = None
    for _ in range(MAX_SERIAL_ATTEMPTS):
        if not requested or serial_in_use(data.get("serial_number") or ""):
            replaced = replaced or (data.get("serial_number") or None)
            data["serial_number"] = data["user_id"] = generate_serial_number(project)
        try:
            return register(project, data, user), replaced
        except IntegrityError as exc:
            kind, _detail = classify_integrity_error(exc)
            if kind not in ("serial", "user_id"):
                logger.exception("Customer save refused by the database (%s)", kind)
                raise describe_save_error(exc)
            replaced = replaced or data.get("serial_number")
            data["serial_number"] = data["user_id"] = generate_serial_number(project)
            requested = data["serial_number"]
    raise RuleError("Could not allocate a unique serial number after several attempts. Please try again.")


def sync_version(project=None):
    """Changes whenever a customer is added or any audited change is made (for one project, or all of them)."""
    from .models import LicenseAuditLog

    logs, customers = LicenseAuditLog.objects.all(), ProjectCustomer.objects.all()
    if project:
        logs, customers = logs.filter(project=project), customers.filter(project=project)
    latest = logs.order_by("-id").values_list("id", flat=True).first() or 0
    return f"{latest}:{customers.count()}"


def key_is_available(project, key):
    return IssuedActivationKey.objects.filter(project=project, key_hash=_hash(normalize_key(key)), used=False).exists()


def trial_period_days(project):
    """The configured trial length of a product whose trial starts at first device activation (None: not configured)."""
    return (settings.TRIAL_DAYS_BY_PRODUCT.get(project) or None) if project in DEVICE_PROJECTS else None


def effective_expiry(customer):
    """The licence's real end, or None when it has none (Lifetime) or has not started.
    Trial: its expiry (for a device product only once the first activation has started it). 1 Year: its expiry (set at
    activation / by renewal). Lifetime: never. Nothing is written."""
    if customer.license_type == ProjectCustomer.LICENSED:
        return None
    if customer.license_type == ProjectCustomer.TRIAL and customer.project in DEVICE_PROJECTS and not customer.first_activated_at:
        return None
    return customer.expires_at


def with_expiry(queryset):
    """The customers of a queryset that have an effective expiry date (the SQL twin of effective_expiry): Lifetime ones are
    left out, and so is a device product's trial that no computer has activated yet."""
    from django.db.models import Q

    started = ~Q(license_type=ProjectCustomer.TRIAL) | ~Q(project__in=DEVICE_PROJECTS) | Q(first_activated_at__isnull=False)
    return queryset.exclude(license_type=ProjectCustomer.LICENSED).filter(started, expires_at__isnull=False)


def state_of(customer, now=None):
    """active | expired | cancelled | inactive"""
    now = now or timezone.now()
    if customer.status == ProjectCustomer.INACTIVE:
        return "inactive"
    if customer.status == ProjectCustomer.TRIAL_CANCELLED:
        return "cancelled"
    if customer.license_type in (ProjectCustomer.TRIAL, ProjectCustomer.ONE_YEAR) and customer.expires_at and customer.expires_at <= now:
        return "expired"
    return "active"


def is_authorized(customer, now=None):
    """Whether the customer may currently use the project: an active trial, or an active full license.
    A cancelled or expired trial and an inactive license are not authorized. Nothing resets expiry."""
    return state_of(customer, now) == "active"


def register(project, data, user):
    now = timezone.now()
    is_trial = data["license_type"] == ProjectCustomer.TRIAL
    is_year = data["license_type"] == ProjectCustomer.ONE_YEAR
    days = data.get("trial_days") or 3
    key = normalize_key(data["activation_key"])
    key_hash = _hash(key)
    with transaction.atomic():
        request_id = data.get("request_id")
        if request_id:
            pending = RegistrationRequest.objects.select_for_update().filter(pk=request_id, project=project).first()
            if pending is None or pending.status != RegistrationRequest.PENDING:
                raise RuleError("This request has already been processed.")
        updated = IssuedActivationKey.objects.filter(project=project, key_hash=key_hash, used=False).update(used=True)
        if not updated:
            raise RuleError("This activation key is not valid. Generate a new one.")
        customer = ProjectCustomer.objects.create(
            project=project,
            name=data["name"],
            company_name=data.get("company_name", ""),
            license_type=data["license_type"],
            user_id=data["user_id"],
            password_hash=hash_password(data["password"]),
            contact_number=data["contact_number"],
            email=data["email"],
            must_change_password=True,  # the password set here is temporary
            address=data.get("address", ""),
            gstin=data.get("gstin", ""),
            serial_number=data.get("serial_number", ""),
            trial_days=days if is_trial else None,
            activation_key_hash=key_hash,
            activation_key_suffix=key[-4:],
            activated_at=now,
            # Trial: from now (a device product restarts it at first activation). 1 Year: from now, except for device
            # products, where the year starts at first activation. Lifetime: none.
            expires_at=now + timedelta(days=days) if is_trial else (now + timedelta(days=365) if is_year and project not in DEVICE_PROJECTS else None),
            created_by=user,
            updated_by=user,
        )
        detail = f"{customer.get_license_type_display()} license" + (f", {days} days" if is_trial else "")
        audit.record(user, "REGISTERED", f"{customer.name} ({customer.company_name}): {detail}", project, customer)
        request_id = data.get("request_id")
        if request_id:
            RegistrationRequest.objects.filter(pk=request_id, project=project, status=RegistrationRequest.PENDING).update(
                status=RegistrationRequest.PROCESSED, customer=customer, processed_at=now
            )
            audit.record(user, "REQUEST_PROCESSED", f"Registration request #{request_id}", project, customer)
    return customer


_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


def generate_temporary_password(length=10):
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def approve_request(req, data, user):
    """Turns a pending sign-up into a customer in one step: a serial number (= the User ID) and activation key are
    generated unless supplied, the temporary password is generated unless supplied, and the request is marked processed.
    Returns (customer, temporary_password, activation_key)."""
    project = req.project
    password = data.get("temporary_password") or generate_temporary_password()
    serial = data.get("serial_number") or generate_serial_number(project)
    for _ in range(3):  # a generated key/serial can lose a race with another approval; retry rather than fail
        try:
            with transaction.atomic():
                pending = RegistrationRequest.objects.select_for_update().get(pk=req.pk)
                if pending.status != RegistrationRequest.PENDING:
                    raise RuleError("This request has already been processed.")
                key = generate_activation_key(project, user)
                customer = register(
                    project,
                    {
                        "name": req.name,
                        "company_name": data.get("company_name", ""),
                        "license_type": data["license_type"],
                        "user_id": serial,
                        "password": password,
                        "contact_number": req.contact_number,
                        "email": req.email,
                        "address": data.get("address", ""),
                        "gstin": data.get("gstin", ""),
                        "serial_number": serial,
                        "trial_days": data.get("trial_days"),
                        "activation_key": key,
                        "request_id": req.pk,
                    },
                    user,
                )
            return customer, password, key
        except IntegrityError as exc:
            kind, _detail = classify_integrity_error(exc)
            if kind not in ("serial", "user_id", "activation_key"):
                logger.exception("Customer approval refused by the database (%s)", kind)
                raise describe_save_error(exc)
            if data.get("serial_number") and kind != "activation_key":
                raise  # the serial was chosen explicitly: report the conflict, never swap it silently
            serial = data.get("serial_number") or generate_serial_number(project)
    raise RuleError("Could not create the account. Try again.")


def reject_request(req, reason, user):
    """A Super Admin / Admin declines a pending sign-up. No customer, key or licence is ever created for it, and it can
    no longer be approved. The same email may sign up again later (the pending-request uniqueness only covers PENDING)."""
    with transaction.atomic():
        pending = RegistrationRequest.objects.select_for_update().get(pk=req.pk)
        if pending.status != RegistrationRequest.PENDING:
            raise RuleError("This request has already been processed.")
        pending.status = RegistrationRequest.REJECTED
        pending.reject_reason = (reason or "")[:200]
        pending.processed_at = timezone.now()
        pending.save(update_fields=["status", "reject_reason", "processed_at"])
        audit.record(user, "REQUEST_REJECTED", f"Registration request #{pending.pk}", pending.project)
    return pending


def edit(customer, data, user):
    with transaction.atomic():
        customer = _locked(customer)
        data = dict(data)
        password = data.pop("password", None)
        changed = [f for f, v in data.items() if getattr(customer, f) != v]
        for field, value in data.items():
            setattr(customer, field, value)
        if password:
            customer.password_hash = hash_password(password)
            customer.must_change_password = False
            customer.password_changed_at = timezone.now()
            customer.failed_logins, customer.locked_until = 0, None
            changed.append("password")
        customer.updated_by = user
        customer.save()
        audit.record(user, "EDITED", "Changed: " + (", ".join(changed) or "nothing"), customer.project, customer)
        if password:
            from .portal import send_password_changed
            transaction.on_commit(lambda: send_password_changed(customer))
    return customer


def password_status(customer):
    """(code, label) describing the password's state. Nothing about the password itself is revealed."""
    if customer.must_change_password:
        return "temporary", "Temporary – customer must change it"
    if customer.password_changed_at:
        return "customer_set", "Set by customer"
    return "not_recorded", "Set (change date not recorded)"


def reset_password(customer, user, legacy_reference=""):
    """Super Admin reset: a new random temporary password the customer must replace at next sign-in. Also the way a
    legacy local account is linked to its Nexus account — nothing from the legacy side is ever copied.
    Returns the temporary password (to be emailed, and shown once only if the email fails)."""
    password = generate_temporary_password()
    with transaction.atomic():
        customer = _locked(customer)
        customer.password_hash = hash_password(password)
        customer.must_change_password = True
        customer.failed_logins, customer.locked_until = 0, None
        customer.updated_by = user
        customer.save()
        if legacy_reference:
            audit.record(user, "LEGACY_ACCOUNT_LINKED", f"Legacy reference: {legacy_reference}"[:300], customer.project, customer)
        audit.record(user, "PASSWORD_RESET_BY_ADMIN", customer.user_id, customer.project, customer)
    return customer, password


def reissue_activation_key(customer, user):
    """Super Admin: a new Activation Key for an existing customer who lost theirs. The old key stops working at once; the
    password, serial number, licence dates and any already activated computer are untouched. Returns (customer, raw key)."""
    with transaction.atomic():
        customer = _locked(customer)
        if customer.status == ProjectCustomer.INACTIVE:
            raise RuleError("An inactive licence cannot be given a new activation key.")
        raw = generate_activation_key(customer.project, user)
        normalized = normalize_key(raw)
        IssuedActivationKey.objects.filter(project=customer.project, key_hash=_hash(normalized)).update(used=True)
        customer.activation_key_hash, customer.activation_key_suffix = _hash(normalized), normalized[-4:]
        customer.updated_by = user
        customer.save()
        audit.record(user, "ACTIVATION_KEY_REISSUED", f"New key ending {normalized[-4:]}", customer.project, customer)
    return customer, raw


SENDING_STALE_AFTER = timedelta(minutes=10)  # a "Sending" left behind by a crashed request may be claimed again after this


def claim_credentials_email(customer):
    """Database-backed idempotency for the credentials email: one conditional UPDATE marks the customer "sending" (and when).
    Exactly one of any number of concurrent requests wins; the others send nothing. Returns the event id, or "" if another
    request is already sending. The caller must already hold the row lock when used inside a transaction."""
    now = timezone.now()
    won = (
        ProjectCustomer.objects.filter(pk=customer.pk)
        .filter(~Q(credentials_email_status="sending") | Q(credentials_emailed_at__lt=now - SENDING_STALE_AFTER))
        .update(credentials_email_status="sending", credentials_emailed_at=now)
    )
    if not won:
        return ""
    customer.credentials_email_status, customer.credentials_emailed_at = "sending", now
    return f"nexus-cred-{customer.pk}-{int(now.timestamp() * 1000)}"


def reissue_unsent_credentials(customer, user, resend=False):
    """Retry Email / Resend Credentials. A saved password is only a hash and cannot be emailed again, so this issues a
    fresh temporary password and Activation Key for the SAME customer (same serial number / User ID and licence; nothing
    new is created) and the caller emails them. The earlier password and key stop working.
    Retry (resend=False) is refused once the mail server has accepted the credentials, so a double click never sends a
    duplicate; Resend (resend=True) is the deliberate staff action for "the customer never got / lost the email".
    Both are refused once the customer has used the account (use Reset Password then), and while a send is in progress.
    Returns (customer, password, raw key or None, email event id)."""
    with transaction.atomic():
        locked = _locked(customer)
        if locked.credentials_email_status == "accepted" and not resend:
            raise RuleError("The credentials email was already sent to this customer.")
        if locked.first_activated_at or locked.password_changed_at or locked.devices.exists():
            raise RuleError("This customer has already started using the account. Use Reset Password instead.")
        if not locked.email:
            raise RuleError("This customer has no email address.")
        # The Activation Key is only a hash too. If no credentials email ever reached the mail server, nobody holds the key,
        # so a fresh one is issued with the email. Once one was submitted, the key is kept (raw=None): only the password resets.
        if locked.credentials_email_status == "sending" and locked.credentials_emailed_at and (
            timezone.now() - locked.credentials_emailed_at < SENDING_STALE_AFTER
        ):
            raise RuleError("The credentials email is already being sent. Check the status in a moment.")
        key_was_sent = locked.credentials_email_status == "accepted"
        locked, password = reset_password(locked, user)
        raw = None
        if not key_was_sent:
            locked, raw = reissue_activation_key(locked, user)
        # Claimed in the same locked transaction as the new password: a second click waits for this row, then sees "sending".
        event_id = claim_credentials_email(locked)
        if not event_id:
            raise RuleError("The credentials email is already being sent. Check the status in a moment.")
        audit.record(user, "CREDENTIALS_RESENT" if resend else "CREDENTIALS_EMAIL_RETRY", locked.user_id, locked.project, locked)
    return locked, password, raw, event_id


def _locked(customer):
    return ProjectCustomer.objects.select_for_update().get(pk=customer.pk)


def renew_license(customer, user, note=""):
    """Adds one year to a 1-Year licence (counted from its current end, or from today when it has already ended) and
    keeps a history record. The first activation date is never touched."""
    with transaction.atomic():
        customer = _locked(customer)
        if customer.license_type != ProjectCustomer.ONE_YEAR:
            raise RuleError("Only a 1 Year licence is renewed. A trial is extended; a Lifetime licence never expires.")
        if customer.status != ProjectCustomer.ACTIVE:
            raise RuleError("A cancelled or inactive licence cannot be renewed.")
        if customer.expires_at is None:
            raise RuleError("This licence has not been activated yet: its year starts at first activation.")
        previous = customer.expires_at
        customer.expires_at = max(previous, timezone.now()) + timedelta(days=365)
        customer.updated_by = user
        customer.save()
        LicenseRenewal.objects.create(
            customer=customer, previous_expires_at=previous, new_expires_at=customer.expires_at, renewed_by=user, note=note[:200]
        )
        audit.record(user, "LICENSE_RENEWED", f"{previous:%d/%m/%Y} -> {customer.expires_at:%d/%m/%Y}", customer.project, customer)
    return customer


def extend_trial(customer, days, user):
    with transaction.atomic():
        customer = _locked(customer)
        if customer.license_type != ProjectCustomer.TRIAL or state_of(customer) not in ("active", "expired"):
            raise RuleError("Only an active or expired trial can be extended.")
        customer.expires_at = max(customer.expires_at, timezone.now()) + timedelta(days=days)
        customer.updated_by = user
        customer.save()
        audit.record(user, "TRIAL_EXTENDED", f"+{days} days, now valid until {customer.expires_at:%d/%m/%Y}", customer.project, customer)
    return customer


def cancel_trial(customer, user):
    with transaction.atomic():
        customer = _locked(customer)
        if customer.license_type != ProjectCustomer.TRIAL or state_of(customer) != "active":
            raise RuleError("Only an active trial can be cancelled.")
        customer.status = ProjectCustomer.TRIAL_CANCELLED
        customer.updated_by = user
        customer.save()
        audit.record(user, "TRIAL_CANCELLED", "", customer.project, customer)
    return customer


HISTORY_BLOCKED = (
    "This customer has existing license, device or usage history and cannot be permanently deleted. "
    "Revoke access or archive the customer instead."
)
# Records every brand-new customer already has: they do not count as history.
_NEW_CUSTOMER_AUDIT = {"REGISTERED", "KEY_GENERATED", "EDITED", "REQUEST_PROCESSED", "REQUEST_RECEIVED"}


from .models import DeviceRegistration, DeviceTransferRequest, EmailLog, LicenseAuditLog, LicenseRenewal  # noqa: E402


def _has_history(customer, superadmin=False):
    """Superadmin: only real licence/device/usage dependencies block (activation, devices, renewals, transfers, lock). Admin-action
    audit and email rows never block, because they are kept anyway. Otherwise: True when anything beyond the records created with a brand-new customer exists. Checked against stored rows only."""

    if customer.first_activated_at or customer.password_changed_at:
        return True
    if not superadmin and customer.status != ProjectCustomer.ACTIVE:
        return True
    if customer.failed_logins or customer.locked_until or customer.device_blocked:
        return True
    if DeviceRegistration.objects.filter(customer=customer).exists() or LicenseRenewal.objects.filter(customer=customer).exists():
        return True
    if DeviceTransferRequest.objects.filter(Q(from_customer=customer) | Q(to_customer=customer)).exists():
        return True
    if superadmin:
        return False
    if EmailLog.objects.filter(customer=customer).exclude(kind="credentials").exists():
        return True
    return LicenseAuditLog.objects.filter(customer=customer).exclude(action__in=_NEW_CUSTOMER_AUDIT).exists()


def delete_customer(customer, user, superadmin=False):
    """Permanently deletes a new, unused customer only. History check and delete share one transaction on a row lock, so a
    device enrolling at the same moment cannot slip in. Audit, email and sign-up rows are kept (their link is cleared).
    Anything with history raises RuleError and nothing is deleted. No constraint is disabled."""
    from django.db.models import ProtectedError, RestrictedError

    try:
        with transaction.atomic():
            customer = _locked(customer)
            if _has_history(customer, superadmin):
                raise RuleError(HISTORY_BLOCKED)
            label = f"{customer.name} / serial {customer.serial_number or customer.user_id} (id {customer.pk})"
            kept = LicenseAuditLog.objects.filter(customer=customer).count()
            audit.record(user, "CUSTOMER_DELETED", f"{label}; {kept} audit rows kept"[:300], customer.project)
            customer.delete()
    except (ProtectedError, RestrictedError):
        raise RuleError(HISTORY_BLOCKED)
    return label


def inactivate(customer, user):
    with transaction.atomic():
        customer = _locked(customer)
        if customer.status == ProjectCustomer.INACTIVE:
            raise RuleError("This license is already inactive.")
        customer.status = ProjectCustomer.INACTIVE
        customer.updated_by = user
        customer.save()
        audit.record(user, "LICENSE_INACTIVATED", "", customer.project, customer)
    return customer
