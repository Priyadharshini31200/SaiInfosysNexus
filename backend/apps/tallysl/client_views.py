"""Versioned client API for Tally SL products: /api/v1/tally-sl/<product>/...

Callers are the product's local client / agent (e.g. GSTR2Tally). Two credentials:
  X-Product-Key: <key>            identifies the PRODUCT (fixed by the key; must match the URL)
  Authorization: Bearer <token>   a customer session from /auth/login/ or /activate/confirm/
Errors are always {"message": str, "code": str, "errors": {...}}.
"""
import hashlib

from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotFound
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from . import emails, services
from .models import TallyProduct, TallyProductKey

CONTACT = __import__("re").compile(r"\+?[0-9][0-9 \-]{8,18}")


def hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class ProductKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        raw = request.headers.get("X-Product-Key", "")
        key = TallyProductKey.objects.select_related("product").filter(key_hash=hash_key(raw), active=True, product__active=True).first() if raw else None
        if key is None:
            raise AuthenticationFailed("Invalid or missing product key.")
        return (None, key)

    def authenticate_header(self, request):
        return "Product-Key"


class IsProductClient(BasePermission):
    message = "This product key cannot be used for this product."

    def has_permission(self, request, view):
        return isinstance(request.auth, TallyProductKey) and request.auth.product.code == view.kwargs.get("product")


class ClientThrottle(SimpleRateThrottle):
    scope = "tally_client"

    def get_rate(self):
        from django.conf import settings
        return getattr(settings, "TALLY_THROTTLE_RATE", "120/min")

    def get_ident(self, request):
        return services.client_ip(request)  # per real client address, not per proxy

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def _error(message, code, http, errors=None):
    return Response({"message": message, "code": code, "errors": errors or {}}, status=http)


class ClientView(APIView):
    authentication_classes = [ProductKeyAuthentication]
    permission_classes = [IsProductClient]
    throttle_classes = [ClientThrottle]

    @property
    def product(self):
        return self.request.auth.product

    def handle_exception(self, exc):
        if isinstance(exc, services.TallyError):
            return _error(str(exc), exc.code, exc.status, exc.errors)
        if isinstance(exc, serializers.ValidationError):
            response = super().handle_exception(exc)
            response.data["code"] = "validation_error"
            return response
        response = super().handle_exception(exc)
        if isinstance(response.data, dict):
            response.data.setdefault("code", {401: "unauthenticated", 403: "forbidden", 404: "not_found", 429: "throttled"}.get(response.status_code, "error"))
        return response

    def ip(self):
        return services.client_ip(self.request)

    def session(self):
        header = self.request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        return services.session_from_token(self.product, token)


class SignedInView(ClientView):
    """Needs a customer session and a finished first-time password change."""

    def signed_in(self):
        session = self.session()
        if session.customer.must_change_password:
            raise services.TallyError("Choose a new password before continuing.", 403, "password_change_required")
        return session


class DeviceSerializer(serializers.Serializer):
    client_device_id = serializers.CharField(max_length=64)
    fingerprint = serializers.CharField(max_length=512)
    device_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    app_version = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


# ------------------------------------------------------------------ product
class IdentifyView(ClientView):
    def get(self, request, product):
        p = self.product
        return Response({"api_version": "v1", "product": {"code": p.code, "name": p.name, "kind": p.kind},
                         "server_time": timezone.now()})


# ------------------------------------------------------------------ registration & credentials
class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    email = serializers.EmailField(max_length=254)
    contact_number = serializers.CharField(max_length=20)
    tally_serial = serializers.CharField(max_length=30)
    product = serializers.CharField(max_length=40, required=False)

    def validate_contact_number(self, value):
        if not CONTACT.fullmatch(value.strip()):
            raise serializers.ValidationError("Enter a valid contact number")
        return value.strip()


class RegisterView(ClientView):
    def post(self, request, product):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if s.validated_data.get("product", product) != product:
            raise services.TallyError("Product does not match this endpoint.", 400, "product_mismatch")
        customer, raw = services.register_customer(
            self.product, {"name": s.validated_data["name"], "email": s.validated_data["email"],
                           "contact_number": s.validated_data["contact_number"], "tally_serial": s.validated_data["tally_serial"]},
            ip=self.ip(),
        )
        mail = emails.send_welcome(customer, raw)
        emails.notify_superadmins_new_customer(customer)
        return Response({
            "user_id": customer.tally_serial, "status": "registered", "license_status": "pending",
            "message": "Registered. A temporary password has been emailed. Your license will be activated by Sai Infosys.",
            "welcome_email": "submitted" if mail == "accepted" else mail,
        }, status=status.HTTP_201_CREATED)


class LoginSerializer(serializers.Serializer):
    user_id = serializers.CharField(max_length=60)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    device = DeviceSerializer(required=False)


class LoginView(ClientView):
    def post(self, request, product):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        return Response(services.login(self.product, d["user_id"], d["password"], d.get("device"), self.ip()))


