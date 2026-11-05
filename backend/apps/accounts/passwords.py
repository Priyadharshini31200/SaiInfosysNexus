"""Staff password rules: any non-empty password, case-insensitive. Passwords are normalised (casefolded) before hashing and
before checking, exactly like customer passwords (registrations.services). Only the Django hash is ever stored."""
from django.contrib.auth.hashers import check_password


def normalize(raw):
    return (raw or "").casefold()


def set_user_password(user, raw):
    user.set_password(normalize(raw))


def check_user_password(user, raw):
    """True when `raw` matches, ignoring case. A hash saved before passwords became case-insensitive was made from the exact
    text, so that exact text still verifies; the caller then re-hashes it (see CaseInsensitiveModelBackend) so every later
    sign-in is case-insensitive. A hash that cannot be matched either way needs a password reset - nothing is bypassed."""
    if raw is None or raw == "":
        return False
    return check_password(normalize(raw), user.password) or check_password(raw, user.password)
