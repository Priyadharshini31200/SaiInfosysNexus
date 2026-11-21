"""Superadmin-managed staff (Admin / User) accounts. Reuses the existing grant tables (UserModulePermission for the PRN/GSTR
modules, TallyAdminPermission for Tally SL / API products) and adds only: staff details, visibility grants, report scope, audit."""
import json
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import IntegrityError, transaction
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .constants import Action, Role
from .models import (DashboardVisibilityGrant, DeveloperProjectAccess, StaffPermissionAudit, StaffProfile, StaffReportProject, UserModulePermission, UserProfile)
from .permissions import IsSuperAdmin, permission_modules

USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
MANAGED = (Role.ADMIN, Role.USER, Role.DEVELOPER)
AUDITED = ("module_grants", "tally_grants", "visibility", "report_projects", "developer_projects")


def project_catalog():
    """{code: name} of every registered project (legacy registry, Tally SL products, API-integrated projects)."""
    from apps.integrations.models import IntegrationProject
    from apps.registrations.models import PROJECTS
    from apps.tallysl.models import TallyProduct
    names = dict(PROJECTS)
    names.update({p.code: p.name for p in TallyProduct.objects.filter(active=True)})
    names.update({p.code: p.name for p in IntegrationProject.objects.all()})
    return names


def project_categories():
    """The same Tally/Non-Tally classification used by the project directories."""
    from apps.integrations.models import IntegrationProject
    from apps.tallysl.models import TallyProduct
    categories = {"gstr2tally": "Tally", "prn-auto-print": "Non-Tally"}
    categories.update({code: "Tally" for code in TallyProduct.objects.filter(active=True).values_list("code", flat=True)})
    categories.update({p.code: "Tally" if p.license_policy != "CALENDAR" or p.tally_serial_required else "Non-Tally"
                       for p in IntegrationProject.objects.all()})
    return categories


def api_modules():
    """Permission modules of API-connected products (Mothys and future ones): <CODE>, <CODE>:dashboard, <CODE>:expiry, <CODE>:usage."""
    from apps.integrations.models import IntegrationProject
    modules = []
    for code, policy in IntegrationProject.objects.values_list("code", "license_policy"):
        modules.extend([code, f"{code}:dashboard", f"{code}:expiry", f"{code}:usage"])
        if policy == "CALENDAR":
            modules.extend([f"{code}:manage", f"{code}:api"])
    return modules


def build_catalog():
    """Real, enforced menus/features and their applicable actions for every registered project - the single source for the permission UI."""
    from apps.integrations.models import IntegrationProject
    from apps.registrations.models import DEVICE_PROJECTS, PROJECTS
    from apps.tallysl.models import PERMISSION_LABELS, TallyProduct
    out = []
    tally = {p.code: p for p in TallyProduct.objects.filter(active=True)}
    for code, label in PROJECTS.items():
        if code in tally:
            continue
        out.append({"code": code, "name": label, "kind": "legacy", "menus": [
            {"module": f"{code}:dashboard", "label": "Dashboard", "actions": ["view"]},
            {"module": code, "label": "Customers & Licences", "actions": [a.value for a in Action if a != Action.REPORTS and (a != Action.DEVICE_TRANSFER or code in DEVICE_PROJECTS)]},
            {"module": f"{code}:expiry", "label": "Reports / License Expiry", "actions": ["reports", "export_print"]},
            {"module": f"{code}:usage", "label": "Reports / Usage", "actions": ["reports", "export_print"]}]})
    for code, p in tally.items():
        out.append({"code": code, "name": p.name, "kind": "tally", "menus": [
            {"module": code, "label": "Dashboard, Customers, Licences, Reports, Devices", "actions": list(PERMISSION_LABELS)}], "labels": dict(PERMISSION_LABELS)})
    for p in IntegrationProject.objects.all():
        c = p.code
        out.append({"code": c, "name": p.name, "kind": "api", "menus": [
            {"module": f"{c}:dashboard", "label": "Dashboard", "actions": ["view"]},
            {"module": c, "label": "Customers & Licences", "actions": ["view", "edit", "extend_trial", "cancel_trial", "inactivate", "approve", "renew", "export_print"]},
            {"module": f"{c}:expiry", "label": "Reports / License Expiry", "actions": ["reports", "export_print"]},
            {"module": f"{c}:usage", "label": "Reports / Usage & Customer reports", "actions": ["reports", "export_print"]},
            *([{"module": f"{c}:manage", "label": "Project Management", "actions": ["edit", "inactivate"]},
               {"module": f"{c}:api", "label": "API Configuration", "actions": ["edit"]}] if p.license_policy == "CALENDAR" else [])]})
    return out


def setup_link(user):
    return f"/setup-password?uid={urlsafe_base64_encode(force_bytes(user.pk))}&token={default_token_generator.make_token(user)}"


