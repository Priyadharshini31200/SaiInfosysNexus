"""Device enrollment and signed entitlement issuance (see docs/DEVICE-LICENSING.md).

Rules enforced here:
* One active device per licence. A second computer is refused (409 device_limit); only a Super Admin can free the licence.
* Three packages. Trial: first activation + the product's trial days (PRN 7), or longer after an authorised extension.
  1 Year: one year from first activation, moved on only by an authorised renewal. Lifetime: no expiry.
* `first_activated_at` is set once, at the first enrollment, and never overwritten; neither is it touched by reinstalling.
* The signed lease (`valid_until`) is separate from the licence's validity: at most LICENSE_LEASE_DAYS (7), renewed by every
  online refresh for as long as the licence itself is valid.
"""
import hmac
import json
import logging
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from . import audit, device_crypto as dc, portal, services
from .models import DEVICE_PROJECTS, DeviceRegistration, DeviceTransferRequest, ProjectCustomer
from .portal import PortalError

PRODUCT = "prn-auto-print"
logger = logging.getLogger(__name__)

_STATE_ERRORS = {
    "expired": ("Your licence has expired.", "license_expired"),
    "cancelled": ("Your trial was cancelled.", "license_cancelled"),
    "inactive": ("This account is inactive.", "account_inactive"),
}


def _iso(dt):
    return dt.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


# ---------------------------------------------------------------- licence state as seen by a device
LEASE_MAX_DAYS = 7
LICENSE_CODES = {"TRIAL": "TRIAL", "ONE_YEAR": "ONE_YEAR", "LICENSED": "LIFETIME"}


def license_code(customer):
    """The package name PRN sees: TRIAL, ONE_YEAR or LIFETIME."""
    return LICENSE_CODES.get(customer.license_type, customer.license_type)


def lease_days():
    return max(1, min(settings.LICENSE_LEASE_DAYS, LEASE_MAX_DAYS))


def effective_expiry(customer):
    return services.effective_expiry(customer)


def device_license_state(customer, now):
    """active | expired | cancelled | inactive. A trial that no device has activated yet has not started, so it is active."""
    if customer.status == ProjectCustomer.INACTIVE:
        return "inactive"
    if customer.status == ProjectCustomer.TRIAL_CANCELLED:
        return "cancelled"
    expiry = effective_expiry(customer)
    if expiry and now >= expiry:
        return "expired"
    return "active"


def license_view(customer, now=None):
    now = now or timezone.now()
    state = device_license_state(customer, now)
    expiry = effective_expiry(customer)
    lease_until = None
    if state == "active":
        lease_until = now + timedelta(days=lease_days())
        if expiry and expiry < lease_until:
            lease_until = expiry
    return {
        "authorized": state == "active",
        "state": state,
        "license_type": license_code(customer),
        "expires_at": expiry,
        "checked_at": now,
        "lease_until": lease_until,
    }


def ensure_active(customer, now):
    state = device_license_state(customer, now)
    if state != "active":
        message, code = _STATE_ERRORS[state]
        raise PortalError(message, 403, code)


def ensure_signing():
    try:
        dc._private_key()
    except dc.SigningUnavailable as exc:
        raise PortalError(str(exc), 503, "licensing_unavailable")


# ---------------------------------------------------------------- entitlement
def build_entitlement(customer, device, fingerprint, now):
    """Signs the licence for THIS device. The hashes in the payload are the ones the device just presented (and that
    were verified against the stored protected copy): the agent compares them with its own computation."""
    fp = dc.normalize_fp(fingerprint)
    expiry = effective_expiry(customer)
    valid_until = now + timedelta(days=lease_days())
    if expiry:
        valid_until = min(valid_until, expiry)
    payload = {
        "v": 1,
        "product": PRODUCT,
        "serial": customer.serial_number or customer.user_id,
        "customer_id": customer.pk,
        "device_id": str(device.device_id),
        "license_type": license_code(customer),
        "status": "ACTIVE",
        "activated_at": _iso(customer.first_activated_at),
        "expires_at": _iso(expiry),
        "purchased_at": _iso(customer.activated_at),
        "issued_at": _iso(now),
        "valid_until": _iso(valid_until),
        "device": fp["smbios"] or fp["machine"],
        "device_machine": fp["machine"],
        "generation": device.generation,
        "device_name": device.device_name,
    }
    if fp["v3"]:
        payload["device_v3"] = fp["v3"]
    try:
        return dc.sign_entitlement(payload)
    except dc.SigningUnavailable as exc:
        raise PortalError(str(exc), 503, "licensing_unavailable")


