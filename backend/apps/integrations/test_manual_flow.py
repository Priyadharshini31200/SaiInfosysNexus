from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.services import ensure_account

from . import services as s
from .models import IntegrationCustomer, IntegrationLicense

ADM = "/api/integrations/admin/"


class ManualFlow(TestCase):
    def setUp(self):
        user, _ = ensure_account("boss3", Role.SUPERADMIN, "pw")
        self.c = APIClient()
        self.c.force_authenticate(user)
        self.code = self.c.post(f"{ADM}projects/", {"name": "Manual Flow", "tally_serial_required": False}, format="json").data["project"]["code"]
        self.n = 0

    def add(self, **over):
        self.n += 1
        body = {"name": f"Cust {self.n}", "company_name": "ACME", "email": f"c{self.n}@example.com", "contact_number": "9876543210",
                "license_type": "TRIAL", "gstin": "33ABCDE1234F1Z5", "address": "1 Main St", **over}
        return self.c.post(f"{ADM}projects/{self.code}/universe-customers/manual/", body, format="json")

    def act(self, pk, action, **body):
        return self.c.post(f"{ADM}projects/{self.code}/universe-customers/customer/{pk}/{action}/", body, format="json")

    def test_identity_password_and_gstin(self):
        r = self.add()
        self.assertEqual(r.status_code, 201, r.content)
        cust = IntegrationCustomer.objects.get(pk=r.data["customer_id"])
        self.assertEqual(cust.user_id, cust.serial_number)
        self.assertRegex(cust.serial_number, r"^9\d{8}$")
        self.assertEqual(cust.gstin, "33ABCDE1234F1Z5")
        self.assertTrue(r.data["temporary_password"] and r.data["temporary_password"] != cust.password_hash)
        self.assertNotIn(r.data["temporary_password"], str(self.c.get(f"{ADM}projects/{self.code}/universe-customers/customer/{cust.pk}/").content))
        self.assertNotIn(r.data["temporary_password"], str(self.c.get(f"{ADM}projects/{self.code}/universe-customers/").content))

    def test_gstin_optional_and_validated(self):
        self.assertEqual(self.add(gstin="").status_code, 201)
        self.assertEqual(self.add(gstin="BAD").status_code, 400)

    def test_license_terms(self):
        now = timezone.now()
        t = self.add(license_type="TRIAL", trial_days=5).data["license"]
        self.assertAlmostEqual((_d(t["expires_at"]) - now).total_seconds(), 5 * 86400, delta=120)
        y = self.add(license_type="ONE_YEAR", years=2).data["license"]
        self.assertEqual(_d(y["expires_at"]).year, (s.add_calendar_years(now, 2)).year)
        self.assertAlmostEqual((_d(y["expires_at"]) - s.add_calendar_years(now, 2)).total_seconds(), 0, delta=120)
        life = self.add(license_type="LIFETIME").data["license"]
        self.assertIsNone(life["expires_at"])
        self.assertEqual(self.add(license_type="TRIAL", trial_days=0).status_code, 422)

    def test_expiry_enforced_edit_extend_renew_keep_identity(self):
        d = self.add(license_type="TRIAL", trial_days=3).data
        pk, uid = d["customer_id"], d["user_id"]
        lic = IntegrationLicense.objects.get(customer_id=pk)
        lic.expires_at = timezone.now() - timedelta(days=1)
        lic.save()
        self.assertEqual(s.license_state(IntegrationLicense.objects.get(pk=lic.pk)), "EXPIRED")
        r = self.c.patch(f"{ADM}projects/{self.code}/universe-customers/customer/{pk}/", {"customer_name": "Renamed", "address": "New addr", "gstin": "33ABCDE1234F1Z5"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        cust = IntegrationCustomer.objects.get(pk=pk)
        self.assertEqual((cust.name, cust.address, cust.user_id, cust.serial_number, cust.company_name), ("Renamed", "New addr", uid, uid, "ACME"))
        r = self.act(pk, "extend-trial", days=7)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(s.license_state(IntegrationLicense.objects.get(pk=lic.pk)), "ACTIVE")
        r = self.act(pk, "set-license", license_type="ONE_YEAR", years=1)
        self.assertEqual(r.status_code, 200, r.content)
        r = self.act(pk, "renew")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(IntegrationCustomer.objects.get(pk=pk).user_id, uid)
        self.assertEqual(IntegrationCustomer.objects.filter(project__code=self.code).count(), 1)

    def test_admin_without_grant_cannot_add(self):
        admin, _ = ensure_account("adm1", Role.ADMIN, "pw")
        c = APIClient()
        c.force_authenticate(admin)
        r = c.post(f"{ADM}projects/{self.code}/universe-customers/manual/", {"name": "X"}, format="json")
        self.assertIn(r.status_code, (401, 403))


def _d(v):
    from django.utils.dateparse import parse_datetime
    return v if hasattr(v, "year") else parse_datetime(v)


class ManualProjectSave(TestCase):
    def setUp(self):
        user, _ = ensure_account("boss4", Role.SUPERADMIN, "pw")
        self.c = APIClient()
        self.c.force_authenticate(user)
        self.body = {"project_name": " new shop ", "name": "Asha", "email": "asha@example.com", "contact_number": "9876543210", "license_type": "LIFETIME"}

    def test_project_and_first_customer_in_one_save(self):
        from .models import IntegrationProject
        r = self.c.post(f"{ADM}universe/manual-project/", self.body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        p = IntegrationProject.objects.get(code=r.data["project"]["code"])
        self.assertEqual((p.name, p.registration_method, p.tally_serial_required, p.customers.count()), ("NEW SHOP", "MANUAL", False, 1))
        self.assertRegex(r.data["user_id"], r"^9\d{8}$")

    def test_invalid_customer_creates_no_project(self):
        from .models import IntegrationProject
        before = IntegrationProject.objects.count()  # migrations seed legacy projects
        r = self.c.post(f"{ADM}universe/manual-project/", {**self.body, "email": "bad"}, format="json")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(IntegrationProject.objects.count(), before)
        r2 = self.c.post(f"{ADM}universe/manual-project/", {**self.body, "license_type": "TRIAL", "trial_days": 0}, format="json")
        self.assertEqual(r2.status_code, 422, r2.content)
        self.assertEqual(IntegrationProject.objects.count(), before)

    def test_duplicate_project_name_rejected(self):
        self.c.post(f"{ADM}universe/manual-project/", self.body, format="json")
        r = self.c.post(f"{ADM}universe/manual-project/", {**self.body, "email": "other@example.com"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("project_name", r.data["errors"])

    def test_chosen_temporary_password_is_hashed_and_validated(self):
        from django.contrib.auth.hashers import check_password
        r = self.c.post(f"{ADM}universe/manual-project/", {**self.body, "temporary_password": "Abcd2345xy"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        cust = IntegrationCustomer.objects.get(pk=r.data["customer_id"])
        self.assertTrue(check_password("Abcd2345xy", cust.password_hash))
        self.assertNotEqual(cust.password_hash, "Abcd2345xy")
        self.assertEqual(self.c.post(f"{ADM}universe/manual-project/", {**self.body, "project_name": "Other", "email": "z@example.com", "temporary_password": "short"}, format="json").status_code, 400)


class TallySlProjects(TestCase):
    def setUp(self):
        user, _ = ensure_account("boss5", Role.SUPERADMIN, "pw")
        self.c = APIClient()
        self.c.force_authenticate(user)

    def make(self, name, tally):
        return self.c.post(f"{ADM}projects/", {"name": name, "category": "tally", "tally_serial_required": tally, "developer_email": "d@example.com"}, format="json")

    def test_tally_yes_and_no_both_api_setup_pending_in_tally_directory(self):
        from .models import IntegrationProject
        for name, tally in (("TS YES", True), ("TS NO", False)):
            r = self.make(name, tally)
            self.assertEqual(r.status_code, 201, r.content)
            p = IntegrationProject.objects.get(code=r.data["project"]["code"])
            self.assertEqual((p.status, p.tally_serial_required, p.registration_method == "MANUAL"), ("SETUP_PENDING", tally, False))
        names = lambda t: {x["name"] for x in self.c.get(f"{ADM}universe/hub/?type={t}").data["results"]}
        self.assertTrue({"TS YES", "TS NO"} <= names("tally"))
        self.assertFalse({"TS YES", "TS NO"} & names("non-tally"))
        self.assertEqual(self.make("TS BAD", False).status_code, 201)
        self.assertEqual(self.c.post(f"{ADM}projects/", {"name": "TS X", "category": "tally", "tally_serial_required": False}, format="json").status_code, 400)  # developer email required

    def test_tally_no_project_rejects_manual_customer(self):
        code = self.make("TS NOMAN", False).data["project"]["code"]
        self.assertEqual(self.c.post(f"{ADM}projects/{code}/universe-customers/manual/", {"name": "X"}, format="json").status_code, 400)


class DeleteAndEdit(TestCase):
    def setUp(self):
        user, _ = ensure_account("boss6", Role.SUPERADMIN, "pw")
        self.c = APIClient()
        self.c.force_authenticate(user)

    def test_empty_project_deleted_project_with_customer_protected_edit_keeps_code(self):
        from .models import IntegrationProject
        empty = self.c.post(f"{ADM}projects/", {"name": "Empty One", "category": "tally", "tally_serial_required": False, "developer_email": "d@example.com"}, format="json").data["project"]["code"]
        r = self.c.patch(f"{ADM}projects/{empty}/", {"name": "Empty Renamed", "developer_email": "n@example.com"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        p = IntegrationProject.objects.get(code=empty)
        self.assertEqual((p.name, p.code), ("EMPTY RENAMED", empty))
        self.assertEqual(self.c.delete(f"{ADM}projects/{empty}/").status_code, 204)
        self.assertFalse(IntegrationProject.objects.filter(code=empty).exists())
        d = self.c.post(f"{ADM}universe/manual-project/", {"project_name": "Has Data", "name": "Asha", "email": "a@example.com", "contact_number": "9876543210", "license_type": "LIFETIME"}, format="json")
        code = d.data["project"]["code"]
        r = self.c.delete(f"{ADM}projects/{code}/")
        self.assertEqual(r.status_code, 409)
        self.assertTrue(IntegrationProject.objects.filter(code=code).exists())
        self.assertEqual(self.c.patch(f"{ADM}projects/{code}/", {"lifecycle": "archive"}, format="json").status_code, 200)
        dash = self.c.get(f"{ADM}projects/{code}/universe-dashboard/").data
        self.assertEqual((dash["stats"]["total_customers"], len(dash["cards"])), (1, 4))

    def test_legacy_project_cannot_be_deleted(self):
        from .models import IntegrationProject
        legacy = IntegrationProject.objects.exclude(license_policy="CALENDAR").first()
        if legacy:
            self.assertEqual(self.c.delete(f"{ADM}projects/{legacy.code}/").status_code, 403)


class DeleteCustomer(TestCase):
    def setUp(self):
        user, _ = ensure_account("boss7", Role.SUPERADMIN, "pw")
        self.c = APIClient()
        self.c.force_authenticate(user)
        self.a = self.c.post(f"{ADM}universe/manual-project/", {"project_name": "Del A", "name": "Asha", "email": "a@example.com", "contact_number": "9876543210", "license_type": "LIFETIME"}, format="json").data
        self.b = self.c.post(f"{ADM}universe/manual-project/", {"project_name": "Del B", "name": "Ben", "email": "b@example.com", "contact_number": "9876543211", "license_type": "LIFETIME"}, format="json").data

    def url(self, code, pk):
        return f"{ADM}projects/{code}/universe-customers/customer/{pk}/"

    def test_project_scoped_delete_with_history_protection(self):
        from .models import IntegrationSession
        ca, cb = self.a["customer_id"], self.b["customer_id"]
        self.assertEqual(self.c.delete(self.url(self.a["project"]["code"], cb)).status_code, 404)  # another project's customer
        lic = IntegrationLicense.objects.get(customer_id=ca)
        IntegrationSession.objects.create(license=lic, token_hash="x" * 64, device_id="dev1")
        self.assertEqual(self.c.delete(self.url(self.a["project"]["code"], ca)).status_code, 409)
        self.assertTrue(IntegrationCustomer.objects.filter(pk=ca).exists())
        self.assertEqual(self.c.delete(self.url(self.b["project"]["code"], cb)).status_code, 204)
        self.assertFalse(IntegrationCustomer.objects.filter(pk=cb).exists())
        self.assertTrue(IntegrationCustomer.objects.filter(pk=ca).exists())

    def test_admin_without_delete_grant_forbidden(self):
        admin, _ = ensure_account("adm2", Role.ADMIN, "pw")
        c = APIClient()
        c.force_authenticate(admin)
        self.assertIn(c.delete(self.url(self.a["project"]["code"], self.a["customer_id"])).status_code, (401, 403))




class HubSummary(TestCase):
    def test_summary_counts_are_real_and_archived_filter_works(self):
        user, _ = ensure_account("boss10", Role.SUPERADMIN, "pw")
        c = APIClient()
        c.force_authenticate(user)
        codes = [c.post(f"{ADM}projects/", {"name": n, "category": "tally", "tally_serial_required": True, "developer_email": "d@example.com"}, format="json").data["project"]["code"] for n in ("SUM ONE", "SUM TWO")]
        c.patch(f"{ADM}projects/{codes[1]}/", {"lifecycle": "archive"}, format="json")
        r = c.get(f"{ADM}universe/hub/?type=tally").data
        self.assertEqual(r["summary"]["total"], 2)
        self.assertEqual((r["summary"]["pending"], r["summary"]["archived"]), (1, 1))
        arch = c.get(f"{ADM}universe/hub/?type=tally&status=archived").data
        self.assertEqual([x["code"] for x in arch["results"]], [codes[1]])
        self.assertTrue(all("tally_required" in x for x in r["results"]))
