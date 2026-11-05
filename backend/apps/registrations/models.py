import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

PROJECTS = {"gstr2tally": "GSTR2Tally", "prn-auto-print": "Barcode Auto Print"}
# Projects whose licence is enforced on the customer's computer through signed device entitlements.
DEVICE_PROJECTS = ("prn-auto-print",)


class IssuedActivationKey(models.Model):
    """A generated activation key. Only its SHA-256 hash is stored (plus the last 4 characters for display);
    the key itself is shown once, when generated. A key is consumed by exactly one registration."""

    project = models.CharField(max_length=40)
    key_hash = models.CharField(max_length=64, unique=True)
    key_suffix = models.CharField(max_length=4)
    used = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class ProjectCustomer(models.Model):
    """A customer registered for ONE project, with that project's license. Records are per project:
    the same person registered for two projects has two independent rows."""

    # LICENSED is the Lifetime package (no fixed expiry); it keeps its stored value so no existing record changes.
    TRIAL, ONE_YEAR, LICENSED = "TRIAL", "ONE_YEAR", "LICENSED"
    LICENSE_TYPES = [(TRIAL, "Trial"), (ONE_YEAR, "1 Year"), (LICENSED, "Lifetime")]
    ACTIVE, TRIAL_CANCELLED, INACTIVE = "ACTIVE", "TRIAL_CANCELLED", "INACTIVE"

    project = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=100)
    company_name = models.CharField(max_length=150)
    license_type = models.CharField(max_length=10, choices=LICENSE_TYPES)
    user_id = models.CharField(max_length=60)
    password_hash = models.CharField(max_length=256)
    contact_number = models.CharField(max_length=20)
    email = models.EmailField(blank=True, default="")
    must_change_password = models.BooleanField(default=False)  # set while the password is a temporary one

    @property
    def password_change_required(self):
        """Whether sign-in must stop at a password change: the temporary password AND the deployment enforces the change."""
        from django.conf import settings
        return bool(self.must_change_password and getattr(settings, "ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE", False))
    # When the CUSTOMER last chose their own password (change or emailed reset). Null = never, or a legacy account
    # from before this was recorded. Informational only; the password itself is only ever stored as a hash.
    password_changed_at = models.DateTimeField(null=True, blank=True)
    # Set once, at the first successful device enrollment, and never overwritten: the start of the PRN validity period.
    first_activated_at = models.DateTimeField(null=True, blank=True)
    credentials_email_status = models.CharField(max_length=20, blank=True, default="", db_default="")  # accepted | not_delivered | failed
    credentials_emailed_at = models.DateTimeField(null=True, blank=True)  # when a real mail server accepted the credentials email
    device_blocked = models.BooleanField(default=False, db_default=False)  # a Super Admin revoked the device: no new computer until a transfer
    failed_logins = models.PositiveSmallIntegerField(default=0, db_default=0)  # consecutive wrong passwords; reset on success
    locked_until = models.DateTimeField(null=True, blank=True)
    address = models.CharField(max_length=500)
    gstin = models.CharField(max_length=15, blank=True, default="")
    serial_number = models.CharField(max_length=30, blank=True, default="")
    trial_days = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, default=ACTIVE)
    activation_key_hash = models.CharField(max_length=64, unique=True)
    activation_key_suffix = models.CharField(max_length=4)
    activated_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)  # trial end; null for a full license
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "user_id"], name="uniq_customer_user_id_per_project"),
            models.UniqueConstraint(
                fields=["project", "serial_number"], condition=Q(serial_number__gt=""), name="uniq_customer_serial_per_project"
            ),
        ]


class LicenseRenewal(models.Model):
    """One 1-Year renewal. The history is kept; the customer's own expiry moves forward only through this record."""

    customer = models.ForeignKey(ProjectCustomer, on_delete=models.CASCADE, related_name="renewals")
    previous_expires_at = models.DateTimeField()
    new_expires_at = models.DateTimeField()
    renewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class LicenseAuditLog(models.Model):
    """Append-only record of license and permission changes."""

    project = models.CharField(max_length=40, blank=True, default="")
    customer = models.ForeignKey(ProjectCustomer, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=40)
    detail = models.CharField(max_length=300, blank=True, default="")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class RegistrationRequest(models.Model):
    """A sign-up submitted from a connected project. It never creates a license: a Super Admin / Admin turns it
    into a customer through Add Customer, which marks it processed."""

    PENDING, PROCESSED, REJECTED = "PENDING", "PROCESSED", "REJECTED"

    project = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=20)
    email = models.EmailField()  # stored lower-case
    status = models.CharField(max_length=10, default=PENDING)
    reject_reason = models.CharField(max_length=200, blank=True, default="", db_default="")
    customer = models.ForeignKey(ProjectCustomer, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "email"], condition=Q(status="PENDING"), name="uniq_pending_request_per_project_email"
            ),
        ]


