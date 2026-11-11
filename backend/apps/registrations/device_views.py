"""Device API for Barcode Auto Print: /api/device/prn-auto-print/...

* `enroll/` takes the customer's credentials (no secret ships in the EXE) and registers the computer's own Ed25519 public key.
* Every later call is signed by that device key: X-Device-Id, X-Device-Timestamp, X-Device-Nonce, X-Device-Signature.
* `public/...` endpoints need no device (sign-up, forgot password, username recovery): rate limited, and they reveal nothing
  about which accounts exist.
Staff endpoints (Super Admin) to see and transfer a customer's device live at the bottom.
"""
import random
import re
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.constants import Action

from . import device, device_crypto as dc, portal, portal_views as pv, services
from .models import DeviceNonce, DeviceRegistration, ProjectClient
from .portal import PortalError
from .views import ProjectView, _rule_error
from .serializers import customer_data

NONCE = re.compile(r"^[A-Za-z0-9_\-+/=]{16,64}$")


def _bad(code="invalid_signature", message="Request signature is not valid.", status_code=401):
    return PortalError(message, status_code, code)


# ---------------------------------------------------------------- device authentication
class DeviceAuthentication(BaseAuthentication):
    """Verifies the signed request, then spends its nonce. Unknown device, bad signature and wrong key all answer the same,
    so the endpoint is no oracle for device ids. A revoked device is told so only after it proved it holds the key."""

    def authenticate(self, request):
        h = request.headers
        device_id, ts, nonce, signature = (h.get(k, "") for k in ("X-Device-Id", "X-Device-Timestamp", "X-Device-Nonce", "X-Device-Signature"))
        if not (device_id and ts and nonce and signature):
            raise _bad("device_auth_required", "Device signature headers are required.")
        try:
            dev = DeviceRegistration.objects.select_related("customer").get(pk=uuid.UUID(device_id))
        except (ValueError, DeviceRegistration.DoesNotExist):
            raise _bad()
        try:
            sent = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            sent = sent if sent.tzinfo else sent.replace(tzinfo=dt_timezone.utc)
        except ValueError:
            raise _bad("bad_timestamp", "Timestamp is not valid.")
        if abs((timezone.now() - sent).total_seconds()) > settings.DEVICE_CLOCK_SKEW_SECONDS:
            raise _bad("timestamp_out_of_range", "The request timestamp is too far from the server time.")
        if not NONCE.fullmatch(nonce):
            raise _bad("bad_nonce", "Nonce is not valid.")
        message = dc.request_message(request.method, request.path, ts, nonce, request._request.body)
        if not dc.verify_request_signature(dev.device_public_key, signature, message):
            raise _bad()
        # Only a correctly signed request may use up a nonce (so strangers cannot fill the table).
        try:
            with transaction.atomic():
                DeviceNonce.objects.create(device=dev, nonce=nonce)
        except IntegrityError:
            raise _bad("replayed_request", "This request was already received.")
        if random.random() < 0.05:
            DeviceNonce.objects.filter(created_at__lt=timezone.now() - timedelta(seconds=settings.DEVICE_NONCE_TTL_SECONDS)).delete()
        if dev.revoked_at is not None:
            raise PortalError("This computer is no longer authorised for the licence.", 403, "device_revoked")
        return (None, dev)

    def authenticate_header(self, request):
        return "Device-Signature"


class IsEnrolledDevice(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, DeviceRegistration) and request.auth.project == view.kwargs.get("project")


class _DeviceProjectView(pv.PortalView):
    """Device licensing exists only for projects in device.DEVICE_PROJECTS; anything else is a 404."""

    def initial(self, request, *args, **kwargs):
        if kwargs.get("project") not in device.DEVICE_PROJECTS:
            raise NotFound("Device licensing is not used by this project.")
        super().initial(request, *args, **kwargs)


class _Open(_DeviceProjectView):
    """Base for calls that need no device."""

    authentication_classes = []
    permission_classes = [AllowAny]


class DeviceView(_DeviceProjectView):
    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsEnrolledDevice]
    throttle_scope = "device"

    def customer(self, request):
        """The signed-in customer's token must have been issued to THIS device and belong to its licence."""
        customer = portal.customer_from_token(
            request.headers.get("X-Customer-Token", ""), self.kwargs["project"], device_id=request.auth.device_id
        )
        if customer.pk != request.auth.customer_id:
            raise PortalError("Invalid or expired token.", 401, "invalid_token")
        return customer


def _client(project):
    return ProjectClient.objects.filter(project=project, active=True).exclude(reset_url="").first()


