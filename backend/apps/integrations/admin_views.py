"""Super Admin API: /api/integrations/admin/... (session auth + CSRF, Super Admin only)."""
import json
import urllib.request

from django.http import HttpResponse
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsSuperAdmin

from . import services as s
from . import spec, usage
from .models import IntegrationAudit, IntegrationCustomer, IntegrationLicense, IntegrationProject, IntegrationSyncLog
from .services import IntegrationError

PROJECT_OUT = (
    "id", "code", "name", "frontend_url", "backend_url", "auth_method", "framework", "health_path", "registration_method", "tally_serial_required",
    "serial_prefix", "serial_length", "activation_key_length", "device_limit", "default_license_type", "trial_days", "transfer_policy", "auto_sync",
    "usage_tracking", "welcome_email_mode", "activation_email_mode", "status", "enabled", "last_sync_status", "last_sync_at", "created_at",
)


def api_base(request):
    """The public API base, e.g. https://host/nexus/api (the /nexus prefix is added by the live proxy via X-Forwarded-Prefix)."""
    from django.conf import settings
    if getattr(settings, "NEXUS_PUBLIC_API_BASE", ""):  # explicit override, e.g. https://host/nexus/api
        return settings.NEXUS_PUBLIC_API_BASE.rstrip("/")
    prefix = request.META.get("HTTP_X_FORWARDED_PREFIX", "").rstrip("/")
    scheme = "https" if request.is_secure() or request.META.get("HTTP_X_FORWARDED_PROTO") == "https" else request.scheme
    return f"{scheme}://{request.get_host()}{prefix}/api"


def project_json(p, request=None):
    out = {f: getattr(p, f) for f in PROJECT_OUT}
    out["usage_rules"] = p.rules
    out["verified_steps"] = p.steps
    out["required_steps"] = list(IntegrationProject.REQUIRED_STEPS)
    cred = p.credentials.filter(active=True).first()
    out["client_id"] = cred.client_id if cred else None
    out["secret_hint"] = f"…{cred.secret_hint}" if cred else None
    out["customers"] = p.customers.count()
    return out


class AdminView(APIView):
    permission_classes = [IsSuperAdmin]

    def handle_exception(self, exc):
        if isinstance(exc, IntegrationError):
            return Response({"message": exc.message, "code": exc.code, "errors": exc.data.get("fields", {})}, status=exc.status)
        return super().handle_exception(exc)

    def project(self, code):
        p = IntegrationProject.objects.filter(code=code.upper()).first()
        if not p:
            raise NotFound("Unknown project.")
        return p

    def actor(self):
        return self.request.user.get_username()


class ProjectListView(AdminView):
    def get(self, request):
        return Response({"projects": [project_json(p) for p in IntegrationProject.objects.all()]})

    def post(self, request):
        clean, errors = s.validate_project_data(request.data)
        if errors:
            return Response({"message": "Please correct the highlighted fields.", "errors": errors}, status=400)
        project, cred, secret = s.create_project(clean, self.actor())
        return Response({"project": project_json(project), "credentials": {"client_id": cred.client_id, "api_secret": secret, "shown_once": True},
                         "api_base": spec.base_url(api_base(request))}, status=201)


class ProjectDetailView(AdminView):
    def get(self, request, code):
        return Response({"project": project_json(self.project(code)), "api_base": spec.base_url(api_base(request)),
                         "endpoints": [{k: e[k] for k in ("id", "method", "path", "summary", "request", "response")} | {"description": e.get("description", ""), "conflict": e.get("conflict")} for e in spec.ENDPOINTS],
                         "errors": [{"code": c, "http": st, "meaning": m} for c, st, m in spec.ERRORS], "api_version": spec.VERSION})

    def patch(self, request, code):
        project = self.project(code)
        clean, errors = s.validate_project_data(request.data, creating=False)
        if errors:
            return Response({"message": "Please correct the highlighted fields.", "errors": errors}, status=400)
        for k, v in clean.items():
            setattr(project, k, v)
        if "enabled" in request.data:
            project.enabled = bool(request.data["enabled"])
            if not project.enabled:
                project.status = IntegrationProject.DISABLED
            elif project.status == IntegrationProject.DISABLED:
                project.status = IntegrationProject.ACTIVE if all(x in project.steps for x in project.REQUIRED_STEPS) else IntegrationProject.SETUP_PENDING
            s.audit(project, "project.enabled" if project.enabled else "project.disabled", project.code, actor=self.actor())
        project.save()
        return Response({"project": project_json(project)})


