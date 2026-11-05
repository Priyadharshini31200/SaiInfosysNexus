"""Portal API for connected projects: /api/portal/<project>/...

Callers are the projects' backends, authenticated by a per-project key in `X-Project-Key` (see ProjectKeyAuthentication).
The project is fixed by the key and must match the URL, so one project's key can never act on another's customers.
Signed-in customer calls also carry the customer's token in `X-Customer-Token`.
"""
import hashlib

from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotFound
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import portal
from .models import PROJECTS, ProjectClient
from .serializers import CONTACT


def hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class ProjectKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        raw = request.headers.get("X-Project-Key", "")
        client = ProjectClient.objects.filter(key_hash=hash_key(raw), active=True).first() if raw else None
        if client is None:
            raise AuthenticationFailed("Invalid or missing project key.")
        return (None, client)

    def authenticate_header(self, request):
        return "Project-Key"


class IsProjectClient(BasePermission):
    message = "This project key cannot be used for this project."

    def has_permission(self, request, view):
        return isinstance(request.auth, ProjectClient) and request.auth.project == view.kwargs.get("project")


class PortalView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectClient]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "portal"

    def initial(self, request, *args, **kwargs):
        if kwargs.get("project") not in PROJECTS:
            raise NotFound("Unknown project.")
        super().initial(request, *args, **kwargs)

    def handle_exception(self, exc):
        if isinstance(exc, portal.PortalError):
            return Response({"message": str(exc), "code": exc.code, "errors": {}}, status=exc.status)
        return super().handle_exception(exc)

    def customer(self, request):
        return portal.customer_from_token(request.headers.get("X-Customer-Token", ""), self.kwargs["project"])


class RegistrationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    contact_number = serializers.CharField(max_length=20)
    email = serializers.EmailField(max_length=254)

    def validate_contact_number(self, value):
        if not CONTACT.fullmatch(value.strip()):
            raise serializers.ValidationError("Enter a valid 10-digit mobile number")
        return value.strip()


class RegisterRequestView(PortalView):
    def post(self, request, project):
        serializer = RegistrationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        req = portal.create_request(project, serializer.validated_data)
        return Response({"id": req.id, "status": "pending"}, status=status.HTTP_201_CREATED)


class RegisterRequestStatusView(PortalView):
    def post(self, request, project):
        return Response(portal.request_status(project, request.data.get("request_id"), request.data.get("email")))


class LoginView(PortalView):
    def post(self, request, project):
        customer = portal.login(project, request.data.get("username"), request.data.get("password"))
        return Response(portal.session_data(customer))


class RecoverUsernameView(PortalView):
    def post(self, request, project):
        portal.recover_username(project, request.data.get("email"))
        return Response({"message": "If that email is registered, the User ID has been sent to it."})


class ForgotPasswordView(PortalView):
    def post(self, request, project):
        portal.request_password_reset(project, request.data.get("identifier"), request.auth)
        return Response({"message": "If the account exists, a password reset link has been emailed."})


class NewPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)


class ResetPasswordView(PortalView):
    def post(self, request, project):
        serializer = NewPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        portal.reset_password(project, request.data.get("token"), serializer.validated_data["new_password"])
        return Response({"message": "Password updated. You can sign in now."})


class ChangePasswordView(PortalView):
    def post(self, request, project):
        serializer = NewPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        portal.change_password(self.customer(request), request.data.get("current_password"), serializer.validated_data["new_password"])
        return Response({"message": "Password changed. Sign in again."})


class _SignedInView(PortalView):
    """Needs a valid customer token, and a finished first-time password change."""

    def signed_in(self, request):
        customer = self.customer(request)
        if customer.password_change_required:
            raise portal.PortalError("Choose a new password before continuing.", 403, "password_change_required")
        return customer


class LicenseStatusView(_SignedInView):
    def get(self, request, project):
        return Response(portal.license_status(self.signed_in(request)))


class AccountView(_SignedInView):
    def get(self, request, project):
        return Response(portal.account_data(self.signed_in(request)))
