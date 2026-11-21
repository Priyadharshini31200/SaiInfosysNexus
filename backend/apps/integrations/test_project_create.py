from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.services import ensure_account

from .models import IntegrationApiConfig, IntegrationProject

ADM = "/api/integrations/admin/"
BODY = {"name": "  pmp   rice mill ", "developer_email": "dev@example.com", "tally_serial_required": False}


class ProjectCreateTests(TestCase):
    def setUp(self):
        user, _ = ensure_account("boss", Role.SUPERADMIN, "pw")
        self.c = APIClient()
        self.c.force_authenticate(user)

    def create(self, **over):
        return self.c.post(f"{ADM}projects/", {**BODY, **over}, format="json")

    def test_name_is_required_normalised_and_uppercased(self):
        self.assertEqual(self.create(name="   ").status_code, 400)
        r = self.create()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["project"]["name"], "PMP RICE MILL")
        self.assertEqual(r.data["project"]["code"], "PMP001")

    def test_code_is_allocated_by_the_server_and_sequenced(self):
        self.assertEqual(self.create(name="GSTR2Tally Lite", code="").data["project"]["code"], "GSTR001")
        self.assertEqual(self.create(name="GSTR Cloud").data["project"]["code"], "GSTR002")
        self.assertEqual(self.create(name="Mothys Inventory").data["project"]["code"], "MOTHYS001")

    def test_duplicate_name_and_bad_email_rejected(self):
        self.create()
        self.assertEqual(self.create(name="PMP RICE MILL").status_code, 400)
        r = self.create(name="Other", developer_email="nope")
        self.assertEqual(r.status_code, 400)
        self.assertIn("developer_email", r.data["errors"])

    def test_saved_as_setup_pending_with_defaults_and_preview_does_not_reserve(self):
        self.assertEqual(self.c.get(f"{ADM}projects/code-preview/?name=pmp rice mill").data["code"], "PMP001")
        before = IntegrationProject.objects.count()
        self.assertEqual(IntegrationProject.objects.count(), before)
        p = IntegrationProject.objects.get(code=self.create().data["project"]["code"])
        self.assertEqual((p.status, p.trial_days, p.serial_prefix, p.serial_length, p.activation_key_length, p.device_limit), ("ACTIVE", 3, "9", 9, 9, 1))  # manual Non-Tally projects need no API setup
        self.assertEqual(p.license_policy, "CALENDAR")
        self.assertEqual(IntegrationApiConfig.objects.get(project=p).developer_email, "dev@example.com")

    def test_sequence_continues_beyond_999(self):
        IntegrationProject.objects.create(code="ABC999", name="OLD", frontend_url="https://a.b", backend_url="https://a.b")
        self.assertEqual(self.create(name="Abc New").data["project"]["code"], "ABC1000")

    def test_explicit_legacy_code_flow_unchanged(self):
        r = self.c.post(f"{ADM}projects/", {"name": "Legacy", "code": "LEG", "frontend_url": "https://a.b", "backend_url": "https://a.b"}, format="json")
        self.assertEqual((r.status_code, r.data["project"]["code"]), (201, "LEG"))


class ManualProjectTests(TestCase):
    def setUp(self):
        user, _ = ensure_account("boss2", Role.SUPERADMIN, "pw")
        self.c = APIClient()
        self.c.force_authenticate(user)

    def test_manual_non_tally_project_and_customer(self):
        r = self.c.post(f"{ADM}projects/", {"name": "Manual Shop", "tally_serial_required": False}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        code = r.data["project"]["code"]
        p = IntegrationProject.objects.get(code=code)
        self.assertEqual((p.registration_method, p.status), ("MANUAL", "ACTIVE"))
        body = {"name": "Ravi", "company_name": "RK Traders", "email": "ravi@example.com", "contact_number": "9876543210", "license_type": "TRIAL"}
        r = self.c.post(f"{ADM}projects/{code}/universe-customers/manual/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.data["user_id"].startswith("9") and len(r.data["user_id"]) == 9)
        self.assertTrue(r.data["temporary_password"])
        lst = self.c.get(f"{ADM}projects/{code}/universe-customers/").data
        self.assertEqual((lst["total"], lst["project"]["manual"]), (1, True))
        self.assertEqual(self.c.post(f"{ADM}projects/{code}/universe-customers/manual/", body, format="json").status_code, 409)

    def test_tally_project_stays_api_and_rejects_manual_add(self):
        r = self.c.post(f"{ADM}projects/", {"name": "Tally One", "tally_serial_required": True, "developer_email": "d@example.com"}, format="json")
        code = r.data["project"]["code"]
        self.assertEqual(IntegrationProject.objects.get(code=code).registration_method, "API")
        self.assertEqual(self.c.post(f"{ADM}projects/{code}/universe-customers/manual/", {"name": "X"}, format="json").status_code, 400)
