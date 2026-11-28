"""Super Admin API: /api/integrations/admin/... (session auth + CSRF, Super Admin only)."""
import json
import secrets
import urllib.request
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import Role
from apps.accounts.permissions import IsSuperAdmin, get_role, user_can

from . import services as s
from . import pull, spec, usage
from .models import (IntegrationApiConfig, IntegrationAudit, IntegrationCredential, IntegrationCustomer, IntegrationDocEmail, IntegrationLicense, IntegrationProject,
                     IntegrationRenewal, IntegrationSyncCheckpoint, IntegrationSyncLog, IntegrationTallyPending)
from .services import IntegrationError
from .project_access import CanConfigureProject, project_actions

PROJECT_OUT = (
    "id", "code", "name", "frontend_url", "backend_url", "auth_method", "framework", "health_path", "registration_method", "tally_serial_required",
    "serial_prefix", "serial_length", "activation_key_length", "device_limit", "default_license_type", "trial_days", "transfer_policy", "auto_sync",
    "usage_tracking", "welcome_email_mode", "activation_email_mode", "status", "enabled", "last_sync_status", "last_sync_at", "created_at", "license_policy",
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
    out["manual"] = p.registration_method == "MANUAL"
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
    def check_permissions(self, request):
        if request.method == "GET" and get_role(request.user) == Role.ADMIN and self.configurable(request):
            return
        super().check_permissions(request)

    def configurable(self, request):
        codes = [m.removesuffix(":api") for m in request.user.module_permissions.filter(
            module__endswith=":api", can_edit=True).values_list("module", flat=True)]
        return list(IntegrationProject.objects.filter(code__in=codes, license_policy="CALENDAR"))

    def get(self, request):
        projects = IntegrationProject.objects.all() if get_role(request.user) == Role.SUPERADMIN else self.configurable(request)
        return Response({"projects": [project_json(p) for p in projects]})

    def post(self, request):
        clean, errors = s.validate_project_data(request.data)
        if errors:
            return Response({"message": "Please correct the highlighted fields.", "errors": errors}, status=400)
        project, cred, secret = s.create_project(clean, self.actor())
        return Response({"project": project_json(project), "credentials": {"client_id": cred.client_id, "api_secret": secret, "shown_once": True},
                         "api_base": spec.base_url(api_base(request))}, status=201)


class ProjectCodePreviewView(AdminView):
    """Live preview only - never reserves anything. The real code is allocated atomically when the project is saved."""

    def get(self, request):
        name = s.normalize_project_name(request.query_params.get("name", ""))
        return Response({"name": name, "code": s.next_project_code(name) if name else ""})


class ProjectDetailView(AdminView):
    def check_permissions(self, request):
        if request.method in ("GET", "PATCH") and get_role(request.user) == Role.ADMIN:
            code = self.kwargs["code"].upper()
            allowed = user_can(request.user, f"{code}:api", "edit") or user_can(request.user, f"{code}:manage", "edit")
            if request.method == "PATCH":
                allowed = allowed or user_can(request.user, f"{code}:manage", "inactivate")
            if allowed and IntegrationProject.objects.filter(code=code, license_policy="CALENDAR").exists():
                return
        super().check_permissions(request)

    def get(self, request, code):
        project = self.project(code)
        result = project_json(project)
        if project.license_policy == "CALENDAR":
            result["developer_email"] = IntegrationApiConfig.objects.filter(project=project).values_list("developer_email", flat=True).first() or ""
        return Response({"project": result, "management": project_actions(request.user, project), "api_base": spec.base_url(api_base(request)),
                         "endpoints": [{k: e[k] for k in ("id", "method", "path", "summary", "request", "response")} | {"description": e.get("description", ""), "conflict": e.get("conflict")} for e in spec.for_project(spec.ENDPOINTS, project.code)],
                         "errors": [{"code": c, "http": st, "meaning": m} for c, st, m in spec.ERRORS], "api_version": spec.VERSION})

    @transaction.atomic
    def delete(self, request, code):
        """Permanent delete ONLY for an empty UNIVERSE project. A project with customers, registrations or usage history is protected (409): archive it instead."""
        project = IntegrationProject.objects.select_for_update().get(pk=self.project(code).pk)
        if project.license_policy != "CALENDAR":
            raise PermissionDenied()
        if not project_actions(request.user, project)["archive"]:
            raise PermissionDenied()
        if project.customers.exists() or project.registrations.exists() or project.integrationusageevent_set.exists():
            raise IntegrationError("PROJECT_HAS_DATA", "This project has customers, registrations or history, so it cannot be deleted. Archive it instead; nothing is lost.", 409)
        s.audit(project, "project.deleted", project.code, actor=self.actor())
        project.delete()
        return Response(status=204)

    @transaction.atomic
    def patch(self, request, code):
        project = IntegrationProject.objects.select_for_update().get(pk=self.project(code).pk)
        actions = project_actions(request.user, project)
        data = request.data
        if "lifecycle" in data:
            if not actions["archive"]:
                raise PermissionDenied()
            if set(data) != {"lifecycle"} or data["lifecycle"] not in ("archive", "restore"):
                raise ValidationError({"lifecycle": "Choose archive or restore."})
            if IntegrationSyncCheckpoint.objects.filter(project=project, status="RUNNING", last_started_at__gt=timezone.now() - timedelta(minutes=15)).exists():
                raise IntegrationError("SYNC_IN_PROGRESS", "Wait for the current synchronisation to finish.", 409)
            if data["lifecycle"] == "archive" and project.status != "ARCHIVED":
                project.status, project.enabled = "ARCHIVED", False
                s.audit(project, "project.archived", code, actor=self.actor())
                project.save(update_fields=["status", "enabled", "updated_at"])
            elif data["lifecycle"] == "restore" and project.status == "ARCHIVED":
                project.status, project.enabled = IntegrationProject.SETUP_PENDING, True
                s.audit(project, "project.restored", code, actor=self.actor())
                project.save(update_fields=["status", "enabled", "updated_at"])
            return Response({"project": project_json(project)})
        if project.license_policy == "CALENDAR":
            if "code" in data or ("tally_serial_required" in data and data["tally_serial_required"] != project.tally_serial_required):
                raise ValidationError({"project": "Project code and category are fixed."})
            if project.status == "ARCHIVED" and data.get("enabled"):
                raise IntegrationError("PROJECT_ARCHIVED", "Restore the project before enabling it.", 409)
            if get_role(request.user) != Role.SUPERADMIN:
                allowed = ({"name", "developer_email"} if actions["edit"] else set()) | ({"frontend_url", "backend_url"} if actions["configure"] else set())
                if set(data) - allowed:
                    raise PermissionDenied()
        if project.license_policy == "CALENDAR" and ("name" in data or "developer_email" in data):
            if "name" in data:
                name = s.normalize_project_name(data["name"])
                if not 2 <= len(name) <= 120 or IntegrationProject.objects.exclude(pk=project.pk).filter(name__iexact=name).exists():
                    raise ValidationError({"name": "Enter a unique project name of 2 to 120 characters."})
                if project.name != name:
                    project.name = name
                    project.save(update_fields=["name", "updated_at"])
                    s.audit(project, "project.renamed", code, actor=self.actor())
            if "developer_email" in data:
                email = str(data["developer_email"]).strip()
                try:
                    validate_email(email)
                except DjangoValidationError as exc:
                    raise ValidationError({"developer_email": "Enter a valid developer email."}) from exc
                if len(email) > 150:
                    raise ValidationError({"developer_email": "Email must be 150 characters or fewer."})
                IntegrationApiConfig.objects.update_or_create(
                    project=project, defaults={"developer_email": email}, create_defaults=s.project_config_defaults(email),
                )
            data = {field: value for field, value in data.items() if field not in ("name", "developer_email")}
        if get_role(request.user) == Role.ADMIN and data.get("backend_url") and data["backend_url"] != project.backend_url:
            config = IntegrationApiConfig.objects.filter(project=project).first()
            if config and config.credential_ref:
                from urllib.parse import urlsplit
                proposed, current = urlsplit(str(data["backend_url"])), urlsplit(project.backend_url)
                if (proposed.scheme, proposed.netloc.lower()) != (current.scheme, current.netloc.lower()):
                    raise PermissionDenied("A Superadmin must approve a new origin for this project's stored credential.")
        clean, errors = s.validate_project_data(data, creating=False)
        if errors:
            return Response({"message": "Please correct the highlighted fields.", "errors": errors}, status=400)
        if "tally_serial_required" in clean and clean["tally_serial_required"] != project.tally_serial_required:
            s.audit(project, "project.tally_required_changed", project.code, actor=self.actor(), detail=f"{project.tally_serial_required} -> {clean['tally_serial_required']}")
        backend_changed = "backend_url" in clean and clean["backend_url"] != project.backend_url
        for k, v in clean.items():
            setattr(project, k, v)
        if backend_changed and project.license_policy == "CALENDAR":
            IntegrationApiConfig.objects.filter(project=project).update(validation="{}")
            if project.status == IntegrationProject.ACTIVE:
                project.status = IntegrationProject.SETUP_PENDING
        if "enabled" in data and project.status != "ARCHIVED":
            project.enabled = bool(data["enabled"])
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

    permission_classes = [CanConfigureProject]

    def post(self, request, code):
        project = self.project(code)
        has_cred = project.credentials.filter(active=True).exists()
        url = project.backend_url.rstrip("/") + "/" + project.health_path.lstrip("/")
        reachable, detail = False, ""
        try:
            pull.validate_outbound_url(url)  # SSRF guard: only vetted http(s) targets are contacted
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


class ApiConfigView(AdminView):
    """The project's customer-registration API configuration (UI-driven; no per-project code)."""

    permission_classes = [CanConfigureProject]

    def get(self, request, code):
        project = self.project(code)
        cfg = IntegrationApiConfig.objects.filter(project=project).first()
        cp = IntegrationSyncCheckpoint.objects.filter(project=project).first()
        return Response({
            "config": None if cfg is None else {
                "customer_method": cfg.customer_method, "customer_path": cfg.customer_path, "auth_type": cfg.auth_type, "auth_header": cfg.auth_header,
                "credential_ref": cfg.credential_ref, "auth_config": json.loads(cfg.auth_config or "{}"), "api_protocol": pull._protocol(cfg), "protocol_supported": pull.protocol_supported(cfg),"request_headers": json.loads(cfg.request_headers), "field_mapping": json.loads(cfg.field_mapping),
                "pagination": json.loads(cfg.pagination), "sync_mode": cfg.sync_mode, "sync_interval_minutes": cfg.sync_interval_minutes,
                "send_welcome_email": cfg.send_welcome_email, "developer_name": cfg.developer_name, "developer_email": cfg.developer_email,
                "tally_path": cfg.tally_path, "tally_id_param": cfg.tally_id_param, "product_path": cfg.product_path, "identity_path": cfg.identity_path,
                "usage_path": cfg.usage_path, "validation": json.loads(cfg.validation or "{}")},
            "checkpoint": None if cp is None else {"status": cp.status, "next_page": cp.next_page, "fetched_total": cp.fetched_total,
                                                   "last_started_at": cp.last_started_at, "last_finished_at": cp.last_finished_at, "last_error": cp.last_error,
                                                   "last_success_at": cp.last_success_at, "next_run_at": cp.next_run_at, "consecutive_failures": cp.consecutive_failures}})

    @transaction.atomic
    def put(self, request, code):
        project = IntegrationProject.objects.select_for_update().get(pk=self.project(code).pk)
        clean, errors = pull.validate_config(request.data)
        if errors:
            return Response({"message": "Please correct the highlighted fields.", "errors": errors}, status=400)
        if clean["sync_mode"] == "POLL":  # webhook projects never call a Customer List API (other outbound calls are checked when made)
            try:
                pull.validate_outbound_url(project.backend_url.rstrip("/") + clean["customer_path"])
            except pull.UnsafeUrl as exc:
                return Response({"message": str(exc), "errors": {"customer_path": [str(exc)]}}, status=400)
        clean["send_welcome_email"] = bool(request.data.get("send_welcome_email", True))
        previous = IntegrationApiConfig.objects.filter(project=project).first()
        if get_role(request.user) == Role.ADMIN and clean["credential_ref"] != (previous.credential_ref if previous else ""):
            raise PermissionDenied("A Superadmin must set or change the server credential reference.")
        if get_role(request.user) != Role.SUPERADMIN and json.loads(clean["auth_config"]) != json.loads((previous.auth_config if previous else "") or "{}"):
            raise PermissionDenied("A Superadmin must set or change the custom authentication settings.")  # e.g. a new token URL would receive the secret
        checked_fields = set(clean) - {"developer_name", "developer_email", "send_welcome_email", "sync_interval_minutes"}
        if project.license_policy == "CALENDAR" and previous and any(getattr(previous, key) != clean[key] for key in checked_fields):
            clean["validation"] = "{}"
            if project.status == IntegrationProject.ACTIVE:
                project.status = IntegrationProject.SETUP_PENDING
                project.save(update_fields=["status", "updated_at"])
        saved, _ = IntegrationApiConfig.objects.update_or_create(project=project, defaults=clean)
        pull.forget_token(saved)  # new settings never reuse a token obtained with the old ones
        s.audit(project, "api_config.saved", project.code, actor=self.actor())
        return self.get(request, code)


class SyncCustomersView(AdminView):
    """Manual re-sync (the same routine the scheduled command runs). Idempotent: repeating it never duplicates a customer or licence."""

    def post(self, request, code):
        project = self.project(code)
        stats = pull.pull_sync(project, actor=self.actor())
        return Response({"result": stats})


class SendDocsView(AdminView):
    """E-mails the project-specific API documentation to the developer through the Nexus SMTP settings. No secret is ever included."""

    def get(self, request, code):
        project = self.project(code)
        return Response({"history": [{"to": h.to_email, "status": h.status, "sent_by": h.sent_by, "at": h.created_at} for h in project.doc_emails.order_by("-id")[:50]]})

    def post(self, request, code):
        from django.core.validators import validate_email
        from apps.registrations import mailer
        project = self.project(code)
        cfg = IntegrationApiConfig.objects.filter(project=project).first()
        to = str(request.data.get("to") or (cfg.developer_email if cfg else "")).strip()
        try:
            validate_email(to)
        except Exception:
            return Response({"message": "Enter a valid developer email address.", "errors": {"to": ["Enter a valid developer email address."]}}, status=400)
        from urllib.parse import urlparse
        from django.conf import settings as dj
        from django.core.cache import cache
        provision = bool(request.data.get("provision_secret"))
        if provision and (get_role(request.user) != Role.SUPERADMIN or request.data.get("confirm") is not True):
            raise PermissionDenied("A Superadmin must explicitly confirm credential provisioning.")
        if not cache.add(f"int:docs:lock:{project.pk}", 1, 30):  # double clicks / duplicate requests
            raise IntegrationError("DUPLICATE_REQUEST", "Documentation for this project was just sent. Wait 30 seconds before sending again.", 409)
        base_api = spec.base_url(api_base(request))
        app = (getattr(dj, "NEXUS_PUBLIC_APP_URL", "") or request.headers.get("Origin", "") or "").rstrip("/")
        is_local = lambda u: (urlparse(u).hostname or "").lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0")  # noqa: E731
        local = is_local(base_api) or is_local(app)
        env_label = "LOCAL DEVELOPMENT - not valid against production" if local else "PRODUCTION"
        cred = IntegrationCredential.objects.filter(project=project, active=True).first()
        link = ""
        if provision:
            cred, token = s.issue_handoff(project)
            link = f"{app}/developer-credentials?token={token}" if app else ""
        lines = ["Hello,", "", f"Here are the Nexus integration details for {project.name}.", "",
                 f"Environment        : {env_label}", f"Project            : {project.name} ({project.code})", f"Nexus API Base URL : {base_api}",
                 f"Client ID          : {cred.client_id if cred else '(not issued yet)'}"]
        if provision:
            lines += [f"API Secret         : {link or '(no public Nexus address configured - ask the administrator)'}",
                      f"                     Open this link once (valid {s.HANDOFF_HOURS} hours) and click Show Credentials. It issues the API Secret for the",
                      "                     Client ID above; any earlier secret stops working. Store it in your server's secret store."]
        else:
            lines.append("API Secret         : not included - ask the Nexus administrator to send a one-time credential link.")
        lines += ["", "1. Register each new customer (server-to-server only, never from a browser)",
                  f"   POST {base_api}customers/register",
                  "   Headers: X-Client-Id: <Client ID> | X-Client-Secret: <API Secret> | Content-Type: application/json",
                  "   Body:", "   " + json.dumps({"project_code": project.code, "external_customer_id": "YOUR_CUSTOMER_ID", "customer_name": "Customer Name",
                                                   "email": "customer@example.com", "contact_number": "9876543210"}),
                  "   Response: HTTP 202  {\"ok\": true, \"data\": {\"status\": \"PENDING_REVIEW\", \"license_issued\": false}}", "",
                  "2. Nexus administrator reviews the customer and clicks Save & Allow. Nexus then issues the licence and emails the",
                  "   customer their User ID and temporary password. Your application does not create passwords.", "",
                  "3. Customer login and device authorization (from your backend)",
                  f"   POST {base_api}devices/authorize  with user_id, password, device_id, device_name",
                  "   - first device: authorised, returns a session_token",
                  "   - another device: HTTP 409 TRANSFER_CONFIRMATION_REQUIRED -> show OK / Cancel, then call again with confirm_transfer true / false",
                  f"   POST {base_api}sessions/introspect  with session_token before protected operations (expired / revoked -> deny)", "",
                  "The complete API reference is available from the API Delivery page (Download Guide).", "", "Regards,", "Sai Infosys Nexus"]
        text = "\n".join(lines)
        status = mailer.deliver(f"Nexus API integration - {project.name} ({project.code}){' [LOCAL]' if local else ''}", text, to, kind="int_docs", project=project.code,
                                event_id=f"int-docs-{project.pk}-{secrets.token_hex(3)}")
        IntegrationDocEmail.objects.create(project=project, to_email=to, status=str(status)[:40], sent_by=self.actor())
        s.audit(project, "docs.emailed", to, actor=self.actor(), detail=("with one-time credential link" if provision else "documentation only") + f"; {env_label}")  # no token/secret
        return Response({"status": status, "project": project.code, "to": to, "environment": env_label, "client_id": cred.client_id if cred else None,
                         "credential_link": "sent" if provision and link else "not_sent"})


class TallyPendingView(AdminView):
    """Customers waiting for Tally verification (Tally Required = Yes projects)."""

    def get(self, request, code):
        from . import tally
        return Response({"results": tally.pending_rows(self.project(code))})


class TallyRetryView(AdminView):
    def post(self, request, code, pk):
        from . import tally
        project = self.project(code)
        pending = IntegrationTallyPending.objects.filter(project=project, pk=pk).first()
        if not pending:
            raise NotFound("Unknown pending registration.")
        status, body = tally.retry_verification(pending, pull.fetch_tally, actor=self.actor())
        return Response({"verified": status == 201, **body}, status=200 if status == 201 else 202)


class RenewalHistoryView(AdminView):
    def get(self, request, code):
        project = self.project(code)
        rows = IntegrationRenewal.objects.filter(license__customer__project=project).select_related("license__customer").order_by("-id")[:200]
        return Response({"results": [{"at": r.created_at, "customer": r.license.customer.name, "user_id": r.license.customer.user_id, "action": r.action,
                                      "license_type": r.license_type, "years": r.years, "previous_expires_at": r.previous_expires_at, "new_expires_at": r.new_expires_at,
                                      "by": r.actor} for r in rows]})


class RegistrationBase(AdminView):
    def reg(self, pk):
        from .models import IntegrationRegistration
        r = IntegrationRegistration.objects.select_related("project", "customer").filter(pk=pk).first()
        if not r:
            raise NotFound("Unknown registration.")
        return r


class RegistrationListView(AdminView):
    def get(self, request):
        from . import universe
        from .models import IntegrationRegistration
        qs = IntegrationRegistration.objects.select_related("project", "customer")
        if request.query_params.get("project"):
            qs = qs.filter(project__code=request.query_params["project"].upper())
        status = request.query_params.get("status")
        if status == "open":
            qs = qs.filter(status__in=["PENDING_REVIEW", "PENDING_TALLY_VERIFICATION", "HELD"])
        elif status:
            qs = qs.filter(status=status)
        try:
            page, size = max(int(request.query_params.get("page", 1)), 1), min(max(int(request.query_params.get("page_size", 25)), 1), 100)
        except ValueError:
            page, size = 1, 25
        total = qs.count()
        return Response({"total": total, "page": page, "page_size": size, "results": [universe.row(r) for r in qs[(page - 1) * size: page * size]]})


class RegistrationDetailView(RegistrationBase):
    def get(self, request, pk):
        from . import universe
        r = self.reg(pk)
        data = universe.row(r)
        q = request.query_params
        if q.get("license_type"):
            t = universe.term(r.project, q["license_type"], q.get("trial_days"), q.get("years"))
            data["preview"] = {"license_type": t["license_type"], "starts_at": t["starts_at"], "expires_at": t["expires_at"]}
        return Response({"registration": data})


class RegistrationActionView(RegistrationBase):
    def post(self, request, pk, action):
        from . import pull, universe
        r = self.reg(pk)
        who, body = self.actor(), request.data
        if action == "approve":
            out = universe.approve(r.pk, body.get("license_type"), body.get("trial_days"), body.get("years"), actor=who, app_url=request.headers.get("Origin", ""))
            return Response({"result": out})
        if action == "hold":
            universe.hold(r.pk, who, str(body.get("note") or ""))
        elif action == "reject":
            universe.reject(r.pk, who, str(body.get("note") or ""))
        elif action == "retry-tally":
            universe.retry_tally(r, pull.fetch_tally, who)
        else:
            raise NotFound("Unknown action.")
        r.refresh_from_db()
        return Response({"registration": universe.row(r)})


class NotificationsView(AdminView):
    permission_classes = []

    def check_permissions(self, request):
        from apps.accounts.constants import Role
        from apps.accounts.permissions import get_role
        if get_role(request.user) not in (Role.SUPERADMIN, Role.ADMIN):
            self.permission_denied(request)

    def _visible(self, request):
        from apps.accounts.constants import Role
        from apps.accounts.permissions import get_role, user_can
        from .models import IntegrationNotification
        from apps.dashboard.models import NotificationDismissal, dismissed_ids
        qs = IntegrationNotification.objects.exclude(pk__in=dismissed_ids(request.user, NotificationDismissal.UNIVERSE))  # this user's X
        if get_role(request.user) == Role.ADMIN:  # project-scoped: only projects this Admin may view
            allowed = [p.pk for p in IntegrationProject.objects.all() if user_can(request.user, p.code, "view")]
            qs = qs.filter(project_id__in=allowed)
        return qs

    def get(self, request):
        from . import universe
        universe.ensure_expiry_notifications()  # idempotent: never repeats a notification
        base = self._visible(request)
        rows = list(base.select_related("project", "registration", "customer").order_by("-id")[:30])
        return Response({"unread_count": base.filter(read=False).count(),
                         "results": [{"id": n.pk, "title": n.title, "project": n.project.name, "registration_id": n.registration_id, "kind": n.kind, "project_code": n.project.code, "customer_id": n.customer_id,
                                      "customer": n.registration.name if n.registration else (n.customer.name if n.customer else ""), "read": n.read, "created_at": n.created_at,
                                      "status": n.registration.status if n.registration else ""} for n in rows]})


class NotificationReadView(NotificationsView):
    def post(self, request, pk):
        self._visible(request).filter(pk=pk).update(read=True)
        return Response({"ok": True})


class NotificationDismissView(NotificationsView):
    """X on a bell row: hides it for the CURRENT user only (stored per user). The notification and its registration / sync / audit data stay."""

    def post(self, request, pk):
        from apps.dashboard.models import NotificationDismissal as D
        if D.objects.filter(user=request.user, source=D.UNIVERSE, notification_id=pk).exists():
            return Response({"ok": True, "dismissed": pk})  # repeat click: already hidden
        if not self._visible(request).filter(pk=pk).exists():
            raise NotFound("Notification not found.")
        D.objects.get_or_create(user=request.user, source=D.UNIVERSE, notification_id=pk)
        return Response({"ok": True, "dismissed": pk})


class ValidateApiView(AdminView):
    """Validate Customer / Product / Tally API, Preview API Response and Test Field Mapping - real, SSRF-guarded calls."""

    permission_classes = [CanConfigureProject]

    def post(self, request, code, kind):
        project = self.project(code)
        if kind not in ("customer", "product", "tally", "mapping", "preview"):
            raise NotFound("Unknown check.")
        result = pull.probe(project, kind, str(request.data.get("sample_id") or ""), request.data.get("sample"))
        cfg = IntegrationApiConfig.objects.filter(project=project).first()
        if cfg and kind != "preview":
            v = json.loads(cfg.validation or "{}")
            v[kind] = {"ok": bool(result["ok"]) and not result.get("warning"), "at": timezone.now().isoformat(), "message": result["message"][:200],
                       **({"checks": result["checks"]} if isinstance(result.get("checks"), dict) else {})}  # webhook: route / credentials / live counts
            cfg.validation = json.dumps(v)
            cfg.save(update_fields=["validation"])
        s.audit(project, f"api.validate.{kind}", project.code, ok=bool(result["ok"]), actor=self.actor(), detail=result["message"][:200])
        return Response(result)


class DetectFieldsView(AdminView):
    """Auto Detect Fields: returns detected JSON paths + suggested mapping. Saves nothing and imports nothing; the sample is not stored."""

    permission_classes = [CanConfigureProject]

    def post(self, request, code):
        return Response(pull.detect_fields(self.project(code), request.data.get("sample")))


class ActivateIntegrationView(AdminView):
    """A green Active status only after the mandatory checks succeeded: customer API, field mapping and (when Tally is required) the Tally API."""

    permission_classes = [CanConfigureProject]

    def post(self, request, code):
        with transaction.atomic():
            project = IntegrationProject.objects.select_for_update().get(pk=self.project(code).pk)
            cfg = IntegrationApiConfig.objects.filter(project=project).first()
            if project.status == "ARCHIVED":
                raise IntegrationError("PROJECT_ARCHIVED", "Restore this project before activation.", 409)
            if cfg and not pull.protocol_supported(cfg):
                raise IntegrationError("PROTOCOL_NOT_SUPPORTED", "Protocol Not Supported Yet: only REST API (JSON) integrations can be activated.", 409)
            if cfg and cfg.sync_mode in pull.PUSH_MODES and not (project.registrations.exists() or project.customers.exists()):
                raise IntegrationError("AWAITING_LIVE_WEBHOOK", "Awaiting Live Webhook Verification: configuration and mapping are saved, but the external application "
                                       "has not yet sent its first authenticated customer registration. A sample payload does not prove live delivery.", 409)
            v = json.loads(cfg.validation or "{}") if cfg else {}
            needed = ["customer", "mapping"] + (["tally"] if project.tally_serial_required else []) + (["product"] if cfg and cfg.product_path else [])
            missing = [k for k in needed if not v.get(k, {}).get("ok")]
            if missing:
                return Response({"message": "Run and pass these checks first: " + ", ".join(missing) + ".", "missing": missing, "errors": {}}, status=409)
            project.status, project.enabled = IntegrationProject.ACTIVE, True
            project.save(update_fields=["status", "enabled", "updated_at"])
            s.audit(project, "integration.activated", project.code, actor=self.actor())
        initial = None
        try:  # immediate first synchronisation; a failure is logged by pull_sync and never blocks the activation
            initial = pull.pull_sync(project, actor=self.actor())
        except Exception as exc:  # noqa: BLE001
            s.audit(project, "sync.initial_failed", project.code, ok=False, actor=self.actor(), detail=type(exc).__name__)
        return Response({"status": project.status, "initial_sync": initial})


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
    permission_classes = [CanConfigureProject]

    def get(self, request, code):
        project = self.project(code)
        rows = IntegrationAudit.objects.filter(project=project).order_by("-id")[:100]
        return Response({"audit": [{"at": a.created_at, "action": a.action, "subject": a.subject, "ok": a.ok, "detail": a.detail, "actor": a.actor} for a in rows]})