class RotateView(AdminView):
    def post(self, request, code):
        project = self.project(code)
        cred, secret = s.issue_credential(project)
        s.audit(project, "credentials.rotated", project.code, actor=self.actor())
        return Response({"credentials": {"client_id": cred.client_id, "api_secret": secret, "shown_once": True}, "note": "The previous secret stopped working immediately."})


class TestConnectionView(AdminView):
    """Checks the credentials are active and that the project's backend answers its health URL. A health answer does NOT
    prove the integration: only `verified_steps` (real calls made by the project's backend) does."""

    def post(self, request, code):
        project = self.project(code)
        has_cred = project.credentials.filter(active=True).exists()
        url = project.backend_url.rstrip("/") + "/" + project.health_path.lstrip("/")
        reachable, detail = False, ""
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Nexus-Integration-Check"}), timeout=5) as r:  # noqa: S310
                reachable, detail = 200 <= r.status < 300, f"HTTP {r.status}"
        except Exception as exc:
            detail = type(exc).__name__
        missing = [x for x in IntegrationProject.REQUIRED_STEPS if x not in project.steps]
        return Response({"credentials_active": has_cred, "project_backend_reachable": reachable, "health_url": url, "detail": detail,
                         "integration_verified": not missing, "missing_steps": missing,
                         "note": "A reachable health URL does not prove registration, licensing and session enforcement are integrated."})


class DocsView(AdminView):
    def get(self, request, code, kind):
        project = self.project(code)
        base = api_base(request)
        if kind == "openapi":
            body, ctype, name = json.dumps(spec.openapi(base, project.code), indent=2), "application/json", f"nexus-{project.code.lower()}-openapi.json"
        else:
            body, ctype, name = spec.markdown(base, project), "text/markdown; charset=utf-8", f"nexus-{project.code.lower()}-api.md"
        resp = HttpResponse(body, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        return resp


class DashboardView(AdminView):
    def get(self, request):
        return Response(usage.dashboard())


class CustomerListView(AdminView):
    def get(self, request, code):
        project = self.project(code)
        rows = []
        for c in IntegrationCustomer.objects.filter(project=project).select_related("license")[:500]:
            lic = c.license
            rows.append({"id": c.pk, "license_id": lic.pk, "external_customer_id": c.external_customer_id, "name": c.name, "email": c.email,
                         "contact_number": c.contact_number, "serial_number": c.serial_number, "license_type": lic.license_type,
                         "state": s.license_state(lic), "expires_at": lic.expires_at, "welcome_email_status": c.welcome_email_status,
                         "active_devices": lic.sessions.filter(status="ACTIVE").count(), "created_at": c.created_at})
        return Response({"customers": rows})


class ApproveView(AdminView):
    def post(self, request, license_id):
        lic = IntegrationLicense.objects.select_related("customer__project").filter(pk=license_id).first()
        if not lic:
            raise NotFound("Unknown licence.")
        s.approve_license(lic, request.data.get("license_type"), self.actor())
        return Response({"license": s.license_payload(lic)})


class ResendWelcomeView(AdminView):
    def post(self, request, customer_id):
        c = IntegrationCustomer.objects.select_related("project").filter(pk=customer_id).first()
        if not c:
            raise NotFound("Unknown customer.")
        return Response({"welcome_email": s.resend_welcome(c, self.actor()), "note": "A new temporary password was issued and emailed."})


class FailedSyncView(AdminView):
    def get(self, request):
        logs = IntegrationSyncLog.objects.filter(status="FAILED").select_related("project").order_by("-id")[:200]
        failed_mail = IntegrationCustomer.objects.filter(welcome_email_status__in=["failed", ""])  # "" = saved but the process stopped before the mail was attempted.select_related("project")[:100]
        return Response({
            "failed_syncs": [{"id": l.pk, "project": l.project.code, "external_customer_id": l.external_customer_id, "event": l.event, "error_code": l.error_code, "error": l.error, "retries": l.retries, "at": l.created_at} for l in logs],
            "failed_emails": [{"customer_id": c.pk, "project": c.project.code, "user_id": c.user_id, "email": s.mask_email(c.email)} for c in failed_mail]})


class RetrySyncView(AdminView):
    def post(self, request, log_id):
        log = IntegrationSyncLog.objects.select_related("project").filter(pk=log_id, status="FAILED").first()
        if not log:
            raise NotFound("No failed sync with this id.")
        return Response(s.retry_sync(log))


class AuditView(AdminView):
    def get(self, request, code):
        project = self.project(code)
        rows = IntegrationAudit.objects.filter(project=project).order_by("-id")[:100]
        return Response({"audit": [{"at": a.created_at, "action": a.action, "subject": a.subject, "ok": a.ok, "detail": a.detail, "actor": a.actor} for a in rows]})
