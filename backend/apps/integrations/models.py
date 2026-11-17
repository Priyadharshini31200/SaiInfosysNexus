"""Universal, product-scoped licensing for connected (non-Tally) projects. Entirely separate from the PRN / GSTR tables."""
import json

from django.db import models

DEFAULT_RULES = {"new_days": 7, "speed_min_days": 15}


class IntegrationProject(models.Model):
    SETUP_PENDING, ACTIVE, DISABLED = "SETUP_PENDING", "ACTIVE", "DISABLED"
    # An integration is only VERIFIED (status ACTIVE) once the project's own backend has really exercised these calls.
    REQUIRED_STEPS = ("register", "auth_verify", "activation_verify", "session_introspect", "usage_event")

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    frontend_url = models.URLField(max_length=300)
    backend_url = models.URLField(max_length=300)
    auth_method = models.CharField(max_length=40, default="JWT Bearer")
    framework = models.CharField(max_length=60, blank=True, default="")
    health_path = models.CharField(max_length=100, blank=True, default="/health")
    registration_method = models.CharField(max_length=30, default="API")
    tally_serial_required = models.BooleanField(default=False)
    serial_prefix = models.CharField(max_length=4, default="9")
    serial_length = models.PositiveSmallIntegerField(default=9)
    activation_key_length = models.PositiveSmallIntegerField(default=9)
    device_limit = models.PositiveSmallIntegerField(default=1)
    default_license_type = models.CharField(max_length=10, default="TRIAL")  # TRIAL | ONE_YEAR | LIFETIME
    trial_days = models.PositiveSmallIntegerField(default=30)
    transfer_policy = models.CharField(max_length=20, default="SELF")  # SELF = customer confirms on the new device | DISABLED
    auto_sync = models.BooleanField(default=True)
    usage_tracking = models.BooleanField(default=True)
    welcome_email_mode = models.CharField(max_length=20, default="ON_REGISTER")
    activation_email_mode = models.CharField(max_length=20, default="ON_REQUEST")
    # explicit, ordered, non-overlapping usage classes (see usage.classify)
    usage_rules = models.TextField(default=json.dumps(DEFAULT_RULES))
    status = models.CharField(max_length=15, default=SETUP_PENDING)
    enabled = models.BooleanField(default=True)
    verified_steps = models.TextField(default="[]")
    last_sync_status = models.CharField(max_length=20, blank=True, default="")
    last_sync_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    @property
    def rules(self):
        try:
            return {**DEFAULT_RULES, **json.loads(self.usage_rules)}
        except ValueError:
            return dict(DEFAULT_RULES)

    @property
    def steps(self):
        try:
            return list(json.loads(self.verified_steps))
        except ValueError:
            return []


class IntegrationCredential(models.Model):
    """Server-to-server credential of ONE project. Only a SHA-256 of the (high-entropy) secret is stored."""

    project = models.ForeignKey(IntegrationProject, on_delete=models.CASCADE, related_name="credentials")
    client_id = models.CharField(max_length=40, unique=True)
    secret_hash = models.CharField(max_length=64)
    secret_hint = models.CharField(max_length=6, blank=True, default="")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class IntegrationCustomer(models.Model):
    project = models.ForeignKey(IntegrationProject, on_delete=models.PROTECT, related_name="customers")
    external_customer_id = models.CharField(max_length=100)
    name = models.CharField(max_length=150)
    email = models.EmailField()
    contact_number = models.CharField(max_length=20)
    serial_number = models.CharField(max_length=20, unique=True)
    user_id = models.CharField(max_length=20, unique=True)
    password_hash = models.CharField(max_length=256)
    must_change_password = models.BooleanField(default=True)
    failed_logins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    welcome_email_status = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "external_customer_id"], name="uniq_int_customer_external"),
            models.UniqueConstraint(fields=["project", "email"], name="uniq_int_customer_email"),
        ]


class IntegrationLicense(models.Model):
    TRIAL, ONE_YEAR, LIFETIME = "TRIAL", "ONE_YEAR", "LIFETIME"
    PENDING, ACTIVE, SUSPENDED, CANCELLED = "PENDING_APPROVAL", "ACTIVE", "SUSPENDED", "CANCELLED"

    customer = models.OneToOneField(IntegrationCustomer, on_delete=models.CASCADE, related_name="license")
    license_type = models.CharField(max_length=10)
    status = models.CharField(max_length=20)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # null = lifetime / not yet approved
    created_at = models.DateTimeField(auto_now_add=True)


class IntegrationActivationKey(models.Model):
    license = models.ForeignKey(IntegrationLicense, on_delete=models.CASCADE, related_name="activation_keys")
    key_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    # set when verification succeeded but another device holds the licence: awaiting the customer's confirmation
    transfer_hash = models.CharField(max_length=64, blank=True, default="")
    pending_device_id = models.CharField(max_length=120, blank=True, default="")
    pending_device_name = models.CharField(max_length=150, blank=True, default="")
    pending_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class IntegrationSession(models.Model):
    ACTIVE, REVOKED = "ACTIVE", "REVOKED"
    license = models.ForeignKey(IntegrationLicense, on_delete=models.CASCADE, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    device_id = models.CharField(max_length=120)
    device_name = models.CharField(max_length=150, blank=True, default="")
    status = models.CharField(max_length=10, default=ACTIVE)
    revoked_reason = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class IntegrationIdempotency(models.Model):
    project = models.ForeignKey(IntegrationProject, on_delete=models.CASCADE)
    endpoint = models.CharField(max_length=60)
    key = models.CharField(max_length=100)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response = models.TextField()  # never contains a password, activation key or session token
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "endpoint", "key"], name="uniq_int_idempotency")]


class IntegrationAudit(models.Model):
    project = models.ForeignKey(IntegrationProject, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60)
    subject = models.CharField(max_length=120, blank=True, default="")
    ok = models.BooleanField(default=True)
    detail = models.CharField(max_length=300, blank=True, default="")
    actor = models.CharField(max_length=80, blank=True, default="")
    ip = models.CharField(max_length=45, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class IntegrationUsageEvent(models.Model):
    project = models.ForeignKey(IntegrationProject, on_delete=models.CASCADE)
    license = models.ForeignKey(IntegrationLicense, on_delete=models.CASCADE, related_name="usage_events")
    session = models.ForeignKey(IntegrationSession, null=True, blank=True, on_delete=models.SET_NULL)
    device_id = models.CharField(max_length=120, blank=True, default="")
    event_type = models.CharField(max_length=20)
    occurred_at = models.DateTimeField(db_index=True)
    event_id = models.CharField(max_length=100)  # client-supplied (retries are idempotent) or generated

    class Meta:
        indexes = [models.Index(fields=["license", "occurred_at"])]
        constraints = [models.UniqueConstraint(fields=["project", "event_id"], name="uniq_int_usage_event")]


class IntegrationSyncLog(models.Model):
    project = models.ForeignKey(IntegrationProject, on_delete=models.CASCADE)
    external_customer_id = models.CharField(max_length=100, blank=True, default="")
    event = models.CharField(max_length=30)
    status = models.CharField(max_length=10)  # OK | FAILED | RETRIED
    error_code = models.CharField(max_length=40, blank=True, default="")
    error = models.CharField(max_length=300, blank=True, default="")
    payload = models.TextField(blank=True, default="")  # sync fields only; never a credential
    retries = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
