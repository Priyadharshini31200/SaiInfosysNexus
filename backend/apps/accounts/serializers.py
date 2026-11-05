from rest_framework import serializers

from . import services
from .passwords import check_user_password
from .permissions import permission_map


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def validate(self, attrs):
        user = self.context["request"].user
        errors = {}
        if not check_user_password(user, attrs["current_password"]):
            errors["current_password"] = ["Current password is incorrect."]
        if attrs["new_password"] != attrs["confirm_password"]:
            errors["confirm_password"] = ["New password and confirmation do not match."]
        if "current_password" not in errors and "confirm_password" not in errors:
            problems = services.validate_new_password(user, attrs["current_password"], attrs["new_password"])
            if problems:
                errors["new_password"] = problems
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class CurrentUserSerializer(serializers.Serializer):
    """Read-only representation of the signed-in user. Never includes credentials."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    role = serializers.SerializerMethodField()
    role_label = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    def get_role(self, user):
        return user.profile.role

    def get_role_label(self, user):
        return user.profile.get_role_display()

    def get_permissions(self, user):
        return permission_map(user)



class AdminPermissionAddSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=60,
        error_messages={
            "blank": "Enter a permission name.",
            "required": "Enter a permission name.",
            "max_length": "Permission name must be 60 characters or fewer.",
        },
    )

    def validate_name(self, value):
        value = services.clean_permission_name(value)
        if not value:
            raise serializers.ValidationError("Enter a permission name.")
        return value


class AdminPermissionToggleSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