# ---------------------------------------------------------------- enrollment
def active_device(customer):
    return customer.devices.filter(revoked_at__isnull=True).first()


def _start_clock(customer, now):
    """Runs once, at the very first activation. A trial runs for the product's trial period from now (never shorter than
    an expiry it already has, e.g. an authorised extension); a 1-Year licence runs one year from now. Lifetime has none."""
    if customer.license_type == ProjectCustomer.TRIAL:
        days = services.trial_period_days(customer.project)
        if days:
            end = now + timedelta(days=days)
            ProjectCustomer.objects.filter(pk=customer.pk).update(expires_at=max(customer.expires_at, end) if customer.expires_at else end)
    elif customer.license_type == ProjectCustomer.ONE_YEAR and customer.expires_at is None:
        ProjectCustomer.objects.filter(pk=customer.pk).update(expires_at=now + timedelta(days=365))


def conflicting_device(project, customer, fingerprint):
    """The ACTIVE registration of another customer's licence that belongs to the computer presenting `fingerprint`, or None."""
    others = DeviceRegistration.objects.filter(project=project, revoked_at__isnull=True).exclude(customer_id=customer.pk).select_related("customer")
    for registration in others:
        if dc.matches_stored(registration.fingerprint_hashes, fingerprint):
            return registration
    return None


ACTIVATION_PENDING = ("Your first activation is waiting for Sai Infosys Super Admin approval. Sign in again after it has been approved.")
ACTIVATION_REJECTED = "The activation request for this computer was rejected. Please contact Sai Infosys."


def approval_required():
    return bool(getattr(settings, "DEVICE_ACTIVATION_APPROVAL_REQUIRED", True))


def _first_activation_request(project, locked, data, protected):
    """The waiting FIRST_ACTIVATION request for this licence (created on the first attempt, refreshed with the newest key afterwards, never
    duplicated) - or a PortalError when this very computer was already rejected."""
    pending = DeviceTransferRequest.objects.select_for_update().filter(
        to_customer=locked, kind=DeviceTransferRequest.FIRST_ACTIVATION, status=DeviceTransferRequest.PENDING).first()
    if pending is not None:
        pending.device_public_key, pending.fingerprint_hashes = data["device_public_key"], protected
        pending.requesting_device_name, pending.app_version = data["device_name"][:100], data["app_version"][:30]
        pending.save(update_fields=["device_public_key", "fingerprint_hashes", "requesting_device_name", "app_version"])
        return pending
    rejected = DeviceTransferRequest.objects.filter(to_customer=locked, kind=DeviceTransferRequest.FIRST_ACTIVATION,
                                                    status=DeviceTransferRequest.REJECTED).order_by("-id").first()
    if rejected is not None and dc.matches_stored(rejected.fingerprint_hashes, data["fingerprint"]):
        return rejected
    req = DeviceTransferRequest.objects.create(
        project=project, kind=DeviceTransferRequest.FIRST_ACTIVATION, to_customer=locked, requesting_device_name=data["device_name"][:100],
        device_public_key=data["device_public_key"], fingerprint_hashes=protected, app_version=data["app_version"][:30])
    audit.record(None, "DEVICE_ACTIVATION_REQUESTED", f"{data['device_name']} (request #{req.pk})", project, locked)
    return req


BOUND_TO_OTHER = ("This computer is already registered to another customer account. Ask for a device transfer, which a Sai Infosys "
                  "Super Admin must approve.")


