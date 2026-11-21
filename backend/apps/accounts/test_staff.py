from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import DashboardVisibilityGrant, StaffPermissionAudit

User = get_user_model()
STAFF = "/api/config/staff/"


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class StaffTests(TestCase):
    def setUp(self):
        call_command("seed_initial_users", verbosity=0)
        self.sa = APIClient()
        self.sa.force_authenticate(User.objects.get(username__iexact="Superadmin"))

    def create(self, **over):
        body = {"full_name": "Asha K", "username": "asha", "email": "asha@example.com", "phone": "9876543210", "role": "ADMIN",
                "module_grants": {"gstr2tally": ["view"]}, "visibility_projects": ["gstr2tally"]}
        body.update(over)
        return self.sa.post(STAFF, body, format="json")

    def test_create_admin_with_setup_link_and_no_password(self):
        r = self.create()
        self.assertEqual(r.status_code, 201)
        u = User.objects.get(username="asha")
        self.assertFalse(u.has_usable_password())
        self.assertIn("/setup-password?uid=", r.data["setup_link"])
        self.assertNotIn("password", str(r.data["staff"]).lower().replace("setup", ""))
        self.assertTrue(StaffPermissionAudit.objects.filter(target_username="asha", action="STAFF_CREATED").exists())
        self.assertTrue(DashboardVisibilityGrant.objects.filter(user=u, project_code="gstr2tally").exists())

    def test_setup_link_sets_password_once_then_login_works(self):
        link = self.create().data["setup_link"]
        uid, token = link.split("uid=")[1].split("&token=")
        c = APIClient()
        self.assertEqual(c.post("/api/auth/setup-password/", {"uid": uid, "token": token, "password": "Secret9", "confirm_password": "Secret9"}, format="json").status_code, 200)
        self.assertEqual(c.post("/api/auth/setup-password/", {"uid": uid, "token": token, "password": "Other9", "confirm_password": "Other9"}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/auth/login/", {"username": "asha", "password": "Secret9"}, format="json").status_code, 200)

    def test_only_superadmin_manages_staff(self):
        admin = APIClient(); admin.force_authenticate(User.objects.get(username__iexact="Admin"))
        self.assertEqual(admin.get(STAFF).status_code, 403)
        self.assertEqual(admin.post(STAFF, {}, format="json").status_code, 403)
        self.assertEqual(APIClient().get(STAFF).status_code, 401)

    def test_validation_and_duplicates(self):
        self.create()
        self.assertEqual(self.create().status_code, 400)  # duplicate username
        self.assertEqual(self.create(username="x1", role="SUPERADMIN").status_code, 400)
        self.assertEqual(self.create(username="x2", module_grants={"nope": ["view"]}).status_code, 400)
        self.assertEqual(self.create(username="x3", email="bad").status_code, 400)
        self.assertEqual(self.create(username="x4", role="USER").status_code, 400)  # report viewer needs projects

    def test_admin_sees_only_granted_and_cannot_manage_other_visibility(self):
        self.create(visibility_projects=["gstr2tally"])
        u = User.objects.get(username="asha")
        c = APIClient(); c.force_authenticate(u)
        v = "/api/dashboard/card-visibility/"
        self.assertEqual(c.put(f"{v}gstr2tally/", {"mode": "hidden", "version": 0}, format="json").status_code, 200)
        self.assertEqual(c.put(f"{v}prn-auto-print/", {"mode": "hidden", "version": 0}, format="json").status_code, 403)
        self.assertEqual(c.get("/api/dashboard/project-usage/").status_code, 403)  # no dashboard grant on any project
        self.assertEqual(c.get(STAFF).status_code, 403)

    def test_report_user_is_restricted(self):
        r = self.create(username="rep", role="USER", report_projects=["gstr2tally"], module_grants={"prn-auto-print": ["view"]}, visibility_projects=["prn-auto-print"])
        self.assertEqual(r.status_code, 201)
        u = User.objects.get(username="rep")
        self.assertFalse(DashboardVisibilityGrant.objects.filter(user=u).exists())  # extra grants are dropped for a User
        c = APIClient(); c.force_authenticate(u)
        me = c.get("/api/auth/me/")
        self.assertEqual((me.status_code, me.data["user"]["role"], me.data["user"]["report_projects"]), (200, "USER", ["gstr2tally"]))
        ok = c.get("/api/staff/expiry-report/")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual([p["code"] for p in ok.data["projects"]], ["gstr2tally"])
        self.assertEqual(c.get("/api/staff/expiry-report/?project=prn-auto-print").status_code, 400)
        for url in ("/api/dashboard/project-usage/", "/api/dashboard/sync/", "/api/reports/license-expiry/", STAFF, "/api/config/project-permissions/", "/api/projects/prn-auto-print/customers/"):
            self.assertIn(c.get(url).status_code, (403, 404), url)
        self.assertEqual(c.put("/api/dashboard/card-visibility/gstr2tally/", {"mode": "hidden", "version": 0}, format="json").status_code, 403)
        self.assertEqual(c.post("/api/auth/logout/").status_code, 200)

    def test_update_deactivate_and_no_superadmin_management(self):
        uid = self.create().data["staff"]["id"]
        r = self.sa.put(f"{STAFF}{uid}/", {"full_name": "Asha K", "email": "asha@example.com", "is_active": False, "module_grants": {}, "visibility_projects": []}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.get(pk=uid).is_active)
        self.assertEqual(StaffPermissionAudit.objects.filter(action="STAFF_UPDATED").count(), 1)
        sa_id = User.objects.get(username__iexact="Superadmin").pk
        self.assertEqual(self.sa.put(f"{STAFF}{sa_id}/", {"full_name": "x", "email": "a@b.co", "is_active": False}, format="json").status_code, 404)
        with self.assertRaises(ValueError):
            StaffPermissionAudit.objects.first().delete()


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class ApiProjectDelegationTests(TestCase):
    """Admin access to API-connected products (Mothys): explicit, project-scoped, enforced by the backend."""

    def setUp(self):
        from apps.integrations.models import IntegrationProject
        call_command("seed_initial_users", verbosity=0)
        self.sa = APIClient()
        self.sa.force_authenticate(User.objects.get(username__iexact="Superadmin"))
        for code in ("MOTHYS", "OTHERP"):
            IntegrationProject.objects.get_or_create(code=code, defaults={"name": code.title(), "frontend_url": "https://x.example.com", "backend_url": "https://x.example.com/api"})

    def make(self, username, role="ADMIN", **over):
        body = {"full_name": "T U", "username": username, "email": f"{username}@example.com", "role": role}
        body.update(over)
        r = self.sa.post(STAFF, body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        c = APIClient(); c.force_authenticate(User.objects.get(username=username))
        return c

    def test_catalog_lists_real_menus_for_every_kind(self):
        cat = {p["code"]: p for p in self.sa.get(STAFF).data["catalog"]}
        self.assertEqual(cat["MOTHYS"]["kind"], "api")
        self.assertIn("approve", [a for m in cat["MOTHYS"]["menus"] for a in m["actions"]])
        self.assertIn("prn-auto-print", cat)

    def test_admin_only_gets_the_granted_mothys_actions(self):
        c = self.make("amy", module_grants={"MOTHYS:dashboard": ["view"], "MOTHYS": ["view", "edit"]})
        base = "/api/integrations/admin/projects/"
        self.assertEqual(c.get(f"{base}MOTHYS/dashboard/").status_code, 200)
        self.assertEqual(c.get(f"{base}MOTHYS/product-customers/").status_code, 200)
        self.assertEqual(c.get(f"{base}MOTHYS/reports/expiry/").status_code, 403)          # no report grant
        self.assertEqual(c.post(f"{base}MOTHYS/product-customers/1/approve/", {}, format="json").status_code, 403)  # no approve grant
        self.assertEqual(c.post(f"{base}MOTHYS/product-customers/export/", {}, format="json").status_code, 403)     # no export grant
        self.assertEqual(c.get(f"{base}OTHERP/dashboard/").status_code, 403)               # other project: no access at all
        self.assertEqual(c.get("/api/integrations/admin/projects/").status_code, 403)       # project management stays Superadmin-only

    def test_revoking_access_takes_effect_immediately_and_user_role_is_denied(self):
        c = self.make("bob", module_grants={"MOTHYS:dashboard": ["view"]})
        uid = User.objects.get(username="bob").pk
        url = "/api/integrations/admin/projects/MOTHYS/dashboard/"
        self.assertEqual(c.get(url).status_code, 200)
        self.sa.put(f"{STAFF}{uid}/", {"full_name": "T U", "email": "bob@example.com", "module_grants": {}}, format="json")
        self.assertEqual(c.get(url).status_code, 403)
        viewer = self.make("vera", role="USER", report_projects=["MOTHYS"])
        self.assertEqual(viewer.get(url).status_code, 403)
        self.assertEqual(self.sa.get(url).status_code, 200)  # Superadmin unchanged