class EmailLog(models.Model):
    """One row per email Nexus tried to send: who, what kind, how many attempts and how it ended. The body is never
    stored (it can hold a temporary password); `error` holds the exception text only."""

    SENT, NOT_DELIVERED, FAILED = "accepted", "not_delivered", "failed"

    kind = models.CharField(max_length=30, db_index=True)  # credentials | activation_key | password_reset_admin | ...
    project = models.CharField(max_length=40, blank=True, default="")
    customer = models.ForeignKey(ProjectCustomer, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    recipient = models.CharField(max_length=254)
    subject = models.CharField(max_length=200)
    status = models.CharField(max_length=20)
    attempts = models.PositiveSmallIntegerField(default=1)
    error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class ProjectClient(models.Model):
    """The credential a connected project's backend uses to call the portal API. Only a hash of the key is stored;
    the project it belongs to comes from here, never from anything the caller sends."""

    project = models.CharField(max_length=40, db_index=True)
    label = models.CharField(max_length=80, blank=True, default="")
    key_hash = models.CharField(max_length=64, unique=True)
    reset_url = models.URLField(max_length=300, blank=True, default="")  # where password-reset links point
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DeviceRegistration(models.Model):
    """The one computer a customer's licence is enrolled on. Holds the device's PUBLIC key and keyed (HMAC) hardware
    hashes only; the device's private key and the agent's own hashes never reach Nexus storage. Revoking (a Super Admin
    transfer) keeps the row as history; the customer's next enrollment gets generation + 1."""

    device_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(ProjectCustomer, on_delete=models.CASCADE, related_name="devices")
    project = models.CharField(max_length=40, db_index=True)
    device_public_key = models.CharField(max_length=64)  # base64 of the raw 32-byte Ed25519 public key
    fingerprint_hashes = models.TextField()  # JSON {smbios, machine, v3}: HMAC-protected
    device_name = models.CharField(max_length=100, blank=True, default="")
    app_version = models.CharField(max_length=30, blank=True, default="")
    generation = models.PositiveIntegerField(default=1, db_default=1)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)  # any signed contact, including background refreshes
    last_used_at = models.DateTimeField(null=True, blank=True)  # the app was really used (signed in / reported usage)
    use_count = models.PositiveIntegerField(default=0, db_default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-enrolled_at"]
        constraints = [
            # One active device per licence, enforced by the database as well as the service.
            models.UniqueConstraint(fields=["customer"], condition=Q(revoked_at__isnull=True), name="uniq_active_device_per_customer"),
        ]


class DeviceTransferRequest(models.Model):
    """A customer asks to use a computer that is currently registered to ANOTHER customer's licence. Nothing changes until a Super
    Admin approves: approval revokes the previous registration (kept as history) and the requester then registers normally.
    Rows are never deleted by the application; every step is also written to LicenseAuditLog."""

    PENDING, APPROVED, REJECTED = "PENDING", "APPROVED", "REJECTED"

    project = models.CharField(max_length=40, db_index=True)
    device = models.ForeignKey(DeviceRegistration, on_delete=models.CASCADE, related_name="transfer_requests")  # the registration in the way
    from_customer = models.ForeignKey(ProjectCustomer, on_delete=models.CASCADE, related_name="transfers_out")
    to_customer = models.ForeignKey(ProjectCustomer, on_delete=models.CASCADE, related_name="transfers_in")
    requesting_device_name = models.CharField(max_length=100, blank=True, default="", db_default="")
    status = models.CharField(max_length=10, default=PENDING, db_default=PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=150, blank=True, default="", db_default="")
    decision_reason = models.CharField(max_length=200, blank=True, default="", db_default="")

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["device", "to_customer"], condition=Q(status="PENDING"), name="uniq_pending_transfer_per_device_customer"),
        ]


class DeviceNonce(models.Model):
    """A request nonce a device has already used, so a captured signed request cannot be replayed. Pruned after the TTL."""

    device = models.ForeignKey(DeviceRegistration, on_delete=models.CASCADE, related_name="+")
    nonce = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "nonce"], name="uniq_device_nonce")]
