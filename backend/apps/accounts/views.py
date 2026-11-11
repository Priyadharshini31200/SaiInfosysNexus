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
        from django.utils import timezone
        request.session["authenticated_at"] = timezone.now().timestamp()
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


class ProjectPermissionsView(APIView):
    """Only Superadmin can replace one Admin's scoped grants. Legacy rows are preserved."""
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        from django.contrib.auth import get_user_model
        from .permissions import scoped_permission_map
        from .constants import Action
        from apps.registrations.models import PROJECTS, DEVICE_PROJECTS
        admins = get_user_model().objects.filter(profile__role="ADMIN", is_active=True).order_by("username")
        return Response({
            "admins": [{"id": u.pk, "username": u.username, "grants": scoped_permission_map(u)} for u in admins],
            "projects": [{"code": code, "label": label, "menus": [
                {"module": f"{code}:dashboard", "label": "Dashboard", "actions": ["view"]},
                {"module": code, "label": "Customer / Add Customer / Customer List", "actions": [a.value for a in Action if a != Action.REPORTS and (a != Action.DEVICE_TRANSFER or code in DEVICE_PROJECTS)]},
                {"module": f"{code}:expiry", "label": "Reports / License Expiry Report", "actions": ["reports", "export_print"]},
                {"module": f"{code}:usage", "label": "Reports / Usage Report", "actions": ["reports", "export_print"]},
            ]} for code, label in PROJECTS.items()],
            "actions": {a.value: a.label for a in Action},
        })

    def put(self, request):
        from django.contrib.auth import get_user_model
        from django.db import transaction
        from rest_framework.exceptions import ValidationError
        from .constants import Action
        from .models import UserModulePermission
        from .permissions import permission_modules
        try:
            target = get_user_model().objects.get(pk=request.data.get("user_id"), profile__role="ADMIN", is_active=True)
        except (get_user_model().DoesNotExist, ValueError, TypeError):
            raise ValidationError({"user_id": ["Choose an active Admin account."]})
        grants = request.data.get("grants")
        if not isinstance(grants, list) or len(grants) > len(permission_modules()):
            raise ValidationError({"grants": ["Send the project permission list."]})
        seen = set()
        for grant in grants:
            if not isinstance(grant, dict) or grant.get("module") not in permission_modules():
                raise ValidationError({"grants": ["Unknown module."]})
            actions = grant.get("actions")
            if grant["module"] in seen or not isinstance(actions, list) or any(a not in Action.values for a in actions):
                raise ValidationError({"grants": ["Invalid or duplicate grant."]})
            seen.add(grant["module"])
        with transaction.atomic():
            # Lock the target to serialize concurrent permission replacements.
            get_user_model().objects.select_for_update().get(pk=target.pk)
            for module in permission_modules():
                actions = next((g["actions"] for g in grants if g["module"] == module), [])
                UserModulePermission.objects.update_or_create(user=target, module=module,
                    defaults={f"can_{a.value}": a.value in actions for a in Action})
            audit.record(request.user, "PROJECT_PERMISSIONS_UPDATED", f"Admin account {target.pk}")
        return self.get(request)


class AdminPasswordResetView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        from django.contrib.auth import get_user_model
        from rest_framework import serializers
        class PasswordInput(serializers.Serializer):
            new_password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
            confirm_password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
        serializer = PasswordInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if not values["new_password"].strip():
            raise serializers.ValidationError({"new_password": ["Password is required."]})
        if values["new_password"] != values["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        try:
            target = get_user_model().objects.get(pk=pk, profile__role="ADMIN")
        except get_user_model().DoesNotExist:
            raise NotFound("Admin account not found.")
        services.set_password(target, values["new_password"])
        audit.record(request.user, "ADMIN_PASSWORD_RESET", f"Admin account {target.pk}")
        return Response({"message": "Password updated. Existing sessions are invalidated."})