def _bind(project, customer, data, now):
    """Registers the presented computer for the licence (or re-registers the same one after a reinstall).
    Returns the DeviceRegistration and the refreshed customer."""
    fingerprint = data["fingerprint"]
    protected = json.dumps(dc.protect_fp(fingerprint), sort_keys=True)
    waiting = None                  # a first-activation request that must be answered after the transaction (so it is kept)
    try:
        with transaction.atomic():
            locked = ProjectCustomer.objects.select_for_update().get(pk=customer.pk)
            current = active_device(locked)
            if locked.device_blocked:
                raise PortalError("This licence's device was revoked. Contact Sai Infosys.", 403, "device_blocked")
            if conflicting_device(project, locked, fingerprint) is not None:     # never register a computer that belongs to someone else
                raise PortalError(BOUND_TO_OTHER, 403, "device_bound_to_other")
            if current is not None:
                if not dc.matches_stored(current.fingerprint_hashes, fingerprint):
                    raise PortalError(
                        "This licence is already registered on another computer. Contact Sai Infosys to move your licence.",
                        409,
                        "device_limit",
                    )
                # Reinstall of the same computer: new device key, same registration, generation and expiry.
                current.device_public_key = data["device_public_key"]
                current.fingerprint_hashes = protected
                current.device_name = data["device_name"]
                current.app_version = data["app_version"]
                current.last_seen_at = now
                current.save()
                device, action = current, "DEVICE_REENROLLED"
            elif approval_required() and not locked.devices.exists():
                waiting = _first_activation_request(project, locked, data, protected)     # the licence's very first computer: Super Admin decides
            else:
                generation = max(locked.devices.values_list("generation", flat=True), default=0) + 1
                device = DeviceRegistration.objects.create(
                    customer=locked,
                    project=project,
                    device_public_key=data["device_public_key"],
                    fingerprint_hashes=protected,
                    device_name=data["device_name"],
                    app_version=data["app_version"],
                    generation=generation,
                    last_seen_at=now,
                )
                action = "DEVICE_ENROLLED"
            if waiting is None:
                # The validity period starts once. The conditional update cannot overwrite an existing value.
                if ProjectCustomer.objects.filter(pk=locked.pk, first_activated_at__isnull=True).update(first_activated_at=now):
                    _start_clock(locked, now)
                locked.refresh_from_db()
                audit.record(None, action, f"{device.device_name} (generation {device.generation})", project, locked)
    except IntegrityError:  # lost a race with another enrollment of the same licence
        raise PortalError("This licence is already registered on another computer. Contact Sai Infosys to move your licence.", 409, "device_limit")
    if waiting is not None:
        if waiting.status == DeviceTransferRequest.REJECTED:
            raise PortalError(ACTIVATION_REJECTED, 403, "activation_rejected")
        raise PortalError(ACTIVATION_PENDING, 403, "activation_pending")
    return device, locked


def enroll(project, data):
    """Username + password of an approved account, plus the new device's public key and hardware hashes."""
    ensure_signing()
    customer = portal.login(project, data["username"], data["password"])  # includes the per-account lockout
    # A customer still on the emailed temporary password may enroll this computer: the credentials prove who they are, and the
    # token returned below can do nothing but change the password (every other signed-in call answers password_change_required
    # until they have). The app opens only after the new password has been chosen and the customer has signed in again.
    now = timezone.now()
    ensure_active(customer, now)
    device, locked = _bind(project, customer, data, now)
    return {
        "device_id": str(device.device_id),
        "token": portal.issue_token(locked, device.device_id),
        "account": portal.account_data(locked),
        "license": license_view(locked, now),
        "entitlement": build_entitlement(locked, device, data["fingerprint"], now),
        "server_time": now,
    }