class LogoutView(ClientView):
    def post(self, request, product):
        session = self.session()
        session.revoked_at, session.revoked_reason = timezone.now(), "logout"
        session.save(update_fields=["revoked_at", "revoked_reason"])
        return Response({"message": "Signed out."})


class RefreshView(ClientView):
    """Short-lived token rotation: exchange a valid session token for a new one (the old one stops working)."""

    def post(self, request, product):
        session = self.session()
        return Response({"token": services.refresh_session(session), "expires_in": services.conf("TALLY_SESSION_MAX_AGE", 7 * 86400)})


class ForgotPasswordView(ClientView):
    def post(self, request, product):
        found = services.request_password_reset(self.product, request.data.get("identifier") or request.data.get("email"))
        if found:
            emails.send_password_reset(*found)
        return Response({"message": "If the account exists, a password reset link and code have been emailed to the registered address."})


class NewPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)


class ResetPasswordView(ClientView):
    def post(self, request, product):
        s = NewPasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        customer = services.reset_password(self.product, request.data.get("token"), s.validated_data["new_password"])
        emails.send_password_changed(customer)
        return Response({"message": "Password updated. You can sign in now."})


class ChangePasswordView(ClientView):
    def post(self, request, product):
        session = self.session()
        s = NewPasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.change_password(session.customer, request.data.get("current_password"), s.validated_data["new_password"])
        emails.send_password_changed(session.customer)
        return Response({"message": "Password changed. Sign in again."})


# ------------------------------------------------------------------ session / licence
class SessionView(ClientView):
    """Cheap poll for online clients: 200 while the session is valid, 401 `session_revoked` the moment it is not."""

    def get(self, request, product):
        session = self.session()
        return Response({"valid": True, "must_change_password": session.customer.must_change_password,
                         "license": services.license_payload(session.customer, services.session_device(session))})


class LicenseStatusView(SignedInView):
    def get(self, request, product):
        session = self.signed_in()
        return Response(services.license_payload(session.customer, services.session_device(session)))


class AccountView(SignedInView):
    def get(self, request, product):
        return Response(services.account_data(self.signed_in().customer))


class TallyInfoSerializer(serializers.Serializer):
    edition = serializers.CharField(max_length=60, required=False, allow_blank=True)
    tss_details = serializers.CharField(max_length=300, required=False, allow_blank=True)
    virtual_user_limit = serializers.IntegerField(min_value=0, max_value=100000, required=False, allow_null=True)
    license_administrator = serializers.CharField(max_length=150, required=False, allow_blank=True)
    gateway_server = serializers.CharField(max_length=200, required=False, allow_blank=True)
    registered_email = serializers.CharField(max_length=254, required=False, allow_blank=True)
    tss_expiry_date = serializers.DateField(required=False, allow_null=True)
    verified_fields = serializers.ListField(child=serializers.CharField(), required=False)


class TallyInfoView(SignedInView):
    def post(self, request, product):
        session = self.signed_in()
        s = TallyInfoSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        info = services.sync_tally_info(session.customer, services.session_device(session), s.validated_data)
        import json
        return Response({"message": "Tally details saved.", "availability": json.loads(info.availability)})


class UsageOpenView(SignedInView):
    def post(self, request, product):
        session = self.signed_in()
        device = services.session_device(session)
        payload = services.license_payload(session.customer, device)
        if not payload["authorized"]:
            raise services.TallyError("This license is not authorized on this device.", 403, "not_authorized", {"state": [payload["state"]]})
        recorded = services.record_open(session.customer, device, "open", str(request.data.get("app_version", ""))[:30])
        return Response({"recorded": recorded, "license": payload})


# ------------------------------------------------------------------ Activate User (replacement laptop)
class ActivationCodeView(ClientView):
    def post(self, request, product):
        found = services.request_activation_code(self.product, request.data.get("user_id"), self.ip())
        if found:
            emails.send_activation_code(*found)
        return Response({"message": "If the account exists, a verification code has been sent to the registered email."})


class ActivationVerifySerializer(serializers.Serializer):
    user_id = serializers.CharField(max_length=60)
    code = serializers.CharField(max_length=20)
    activation_key = serializers.CharField(max_length=40)


class ActivationVerifyView(ClientView):
    def post(self, request, product):
        s = ActivationVerifySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        return Response(services.verify_activation(self.product, d["user_id"], d["code"], d["activation_key"], self.ip()))


class ActivationConfirmSerializer(serializers.Serializer):
    ticket = serializers.CharField(max_length=200)
    device = DeviceSerializer()
    confirm_transfer = serializers.BooleanField(required=False, default=False)


class ActivationConfirmView(ClientView):
    def post(self, request, product):
        s = ActivationConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        result, customer, replaced = services.confirm_activation(self.product, d["ticket"], d["device"], d["confirm_transfer"], self.ip())
        if replaced:
            emails.send_device_replaced(customer, d["device"].get("device_name", ""))
        return Response(result)