def _grants(user):
    from apps.tallysl.models import PERMISSION_ACTIONS, TallyAdminPermission
    module = {g.module: [a.value for a in Action if g.allows(a)] for g in UserModulePermission.objects.filter(user=user)}
    tally = {g.product.code: [a for a in PERMISSION_ACTIONS if getattr(g, f"can_{a}")]
             for g in TallyAdminPermission.objects.filter(user=user).select_related("product")}
    return {"module_grants": {k: v for k, v in module.items() if v}, "tally_grants": {k: v for k, v in tally.items() if v}}


def describe(user):
    role = user.profile.role
    data = _grants(user)
    prof = getattr(user, "staff_profile", None)
    report = sorted(StaffReportProject.objects.filter(user=user).values_list("project_code", flat=True))
    dev = sorted(DeveloperProjectAccess.objects.filter(user=user).values_list("project_code", flat=True))
    projects = dev if role == Role.DEVELOPER else report if role == Role.USER else sorted({c.split(":")[0] for c in set(data["module_grants"]) | set(data["tally_grants"])})
    return {
        "id": user.pk, "username": user.username, "email": user.email, "role": role,
        "full_name": prof.full_name if prof else user.get_username(), "phone": prof.phone if prof else "",
        "is_active": user.is_active, "last_login": user.last_login.isoformat() if user.last_login else None,
        "projects": projects, **data, "report_projects": report, "developer_projects": dev,
        "visibility_projects": sorted(DashboardVisibilityGrant.objects.filter(user=user).values_list("project_code", flat=True)),
    }


def _audit(actor, target, action, detail=""):
    StaffPermissionAudit.objects.create(actor=actor, target_username=target.username, action=action, detail=detail[:2000])


def _clean(d, role):
    """Validate the whole payload before anything is written."""
    from apps.tallysl.models import PERMISSION_ACTIONS, TallyProduct
    errors = {}
    full_name = " ".join(str(d.get("full_name", "")).split())[:120]
    if not full_name:
        errors["full_name"] = ["Full name is required."]
    email = str(d.get("email", "")).strip()
    try:
        serializers.EmailField().run_validation(email)
    except serializers.ValidationError:
        errors["email"] = ["Enter a valid email address."]
    phone = str(d.get("phone", "")).strip()[:20]
    if phone and not re.fullmatch(r"[0-9+\-() ]{6,20}", phone):
        errors["phone"] = ["Enter a valid contact number."]
    module_grants = d.get("module_grants") or {}
    tally_grants = d.get("tally_grants") or {}
    visibility = d.get("visibility_projects") or []
    report_projects = d.get("report_projects") or []
    developer_projects = d.get("developer_projects") or []
    if not isinstance(developer_projects, list):
        raise ValidationError({"developer_projects": ["Invalid project list."]})
    if not (isinstance(module_grants, dict) and isinstance(tally_grants, dict) and isinstance(visibility, list) and isinstance(report_projects, list)):
        raise ValidationError({"grants": ["Invalid permission payload."]})
    catalog = project_catalog()
    valid_modules = set(permission_modules()) | set(api_modules())
    for module, actions in module_grants.items():
        if module not in valid_modules or not isinstance(actions, list) or any(a not in Action.values for a in actions):
            errors["module_grants"] = [f"Invalid grant for {module}."]
    tally_codes = set(TallyProduct.objects.values_list("code", flat=True))
    for code, actions in tally_grants.items():
        if code not in tally_codes or not isinstance(actions, list) or any(a not in PERMISSION_ACTIONS for a in actions):
            errors["tally_grants"] = [f"Invalid grant for {code}."]
    if any(c not in catalog for c in visibility):
        errors["visibility_projects"] = ["Unknown project."]
    if role == Role.DEVELOPER:
        if not developer_projects or any(c not in catalog for c in developer_projects):
            errors["developer_projects"] = ["Choose at least one valid project for this developer."]
        module_grants, tally_grants, visibility, report_projects = {}, {}, [], []
    elif role == Role.USER:
        if not report_projects or any(c not in catalog for c in report_projects):
            errors["report_projects"] = ["Choose at least one valid project for this report viewer."]
        module_grants, tally_grants, visibility = {}, {}, []  # a restricted User holds no other grants
    else:
        report_projects = []
    if errors:
        raise ValidationError(errors)
    return dict(full_name=full_name, email=email, phone=phone, module_grants=module_grants, tally_grants=tally_grants,
                visibility=sorted(set(visibility)), report_projects=sorted(set(report_projects)),
                developer_projects=sorted(set(developer_projects)) if role == Role.DEVELOPER else [])