def activate(project, data):
    """First-run activation with the issued Serial Number + Activation Key (the temporary password is not needed yet).
    Binds the licence to this computer and returns the signed entitlement; the customer then signs in with User ID + password.
    Any wrong serial / key answers the same way, so the call cannot be used to find out which serials exist."""
    ensure_signing()
    serial = (data["serial_number"] or "").strip()
    key_hash = services._hash(services.normalize_key(data["activation_key"]))
    customer = ProjectCustomer.objects.filter(project=project, serial_number=serial).first() if serial else None
    if customer is None or not hmac.compare_digest(customer.activation_key_hash, key_hash):
        # Redacted on purpose: the reason, the project and the LAST 3 characters of the serial only - never the key.
        logger.warning(
            "activation rejected project=%s reason=%s serial=...%s key_chars=%d",
            project, "unknown_serial" if customer is None else "key_mismatch", serial[-3:], len(services.normalize_key(data["activation_key"])),
        )
        raise PortalError("The serial number or activation key is not correct.", 401, "invalid_activation")
    now = timezone.now()
    ensure_active(customer, now)
    device, locked = _bind(project, customer, data, now)
    return {
        "device_id": str(device.device_id),
        "account": {"user_id": locked.user_id, "serial_number": locked.serial_number, "must_change_password": locked.password_change_required},
        "license": license_view(locked, now),
        "entitlement": build_entitlement(locked, device, data["fingerprint"], now),
        "server_time": now,
    }


def record_usage(device, event="use", count=1):
    """The app reports it was really used (signed in, printed). Updates the device's last-used time and activity count.
    A device whose licence is not currently active is refused, so it is never counted as in use."""
    ensure_active(device.customer, timezone.now())
    now = timezone.now()
    DeviceRegistration.objects.filter(pk=device.pk).update(
        last_used_at=now, last_seen_at=now, use_count=F("use_count") + max(1, min(int(count), 1000))
    )
    return now


def refresh(device, fingerprint):
    """New entitlement for an enrolled device. Same activated_at / expires_at; valid_until moves forward only while active."""
    ensure_signing()
    customer = device.customer
    now = timezone.now()
    if not dc.matches_stored(device.fingerprint_hashes, fingerprint):
        raise PortalError("This licence is registered on another computer.", 403, "device_mismatch")
    ensure_active(customer, now)
    DeviceRegistration.objects.filter(pk=device.pk).update(last_seen_at=now)
    return {
        "entitlement": build_entitlement(customer, device, fingerprint, now),
        "license": license_view(customer, now),
        "server_time": now,
    }


# ---------------------------------------------------------------- Super Admin revoke / transfer
def revoke(customer, user, reason="", block=False):
    """Revokes the enrolled computer: its next signed call is refused. With block=True no other computer may be enrolled
    either until a transfer is authorised; otherwise the licence is simply freed for the customer's new computer."""
    with transaction.atomic():
        locked = ProjectCustomer.objects.select_for_update().get(pk=customer.pk)
        current = active_device(locked)
        if current is None:
            raise services.RuleError("This customer has no enrolled device.")
        current.revoked_at = timezone.now()
        current.revoked_reason = reason[:200]
        current.save(update_fields=["revoked_at", "revoked_reason"])
        if block:
            ProjectCustomer.objects.filter(pk=locked.pk).update(device_blocked=True)
        audit.record(user, "DEVICE_REVOKED", f"{current.device_name}: {reason}"[:300], locked.project, locked)
        # The decision is its own request type in the history. It is created already APPROVED and never binds or approves anything.
        DeviceTransferRequest.objects.create(
            project=locked.project, kind=DeviceTransferRequest.REVOKE, device=current, from_customer=locked, to_customer=locked,
            status=DeviceTransferRequest.APPROVED, decided_at=current.revoked_at, decided_by=getattr(user, "username", "")[:150],
            decision_reason=reason[:200])
    return current


def transfer(customer, user, reason=""):
    """Authorises moving the licence to a new computer: revokes the current one (if any) and lifts a block. The customer's
    next enrollment on the new computer gets generation + 1. Expiry and the first activation date are untouched."""
    with transaction.atomic():
        locked = ProjectCustomer.objects.select_for_update().get(pk=customer.pk)
        current = active_device(locked)
        if current is None and not locked.device_blocked and not locked.devices.exists():
            raise services.RuleError("This customer has no device to transfer.")
        if current is not None:
            current.revoked_at = timezone.now()
            current.revoked_reason = (reason or "Transfer")[:200]
            current.save(update_fields=["revoked_at", "revoked_reason"])
        ProjectCustomer.objects.filter(pk=locked.pk).update(device_blocked=False)
        audit.record(user, "DEVICE_TRANSFER_AUTHORIZED", f"{current.device_name if current else 'no active device'}: {reason}"[:300], locked.project, locked)
    return current