# ---------------------------------------------------------------- enroll
class EnrollSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=60)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    device_public_key = serializers.CharField(max_length=64)
    fingerprint = serializers.DictField()
    device_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    app_version = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")

    def validate_device_public_key(self, value):
        if not dc.valid_public_key(value):
            raise serializers.ValidationError("Must be the base64 of a raw 32-byte Ed25519 public key.")
        return value

    def validate_fingerprint(self, value):
        if not dc.has_identity(value):
            raise serializers.ValidationError("Provide at least one 64-character hexadecimal hardware hash (smbios, machine or v3).")
        return value


class ActivateSerializer(EnrollSerializer):
    """First-run activation: the issued Serial Number + Activation Key instead of a password."""

    username = None
    password = None
    serial_number = serializers.CharField(max_length=30)
    activation_key = serializers.CharField(max_length=40, trim_whitespace=True)


class ActivateView(_Open):
    throttle_scope = "device_enroll"

    def post(self, request, project):
        serializer = ActivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(device.activate(project, serializer.validated_data))


class TransferRequestSerializer(serializers.Serializer):
    """A customer refused because this computer belongs to another customer: their own credentials + this computer's hardware hashes."""

    username = serializers.CharField(max_length=60)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    fingerprint = serializers.DictField()
    device_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate_fingerprint(self, value):
        if not dc.has_identity(value):
            raise serializers.ValidationError("Provide at least one 64-character hexadecimal hardware hash (smbios, machine or v3).")
        return value


class TransferRequestView(_Open):
    """Ask a Super Admin to move this computer to the signed-in customer's licence. Changes nothing until approved."""

    throttle_scope = "device_enroll"

    def post(self, request, project):
        serializer = TransferRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(device.request_transfer(project, serializer.validated_data), status=status.HTTP_201_CREATED)


class TransferStatusView(_Open):
    throttle_scope = "device_enroll"

    def post(self, request, project):
        serializer = TransferRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(device.transfer_status(project, serializer.validated_data))


class EnrollView(_Open):
    throttle_scope = "device_enroll"

    def post(self, request, project):
        serializer = EnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(device.enroll(project, serializer.validated_data))


# ---------------------------------------------------------------- device-signed calls
class DeviceLoginView(DeviceView):
    def post(self, request, project):
        customer = portal.login(project, request.data.get("username"), request.data.get("password"))
        if customer.pk != request.auth.customer_id:
            raise PortalError("This computer is registered to a different account.", 403, "wrong_device_account")
        data = portal.session_data(customer, request.auth.device_id)
        data["license"] = device.license_view(customer)
        if device.device_license_state(customer, timezone.now()) == "active" and not customer.password_change_required:
            device.record_usage(request.auth, "login")
        return Response(data)


class _SignedIn(DeviceView):
    def signed_in(self, request):
        customer = self.customer(request)
        if customer.password_change_required:
            raise PortalError("Choose a new password before continuing.", 403, "password_change_required")
        return customer


class DeviceLicenseStatusView(_SignedIn):
    def get(self, request, project):
        return Response(device.license_view(self.signed_in(request)))


class DeviceAccountView(_SignedIn):
    def get(self, request, project):
        return Response(portal.account_data(self.signed_in(request)))


class DeviceChangePasswordView(DeviceView):
    def post(self, request, project):
        serializer = pv.NewPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        portal.change_password(self.customer(request), request.data.get("current_password"), serializer.validated_data["new_password"])
        return Response({"message": "Password changed. Sign in again."})


class UsageSerializer(serializers.Serializer):
    event = serializers.ChoiceField(choices=["login", "start", "print", "heartbeat"], default="heartbeat")
    count = serializers.IntegerField(min_value=1, max_value=1000, default=1)
    app_version = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


class UsageView(_SignedIn):
    """The app reports real use (a signed-in session, printed jobs). Only counts for a signed-in customer of this computer
    whose licence is currently active."""

    def post(self, request, project):
        serializer = UsageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.signed_in(request)
        data = serializer.validated_data
        if data["app_version"]:
            DeviceRegistration.objects.filter(pk=request.auth.pk).update(app_version=data["app_version"])
        used = device.record_usage(request.auth, data["event"], data["count"])
        return Response({"recorded": True, "last_used_at": used})


class EntitlementSerializer(serializers.Serializer):
    fingerprint = serializers.DictField()


class EntitlementView(DeviceView):
    """Hourly refresh while online. A revoked device never gets here (403 device_revoked); an expired / cancelled licence gets 403."""

    def post(self, request, project):
        serializer = EntitlementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(device.refresh(request.auth, serializer.validated_data["fingerprint"]))


# The same three recovery calls, signed by an enrolled device or (below) public.
def _register(request, project):
    serializer = pv.RegistrationRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        req = portal.create_request(project, serializer.validated_data)
        return Response({"id": req.id, "status": "pending"}, status=status.HTTP_201_CREATED)
    except PortalError as exc:
        if exc.status != 409:
            raise
        # Duplicate: answer like a success so the endpoint cannot be used to find out who has an account.
        return Response({"status": "pending"}, status=status.HTTP_201_CREATED)


