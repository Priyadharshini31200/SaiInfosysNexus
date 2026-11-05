class NoStoreApiResponsesMiddleware:
    """Authenticated API data must never be cached by the browser or proxies, so the
    Back button cannot resurface it after logout."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response["Pragma"] = "no-cache"
            response["Vary"] = "Cookie"
        return response


class ProjectApiCorsMiddleware:
    """Opt-in CORS for the server-to-server project APIs only (/api/portal/ and /api/device/), which authenticate with a
    project key / device signature and never with cookies. Staff endpoints (/api/auth/, /api/projects/, ...) are same-origin
    only and never receive CORS headers. Allowed origins come from DJANGO_CORS_ALLOWED_ORIGINS; empty = no CORS at all."""

    PREFIXES = ("/api/portal/", "/api/device/")
    ALLOW_HEADERS = "Content-Type, X-Project-Key, X-Customer-Token, X-Device-Id, X-Device-Timestamp, X-Device-Nonce, X-Device-Signature"

    def __init__(self, get_response):
        from django.conf import settings

        self.get_response = get_response
        self.origins = set(getattr(settings, "CORS_ALLOWED_ORIGINS", []))

    def __call__(self, request):
        origin = request.headers.get("Origin", "")
        applies = bool(self.origins) and origin in self.origins and request.path.startswith(self.PREFIXES)
        if applies and request.method == "OPTIONS":
            from django.http import HttpResponse

            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if applies:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = self.ALLOW_HEADERS
            response["Access-Control-Max-Age"] = "600"
            response["Vary"] = "Origin, " + response.get("Vary", "Cookie")
        return response
