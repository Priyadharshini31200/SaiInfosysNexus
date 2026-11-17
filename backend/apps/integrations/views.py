"""Server-to-server Universal Licensing API: /api/integrations/v1/... (X-Client-Id + X-Client-Secret, product-scoped)."""
import uuid

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import ParseError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as s
from .spec import VERSION
from .services import IntegrationError


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", ""))[:45]


class V1View(APIView):
    authentication_classes = []  # machine credentials only; no cookies, hence no CSRF surface
    permission_classes = []
    step = None  # verification step recorded after a successful call

    def _client(self, request):
        cred = s.authenticate_client(request.headers.get("X-Client-Id"), request.headers.get("X-Client-Secret"))
        if not cred:
            s.audit(None, "api.unauthenticated", request.headers.get("X-Client-Id", "")[:40], ok=False, ip=client_ip(request))
            raise IntegrationError("UNAUTHENTICATED", "Missing or invalid API credentials.", 401)
        if not cred.project.enabled:
            raise IntegrationError("INTEGRATION_DISABLED", "This integration is disabled in Nexus.", 403)
        limit = int(getattr(settings, "INTEGRATION_RATE_LIMIT_PER_MIN", 300))
        bucket = f"int:rl:{cred.client_id}:{int(timezone.now().timestamp() // 60)}"
        cache.add(bucket, 0, 70)
        if cache.incr(bucket) > limit:
            raise IntegrationError("RATE_LIMITED", "Rate limit exceeded; retry in a minute.", 429)
        return cred

    def dispatch_call(self, request, **kwargs):
        rid = uuid.uuid4().hex
        try:
            cred = self._client(request)
            project = cred.project
            if request.method == "POST":
                try:
                    data = request.data
                except ParseError:
                    raise IntegrationError("INVALID_JSON", "The request body is not valid JSON.", 400)
                if not isinstance(data, dict):
                    raise IntegrationError("INVALID_JSON", "The request body must be a JSON object.", 400)
                if str(data.get("project_code", "")).upper() != project.code:
                    s.audit(project, "api.project_mismatch", data.get("project_code", ""), ok=False, ip=client_ip(request))
                    raise IntegrationError("PROJECT_MISMATCH", "project_code does not match these credentials.", 403)
            else:
                data = {}
            out = self.process(project, data, request, **kwargs)
            status, body = out if isinstance(out, tuple) else (200, out)
            if self.step and status < 300:
                s.mark_step(project, self.step)
            return Response({"ok": True, "data": body, "request_id": rid}, status=status)
        except IntegrationError as exc:
            return Response({"ok": False, "error": {"code": exc.code, "message": exc.message, **exc.data}, "request_id": rid}, status=exc.status)

    def post(self, request, **kwargs):
        return self.dispatch_call(request, **kwargs)

    get = post


class RegisterView(V1View):
    step = "register"

    def process(self, project, data, request):
        return s.register_customer(project, data, idem_key=(request.headers.get("Idempotency-Key") or str(data.get("idempotency_key") or ""))[:100])


class UpdateView(V1View):
    def process(self, project, data, request):
        return s.sync_customer(project, data)


class ImportView(V1View):
    def process(self, project, data, request):
        items = data.get("customers")
        if not isinstance(items, list) or not 1 <= len(items) <= 200:
            raise IntegrationError("VALIDATION_ERROR", "customers must be a list of 1-200 customers.", 422)
        results = s.bulk_import(project, [i for i in items if isinstance(i, dict)], send_email=data.get("send_welcome_email", True) is not False)
        ok = sum(1 for r in results if r["ok"])
        return 200, {"imported": ok, "failed": len(results) - ok, "results": results}


class AuthVerifyView(V1View):
    step = "auth_verify"

    def process(self, project, data, request):
        return s.auth_verify(project, data, client_ip(request))


class ChangePasswordView(V1View):
    def process(self, project, data, request):
        return s.change_password(project, data)


class LicenseValidateView(V1View):
    def process(self, project, data, request):
        return s.validate_license(project, data)


class ActivationRequestView(V1View):
    def process(self, project, data, request):
        return s.request_activation_key(project, data, client_ip(request))


class ActivationVerifyView(V1View):
    def process(self, project, data, request):
        return s.verify_activation(project, data, client_ip(request))


class TransferConfirmView(V1View):
    def process(self, project, data, request):
        return s.confirm_transfer(project, data, client_ip(request))


class IntrospectView(V1View):
    def process(self, project, data, request):
        return s.introspect(project, data)


class LogoutView(V1View):
    def process(self, project, data, request):
        return s.logout(project, data)


class UsageView(V1View):
    def process(self, project, data, request):
        return s.record_usage(project, data)


class ConfigView(V1View):
    def process(self, project, data, request, project_code):
        if project_code.upper() != project.code:
            raise IntegrationError("PROJECT_MISMATCH", "project_code does not match these credentials.", 403)
        return {
            "project_code": project.code, "name": project.name, "status": project.status, "frontend_url": project.frontend_url,
            "serial_length": project.serial_length, "serial_prefix": project.serial_prefix, "activation_key_length": project.activation_key_length,
            "device_limit": project.device_limit, "transfer_policy": project.transfer_policy, "trial_days": project.trial_days,
            "default_license_type": project.default_license_type, "usage_tracking": project.usage_tracking, "api_version": VERSION,
            "required_steps": list(project.REQUIRED_STEPS), "verified_steps": project.steps,
        }