# ---------------------------------------------------------------- device transfer between customers (Super Admin approval)
def transfer_summary(r):
    """Safe description for the requester and the Nexus UI: ids, names, dates. No keys, no hashes."""
    source = r.from_customer
    return {
        "id": r.pk,
        "kind": r.kind.lower(),
        "status": r.status.lower(),
        "from_user_id": source.user_id if source else "",
        "from_name": source.name if source else "",
        "to_user_id": r.to_customer.user_id,
        "to_name": r.to_customer.name,
        "device_id": str(r.device_id) if r.device_id else "",
        "device_name": r.device.device_name if r.device_id else r.requesting_device_name,
        "requesting_device_name": r.requesting_device_name,
        "requested_at": r.requested_at,
        "decided_at": r.decided_at,
        "decided_by": r.decided_by,
        "decision_reason": r.decision_reason,
    }


def _requester(project, data):
    """The customer proves who they are (their own Nexus credentials, with the usual lockout); the licence must be usable."""
    customer = portal.login(project, data["username"], data["password"])
    ensure_active(customer, timezone.now())
    return customer


def own_other_device(customer, fingerprint):
    """The customer's OWN active registration when the presenting computer is a different one (licence already on another PC), else None."""
    current = active_device(customer)
    if current is not None and not dc.matches_stored(current.fingerprint_hashes, fingerprint):
        return current
    return None


def request_transfer(project, data):
    """A refused customer asks to use this computer: either it belongs to ANOTHER customer's licence, or the customer's OWN licence is
    active on a different computer (moving the licence to a new PC). Both need the customer's password and a Super Admin decision.
    Idempotent: asking again returns the same pending request."""
    customer = _requester(project, data)
    for _ in range(2):
        try:
            with transaction.atomic():
                locked = ProjectCustomer.objects.select_for_update().get(pk=customer.pk)
                other = conflicting_device(project, locked, data["fingerprint"])
                own = other is None
                if own:
                    other = own_other_device(locked, data["fingerprint"])
                if other is None:
                    raise PortalError("This computer does not need a transfer. Sign in again.", 409, "no_transfer_needed")
                if locked.device_blocked:
                    raise PortalError("This licence's device was revoked. Contact Sai Infosys.", 403, "device_blocked")
                if not own and active_device(locked) is not None:
                    raise PortalError("This licence is already registered on another computer. Contact Sai Infosys to move your licence.", 409, "device_limit")
                req = DeviceTransferRequest.objects.filter(device=other, to_customer=locked, status=DeviceTransferRequest.PENDING).first()
                if req is None:
                    key = data.get("device_public_key", "")
                    keyed = bool(key) and dc.valid_public_key(key)
                    req = DeviceTransferRequest.objects.create(
                        project=project, kind=DeviceTransferRequest.TRANSFER, device=other, from_customer=other.customer, to_customer=locked,
                        requesting_device_name=data.get("device_name", "")[:100],
                        device_public_key=key if keyed else "",
                        fingerprint_hashes=json.dumps(dc.protect_fp(data["fingerprint"]), sort_keys=True) if keyed else "",
                        app_version=data.get("app_version", "")[:30])
                    audit.record(None, "DEVICE_TRANSFER_REQUESTED",
                                 f"{other.device_name}: {other.customer.user_id} -> {locked.user_id} (request #{req.pk})"
                                 + (" [own licence, new computer]" if own else ""), project, locked)
                return transfer_summary(req)
        except IntegrityError:        # two identical requests at the same moment: the second finds the first
            continue
    raise PortalError("Could not record the transfer request. Try again.", 409, "transfer_conflict")


