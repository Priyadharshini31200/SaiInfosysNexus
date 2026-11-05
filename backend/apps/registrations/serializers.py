import re

from rest_framework import serializers

from . import services
from .portal import EMAIL_STATUS_LABELS
from .models import ProjectCustomer, RegistrationRequest

GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PHONE = re.compile(r"^[0-9]{10}$")
CONTACT = PHONE
SERIAL = re.compile(r"^7[0-9]{8}$")
SERIAL_MESSAGE = "Serial Number must contain exactly 9 digits and start with 7."
USER_ID = re.compile(r"^[A-Za-z0-9._@-]{3,60}$")


def _display(customer):
    state = services.state_of(customer)
    if state == "inactive":
        return state, "Inactive"
    if state == "cancelled":
        return state, "Trial Cancelled"
    if customer.license_type == ProjectCustomer.TRIAL:
        return state, "Trial Expired" if state == "expired" else "Trial Active"
    if customer.license_type == ProjectCustomer.ONE_YEAR:
        return state, "1 Year Expired" if state == "expired" else "1 Year Active"
    return state, "Lifetime Active"


def customer_data(customer):
    """Read representation. Never includes the password hash or the full activation key."""
    state, label = _display(customer)
    password_status_code, password_status_label = services.password_status(customer)
    return {
        "id": customer.id,
        "name": customer.name,
        "company_name": customer.company_name,
        "license_type": customer.license_type,
        "license_type_label": customer.get_license_type_display(),
        "user_id": customer.user_id,
        "contact_number": customer.contact_number,
        "email": customer.email,
        "address": customer.address,
        "gstin": customer.gstin,
        "serial_number": customer.serial_number,
        "trial_days": customer.trial_days,
        "state": state,
        "status_label": label,
        "password_status": password_status_code,
        "password_status_label": password_status_label,
        "password_changed_at": customer.password_changed_at,
        "credentials_email_status": customer.credentials_email_status,
        "credentials_email_status_label": EMAIL_STATUS_LABELS.get(customer.credentials_email_status, "Not sent"),
        "credentials_emailed_at": customer.credentials_emailed_at,
        "activation_key_hint": f"••••-••••-••••-{customer.activation_key_suffix}",
        "activated_at": customer.activated_at,
        "expires_at": customer.expires_at,
        "effective_expires_at": services.effective_expiry(customer),
        "created_by": customer.created_by.username if customer.created_by else "",
        "updated_by": customer.updated_by.username if customer.updated_by else "",
        "created_at": customer.created_at,
        "updated_at": customer.updated_at,
    }


class _ProfileFields(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    company_name = serializers.CharField(max_length=150)
    contact_number = serializers.CharField(max_length=20)
    email = serializers.EmailField(max_length=254)
    address = serializers.CharField(max_length=500)
    gstin = serializers.CharField(max_length=15)
    serial_number = serializers.CharField(max_length=30)

    def validate_contact_number(self, value):
        value = value.strip()
        if not PHONE.fullmatch(value):
            raise serializers.ValidationError("Enter a valid 10-digit mobile number")
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        taken = ProjectCustomer.objects.filter(project=self.context["project"], email__iexact=value)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError("This email is already registered for this project.")
        return value

    def validate_gstin(self, value):
        value = value.strip().upper()
        if not GSTIN.match(value):
            raise serializers.ValidationError("Enter a valid 15-character GSTIN.")
        return value

    def validate_serial_number(self, value):
        value = value.strip()
        if not SERIAL.fullmatch(value):
            raise serializers.ValidationError(SERIAL_MESSAGE)
        if self.instance is None:
            return value  # a new customer: a number already in use is replaced with a fresh unique one when saving
        taken = ProjectCustomer.objects.filter(project=self.context["project"], serial_number=value).exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError("This serial number is already registered for this project.")
        return value


class CustomerEditSerializer(_ProfileFields):
    """PATCH: the profile fields, plus an optional new password. License type, key, user ID and trial days are fixed.
    A field that is sent must still be valid and non-blank."""

    password = serializers.CharField(max_length=128, write_only=True, required=False, trim_whitespace=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False



class CustomerRegisterSerializer(_ProfileFields):
    license_type = serializers.ChoiceField(choices=[ProjectCustomer.TRIAL, ProjectCustomer.ONE_YEAR, ProjectCustomer.LICENSED])
    activation_key = serializers.CharField(max_length=40)
    user_id = serializers.CharField(max_length=60, required=False)  # always the serial number; any value sent is ignored
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)
    trial_days = serializers.IntegerField(min_value=1, max_value=365, required=False)
    request_id = serializers.IntegerField(required=False)  # the sign-up request this registration answers

    def validate_request_id(self, value):
        if not RegistrationRequest.objects.filter(pk=value, project=self.context["project"], status=RegistrationRequest.PENDING).exists():
            raise serializers.ValidationError("This registration request is no longer pending.")
        return value

    def validate(self, attrs):
        """The customer's login User ID is their Serial Number. A number that is already taken is not an error: saving
        issues a new unique one (see services.register_with_unique_serial) and never touches the existing customer."""
        attrs["user_id"] = attrs["serial_number"]
        return attrs

    def validate_activation_key(self, value):
        if not services.key_is_available(self.context["project"], value):
            raise serializers.ValidationError("This activation key is not valid. Generate a new one.")
        return value


class ResetPasswordSerializer(serializers.Serializer):
    legacy_reference = serializers.CharField(max_length=200, required=False, allow_blank=True)


class ExtendTrialSerializer(serializers.Serializer):
    days = serializers.IntegerField(min_value=1, max_value=365)


class RejectRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class ApproveRequestSerializer(serializers.Serializer):
    """Everything beyond what the customer typed at sign-up. Serial number and temporary password are generated when omitted."""

    license_type = serializers.ChoiceField(choices=[ProjectCustomer.TRIAL, ProjectCustomer.ONE_YEAR, ProjectCustomer.LICENSED])
    trial_days = serializers.IntegerField(min_value=1, max_value=365, required=False)
    serial_number = serializers.CharField(max_length=30, required=False)
    temporary_password = serializers.CharField(max_length=128, required=False, trim_whitespace=False)
    company_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    address = serializers.CharField(max_length=500, required=False, allow_blank=True)
    gstin = serializers.CharField(max_length=15, required=False, allow_blank=True)

    def validate_serial_number(self, value):
        value = value.strip()
        if not SERIAL.fullmatch(value):
            raise serializers.ValidationError(SERIAL_MESSAGE)
        if ProjectCustomer.objects.filter(project=self.context["project"], user_id__iexact=value).exists():
            raise serializers.ValidationError("This serial number is already registered for this project.")
        return value

    def validate_gstin(self, value):
        value = value.strip().upper()
        if value and not GSTIN.match(value):
            raise serializers.ValidationError("Enter a valid 15-character GSTIN.")
        return value