def _apply(user, v):
    from apps.tallysl.models import PERMISSION_ACTIONS, TallyAdminPermission, TallyProduct
    for module in list(permission_modules()) + api_modules():
        UserModulePermission.objects.update_or_create(
            user=user, module=module, defaults={f"can_{a.value}": a.value in v["module_grants"].get(module, []) for a in Action})
    for product in TallyProduct.objects.all():
        TallyAdminPermission.objects.update_or_create(
            user=user, product=product, defaults={f"can_{a}": a in v["tally_grants"].get(product.code, []) for a in PERMISSION_ACTIONS})
    DashboardVisibilityGrant.objects.filter(user=user).exclude(project_code__in=v["visibility"]).delete()
    for code in v["visibility"]:
        DashboardVisibilityGrant.objects.get_or_create(user=user, project_code=code)
    DeveloperProjectAccess.objects.filter(user=user).exclude(project_code__in=v["developer_projects"]).delete()
    for code in v["developer_projects"]:
        DeveloperProjectAccess.objects.get_or_create(user=user, project_code=code)
    StaffReportProject.objects.filter(user=user).exclude(project_code__in=v["report_projects"]).delete()
    for code in v["report_projects"]:
        StaffReportProject.objects.get_or_create(user=user, project_code=code)


class StaffListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        users = get_user_model().objects.filter(profile__role__in=MANAGED).select_related("profile", "staff_profile").order_by("username")
        return Response({"staff": [describe(u) for u in users], "projects": [{"code": c, "name": n} for c, n in project_catalog().items()],
                         "catalog": build_catalog(), "action_labels": {a.value: a.label for a in Action}})

    def post(self, request):
        d = request.data
        role = d.get("role")
        if role not in MANAGED:
            raise ValidationError({"role": ["Choose Admin or User."]})
        username = str(d.get("username", "")).strip()
        if not USERNAME.match(username):
            raise ValidationError({"username": ["Use 3-40 letters, numbers, dot, dash or underscore."]})
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError({"username": ["This username is already taken."]})
        v = _clean(d, role)
        try:
            with transaction.atomic():
                user = User(username=username, email=v["email"], is_active=bool(d.get("is_active", True)))
                user.set_unusable_password()  # no password exists until the person sets one through the one-time link
                user.save()
                UserProfile.objects.create(user=user, role=role)
                StaffProfile.objects.create(user=user, full_name=v["full_name"], phone=v["phone"], created_by=request.user)
                _apply(user, v)
                _audit(request.user, user, "STAFF_CREATED", json.dumps({"role": role, **{k: v[k] for k in AUDITED}}))
        except IntegrityError:
            raise ValidationError({"username": ["This username is already taken."]})
        return Response({"staff": describe(user), "setup_link": setup_link(user),
                         "message": "Account created. Share the one-time setup link securely; no password was set or emailed."},
                        status=status.HTTP_201_CREATED)


def _managed(pk):
    User = get_user_model()
    try:
        return User.objects.select_related("profile").get(pk=pk, profile__role__in=MANAGED)
    except User.DoesNotExist:
        raise NotFound("Staff account not found.")  # Superadmin accounts are not managed here, so they cannot be demoted or deactivated


class StaffDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, pk):
        return Response({"staff": describe(_managed(pk))})

    def put(self, request, pk):
        user = _managed(pk)
        d = request.data
        v = _clean(d, user.profile.role)  # the role is fixed after creation
        with transaction.atomic():
            user.email = v["email"]
            if "is_active" in d:
                user.is_active = bool(d["is_active"])
            user.save()
            StaffProfile.objects.update_or_create(user=user, defaults={"full_name": v["full_name"], "phone": v["phone"]})
            _apply(user, v)
            _audit(request.user, user, "STAFF_UPDATED", json.dumps({"active": user.is_active, **{k: v[k] for k in AUDITED}}))
        return Response({"staff": describe(user)})  # grants are read from the database on every request, so this applies at once


class StaffSetupLinkView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        user = _managed(pk)
        _audit(request.user, user, "SETUP_LINK_ISSUED")
        return Response({"setup_link": setup_link(user)})


class SetupPasswordView(APIView):
    """Public and throttled. The signed token stops working once the password is set."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        User = get_user_model()
        bad = Response({"message": "This setup link is invalid or has expired.", "errors": {}}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(str(request.data.get("uid", "")))), profile__role__in=MANAGED)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            return bad
        if not user.is_active or not default_token_generator.check_token(user, str(request.data.get("token", ""))):
            return bad
        password = request.data.get("password")
        if not isinstance(password, str) or password != request.data.get("confirm_password"):
            raise ValidationError({"confirm_password": ["Passwords do not match."]})
        problems = services.validate_new_password(user, "", password)
        if problems:
            raise ValidationError({"password": problems})
        services.set_password(user, password)
        _audit(user, user, "PASSWORD_SETUP_COMPLETED")
        return Response({"message": "Password set. You can now sign in."})