def transfer_status(project, data):
    """Where this customer's request for this computer stands. Needs the customer's credentials, so it tells nobody else anything."""
    customer = _requester(project, data)
    first = DeviceTransferRequest.objects.filter(to_customer=customer, kind=DeviceTransferRequest.FIRST_ACTIVATION).order_by("-id").first()
    if first is not None and active_device(customer) is None and dc.matches_stored(first.fingerprint_hashes, data["fingerprint"]):
        return {"needed": True, "kind": "first_activation", "state": first.status.lower(), "id": first.pk, "decided_at": first.decided_at,
                "decision_reason": first.decision_reason}
    other = conflicting_device(project, customer, data["fingerprint"]) or own_other_device(customer, data["fingerprint"])
    if other is None:
        return {"needed": False, "state": "none"}
    req = DeviceTransferRequest.objects.filter(device=other, to_customer=customer).order_by("-id").first()
    if req is None:
        return {"needed": True, "state": "none"}
    return {"needed": True, "state": req.status.lower(), "id": req.pk, "decided_at": req.decided_at, "decision_reason": req.decision_reason}


def list_transfers(project, status=None, kind=None):
    qs = DeviceTransferRequest.objects.filter(project=project).select_related("from_customer", "to_customer", "device")
    if status:
        qs = qs.filter(status=status.upper())
    if kind:
        qs = qs.filter(kind=kind.upper())
    return [transfer_summary(r) for r in qs[:200]]


def _bind_approved(req, to, now):
    """Approval of a request that carries the requesting computer's key: bind it to the licence in the caller's transaction. The
    registration gets generation + 1, the validity period starts once (existing rules), and the audit trail says who approved it."""
    device = DeviceRegistration.objects.create(
        customer=to, project=req.project, device_public_key=req.device_public_key, fingerprint_hashes=req.fingerprint_hashes,
        device_name=req.requesting_device_name, app_version=req.app_version,
        generation=max(to.devices.values_list("generation", flat=True), default=0) + 1, last_seen_at=now)
    if ProjectCustomer.objects.filter(pk=to.pk, first_activated_at__isnull=True).update(first_activated_at=now):
        _start_clock(to, now)
    return device


def decide_transfer(project, pk, user, approve, reason=""):
    """Super Admin decision (only the Super Admin API reaches this).
    FIRST_ACTIVATION: approval binds the requesting computer to the licence and starts the validity period; rejection binds nothing.
    TRANSFER: approval revokes the blocking registration (kept as history) and, when the request carries the new computer's key,
    binds it in the same transaction; otherwise the requester registers by signing in. Nothing is deleted."""
    with transaction.atomic():
        req = DeviceTransferRequest.objects.select_for_update().filter(pk=pk, project=project).first()
        if req is None:
            raise services.RuleError("Transfer request not found.")
        if req.kind == DeviceTransferRequest.REVOKE:
            raise services.RuleError("A revoke is recorded history, not a request that can be decided.")
        if req.status != DeviceTransferRequest.PENDING:
            raise services.RuleError("This request has already been decided.")
        to = ProjectCustomer.objects.select_for_update().get(pk=req.to_customer_id)
        now = timezone.now()
        reason = (reason or "")[:200]
        first = req.kind == DeviceTransferRequest.FIRST_ACTIVATION
        registration = None if first else DeviceRegistration.objects.select_for_update().get(pk=req.device_id)
        if approve:
            if to.device_blocked:
                raise services.RuleError("The requesting customer's device is blocked. Authorise a transfer for that customer first.")
            if first:
                if to.devices.exists():
                    raise services.RuleError("This customer already has a registered computer; this activation request is obsolete.")
                try:
                    ensure_active(to, now)
                except PortalError as exc:
                    raise services.RuleError(str(exc))
                stored = json.loads(req.fingerprint_hashes or "{}")
                for other in DeviceRegistration.objects.filter(project=project, revoked_at__isnull=True).exclude(customer_id=to.pk):
                    if dc.fingerprints_match(json.loads(other.fingerprint_hashes), stored):
                        raise services.RuleError("This computer is registered to another customer. Request a device transfer instead.")
                bound = _bind_approved(req, to, now)
                audit.record(user, "DEVICE_ENROLLED", f"{bound.device_name} (generation {bound.generation}) approved (request #{req.pk})", project, to)
            else:
                own = req.from_customer_id == req.to_customer_id        # the customer moves their OWN licence to a new computer
                if not own and active_device(to) is not None:
                    raise services.RuleError("The requesting customer already has a registered computer.")
                if registration.revoked_at is None:
                    registration.revoked_at = now
                    registration.revoked_reason = (f"Transferred to {to.user_id} (request #{req.pk})")[:200]
                    registration.save(update_fields=["revoked_at", "revoked_reason"])
                    audit.record(user, "DEVICE_REVOKED", f"{registration.device_name}: transferred to {to.user_id} (request #{req.pk})", project, req.from_customer)
                if req.device_public_key and req.fingerprint_hashes and active_device(to) is None:
                    bound = _bind_approved(req, to, now)
                    audit.record(user, "DEVICE_ENROLLED", f"{bound.device_name} (generation {bound.generation}) via transfer #{req.pk}", project, to)
        req.status = DeviceTransferRequest.APPROVED if approve else DeviceTransferRequest.REJECTED
        req.decided_at, req.decided_by, req.decision_reason = now, getattr(user, "username", "")[:150], reason
        req.save(update_fields=["status", "decided_at", "decided_by", "decision_reason"])
        what = "ACTIVATION" if first else "TRANSFER"
        detail = (f"{req.requesting_device_name} for {to.user_id} (request #{req.pk}) {reason}" if first else
                  f"{registration.device_name}: {req.from_customer.user_id} -> {to.user_id} (request #{req.pk}) {reason}")
        audit.record(user, f"DEVICE_{what}_APPROVED" if approve else f"DEVICE_{what}_REJECTED", detail, project, to)
    return req


