from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .passwords import check_user_password, normalize, set_user_password


class CaseInsensitiveModelBackend(ModelBackend):
    """Matches usernames AND passwords case-insensitively ('admin' == 'Admin')."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or password is None:
            return None
        User = get_user_model()
        user = User.objects.filter(**{f"{User.USERNAME_FIELD}__iexact": username}).first()
        if user is None:
            User().set_password(normalize(password))  # equalise timing to avoid username enumeration
            return None
        if check_user_password(user, password) and self.user_can_authenticate(user):
            if not user.check_password(normalize(password)):  # legacy exact-case hash: upgrade it now that the real text is known
                set_user_password(user, password)
                user.save(update_fields=["password"])
            return user
        return None
