from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.authentication import CsrfEnforcedSessionAuthentication
from apps.registrations import audit

from . import services
from .models import AdminPermission
from .permissions import IsAdminOrSuperAdmin, IsSuperAdmin
from .serializers import (
    AdminPermissionAddSerializer,
    AdminPermissionToggleSerializer,
    ChangePasswordSerializer,
    CurrentUserSerializer,
    LoginSerializer,
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    authentication_classes = [CsrfEnforcedSessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = services.authenticate_user(request, **serializer.validated_data)
        except services.InvalidCredentials:
            return Response(
                {"message": "Invalid username or password.", "errors": {}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        login(request, user)  # rotates the session key
        return Response({"user": CurrentUserSerializer(user).data})


class LogoutView(APIView):
    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request):
        logout(request)
        return Response({"message": "Signed out."})


class MeView(APIView):
    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request):
        return Response({"user": CurrentUserSerializer(request.user).data})


class ChangePasswordView(APIView):
    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        services.set_password(request.user, serializer.validated_data["new_password"])
        # The password hash changed, so Django rejects every other session of this user.
        # End this one too: the user must sign in again.
        logout(request)
        return Response({"message": "Password changed. Please sign in again."})


def _admin_permissions_payload():
    """The global Admin Permissions list."""
    return {"permissions": services.list_admin_permissions()}


class AdminPermissionsView(APIView):
    """Configuration: list the Admin Permissions (GET) or add a custom heading (POST)."""

    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response(_admin_permissions_payload())

    def post(self, request):
        serializer = AdminPermissionAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            entry = services.add_admin_permission(serializer.validated_data["name"])
        except services.DuplicatePermission:
            return Response(
                {"message": "This permission already exists.", "errors": {"name": ["This permission already exists."]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        audit.record(request.user, "PERMISSION_ADDED", f"{entry.name} (ON)")
        return Response(_admin_permissions_payload(), status=status.HTTP_201_CREATED)


class AdminPermissionDetailView(APIView):
    """Switch one listed permission ON (grant) or OFF (revoke) for the Admin role, in all projects."""

    permission_classes = [IsSuperAdmin]

    def patch(self, request, pk):
        serializer = AdminPermissionToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not services.set_admin_permission(pk, serializer.validated_data["enabled"]):
            raise NotFound("Unknown permission.")
        enabled = serializer.validated_data["enabled"]
        audit.record(request.user, "PERMISSION_ON" if enabled else "PERMISSION_OFF", AdminPermission.objects.get(pk=pk).name)
        return Response(_admin_permissions_payload())