def device_status(customer, current):
    """(code, label) of the customer's device for the Nexus drawer."""
    if customer.device_blocked:
        return "revoked", "Revoked"
    if current is not None:
        return "active", "Active"
    if customer.devices.exists():
        return "transfer_pending", "Awaiting new device"
    if customer.transfers_in.filter(kind=DeviceTransferRequest.FIRST_ACTIVATION, status=DeviceTransferRequest.PENDING).exists():
        return "activation_pending", "Awaiting activation approval"
    return "not_activated", "Not activated"


def device_panel(customer):
    """Everything the drawer's Device Management section shows. No keys, no hashes."""
    devices = list(customer.devices.all()[:10])
    current = next((d for d in devices if d.revoked_at is None), None)
    shown = current or (devices[0] if devices else None)
    code, label = device_status(customer, current)
    last_used = max((d.last_used_at for d in devices if d.last_used_at), default=None)
    return {
        "device": device_summary(shown),
        "history": [device_summary(d) for d in devices if d.revoked_at is not None],
        "device_status": code,
        "device_status_label": label,
        "first_activated_at": customer.first_activated_at,
        "last_used_at": last_used,
        "license_type": license_code(customer),
        "license_type_label": customer.get_license_type_display(),
        "effective_expires_at": effective_expiry(customer),
        "transfer_requests": [transfer_summary(r) for r in DeviceTransferRequest.objects.filter(
            status=DeviceTransferRequest.PENDING).filter(Q(from_customer=customer) | Q(to_customer=customer)).select_related(
            "from_customer", "to_customer", "device")[:10]],
        "can_revoke": current is not None,
        "can_transfer": current is not None or customer.device_blocked or bool(devices),
    }


def device_summary(device):
    """Safe description for the Nexus UI: no keys and no hashes."""
    if device is None:
        return None
    return {
        "device_id": str(device.device_id),
        "device_name": device.device_name,
        "app_version": device.app_version,
        "generation": device.generation,
        "enrolled_at": device.enrolled_at,
        "last_seen_at": device.last_seen_at,
        "last_used_at": device.last_used_at,
        "use_count": device.use_count,
        "revoked_at": device.revoked_at,
        "revoked_reason": device.revoked_reason,
    }
