from rest_framework.authentication import SessionAuthentication


class SessionAuthentication401(SessionAuthentication):
    """Session auth that answers 401 (not 403) for anonymous requests, so the SPA can tell
    'not signed in / session expired' (401) apart from 'CSRF or permission denied' (403)."""

    def authenticate_header(self, request):
        return "Session"


class CsrfEnforcedSessionAuthentication(SessionAuthentication401):
    """Enforces CSRF even for anonymous callers. Used by the login endpoint (login CSRF)."""

    def authenticate(self, request):
        self.enforce_csrf(request)
        return super().authenticate(request)