class DeviceRegisterRequestView(DeviceView):
    def post(self, request, project):
        return _register(request, project)


class DeviceForgotPasswordView(DeviceView):
    def post(self, request, project):
        client = _client(project)
        if client:
            portal.request_password_reset(project, request.data.get("identifier"), client)
        return Response({"message": "If the account exists, a password reset link has been emailed."})


class DeviceRecoverUsernameView(DeviceView):
    def post(self, request, project):
        portal.recover_username(project, request.data.get("email"))
        return Response({"message": "If that email is registered, the User ID has been sent to it."})


# ---------------------------------------------------------------- public (no device yet)
class PublicRegisterRequestView(_Open):
    throttle_scope = "device_public"

    def post(self, request, project):
        return _register(request, project)


class PublicRegisterStatusView(_Open):
    throttle_scope = "device_public"

    def post(self, request, project):
        return Response(portal.request_status(project, request.data.get("request_id"), request.data.get("email")))


class PublicForgotPasswordView(_Open):
    throttle_scope = "device_public"

    def post(self, request, project):
        client = _client(project)
        if client:
            portal.request_password_reset(project, request.data.get("identifier"), client)
        return Response({"message": "If the account exists, a password reset link has been emailed."})


class PublicRecoverUsernameView(_Open):
    throttle_scope = "device_public"

    def post(self, request, project):
        portal.recover_username(project, request.data.get("email"))
        return Response({"message": "If that email is registered, the User ID has been sent to it."})


class PublicResetPasswordView(_Open):
    throttle_scope = "device_public"

    def post(self, request, project):
        serializer = pv.NewPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        portal.reset_password(project, request.data.get("token"), serializer.validated_data["new_password"])
        return Response({"message": "Password updated. You can sign in now."})


class PublicKeysView(_Open):
    """The PUBLIC verification key(s) Barcode Auto Print should trust. Convenience only: PRN should ship/pin the key itself."""

    throttle_scope = "device_public"

    def get(self, request, project):
        try:
            return Response({"keys": [{"key_id": settings.LICENSE_KEY_ID, "public_key": dc.public_key_b64(), "algorithm": "Ed25519"}]})
        except dc.SigningUnavailable as exc:
            raise PortalError(str(exc), 503, "licensing_unavailable")


# ---------------------------------------------------------------- staff: view and transfer (Super Admin only)
class _StaffDeviceView(ProjectView):
    required_actions = {"GET": Action.DEVICE_TRANSFER, "POST": Action.DEVICE_TRANSFER}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if kwargs.get("project") not in device.DEVICE_PROJECTS:
            raise NotFound("Device licensing is not used by this project.")


class CustomerDeviceView(_StaffDeviceView):
    def get(self, request, project, pk):
        return Response(device.device_panel(self.customer(pk)))


class RevokeDeviceSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    block = serializers.BooleanField(required=False, default=False)  # also stop any new computer until a transfer


class RevokeDeviceView(_StaffDeviceView):
    """Revoke Device: the enrolled computer stops being authorised at once. With `block` no new computer can be enrolled until
    a Super Admin authorises a transfer; without it the customer may enrol a new computer straight away."""

    def post(self, request, project, pk):
        customer = self.customer(pk)
        serializer = RevokeDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            device.revoke(customer, request.user, serializer.validated_data["reason"].strip(), serializer.validated_data["block"])
        except services.RuleError as exc:
            return _rule_error(exc)
        customer.refresh_from_db()
        return Response({**customer_data(customer), **device.device_panel(customer)})


class TransferRequestListView(_StaffDeviceView):
    """Super Admin: the device transfer requests of this project (?status=pending|approved|rejected)."""

    def get(self, request, project):
        return Response({"results": device.list_transfers(project, request.query_params.get("status"))})


class DecideTransferSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class _DecideTransferView(_StaffDeviceView):
    approve = True

    def post(self, request, project, pk):
        serializer = DecideTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            req = device.decide_transfer(project, pk, request.user, self.approve, serializer.validated_data["reason"].strip())
        except services.RuleError as exc:
            return _rule_error(exc)
        return Response(device.transfer_summary(req))


class ApproveTransferView(_DecideTransferView):
    """Approve: the registration in the way is revoked (kept as history); the requesting customer then registers by signing in."""

    approve = True


class RejectTransferView(_DecideTransferView):
    approve = False


class TransferDeviceView(_StaffDeviceView):
    """Transfer Device: authorises the licence to move to another computer (revokes the current one, lifts a block)."""

    def post(self, request, project, pk):
        customer = self.customer(pk)
        serializer = RevokeDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            device.transfer(customer, request.user, serializer.validated_data["reason"].strip())
        except services.RuleError as exc:
            return _rule_error(exc)
        customer.refresh_from_db()
        return Response({**customer_data(customer), **device.device_panel(customer)})
