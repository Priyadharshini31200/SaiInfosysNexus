import re

from django.core import mail
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from .models import IntegrationCustomer
from .test_universe_customers import CustomerCentric


class SetupEmail(CustomerCentric):
    def approve_ok(self):
        pk = self.reg().pk
        r = self.staff.post(f"{self.base}registration/{pk}/approve/", {"license_type": "TRIAL"}, format="json", HTTP_ORIGIN="http://localhost:5173")
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def test_email_has_user_id_and_one_time_link_but_no_secret(self):
        mail.outbox.clear()
        out = self.approve_ok()
        msgs = [m for m in mail.outbox if "Account Is Ready" in m.subject]
        self.assertEqual(len(msgs), 1)
        body = msgs[0].body
        self.assertIn(out["result"]["user_id"], body)
        # current rule (Universal): the first Save & Allow emails the one-time temporary password, and never a localhost setup link
        self.assertIn(out["result"]["temporary_password"], body)
        self.assertNotIn("setup-customer-password", body)
        self.assertNotIn("localhost", body)
        self.assertNotIn("activation key", body.lower().replace("internal activation key", ""))
        self.assertEqual(msgs[0].to, ["alpha@example.com"])

    def test_link_sets_password_once_and_expires_on_use(self):
        self.approve_ok()
        c0 = IntegrationCustomer.objects.get(email="alpha@example.com")
        mail.outbox.clear()  # the setup link now travels in the staff "Resend" email from a public (non-local) Nexus address
        self.staff.post(f"{self.base}customer/{c0.pk}/resend-setup/", {}, format="json", HTTP_ORIGIN="https://nexus.example.com")
        token = re.search(r"token=([\w\-:]+)", [m for m in mail.outbox if "Account Is Ready" in m.subject][0].body).group(1)
        pub = APIClient()
        self.assertEqual(pub.post("/api/integrations/public/customer-setup/", {"token": token, "password": "short"}, format="json").status_code, 400)
        self.assertEqual(pub.post("/api/integrations/public/customer-setup/", {"token": token, "password": "GoodPass123"}, format="json").status_code, 200)
        c = IntegrationCustomer.objects.get(email="alpha@example.com")
        self.assertFalse(c.must_change_password)
        self.assertEqual(pub.post("/api/integrations/public/customer-setup/", {"token": token, "password": "Another123"}, format="json").status_code, 400)
        self.assertEqual(pub.post("/api/integrations/public/customer-setup/", {"token": "garbage", "password": "GoodPass123"}, format="json").status_code, 400)

    def test_resend_setup_link_is_a_staff_action_and_sends_again(self):
        self.approve_ok()
        c = IntegrationCustomer.objects.get(email="alpha@example.com")
        mail.outbox.clear()
        r = self.staff.post(f"{self.base}customer/{c.pk}/resend-setup/", {}, format="json", HTTP_ORIGIN="http://localhost:5173")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len([m for m in mail.outbox if "Account Is Ready" in m.subject]), 1)

    def test_mail_failure_never_undoes_the_approval(self):
        from unittest import mock
        with mock.patch("apps.registrations.mailer.deliver", return_value="failed"):
            out = self.approve_ok()
        self.assertEqual(out["result"]["setup_email"], "failed")
        self.assertTrue(IntegrationCustomer.objects.filter(email="alpha@example.com").exists())


class ProjectScopedDocsTests(SimpleTestCase):
    """API documentation for one project must never show another project's code (e.g. XZX docs containing MOTHYS)."""

    def test_markdown_and_openapi_use_selected_project_code(self):
        from apps.integrations import spec

        class Project:
            code, name = "XZX001", "XZX"
        md = spec.markdown("https://example.test/nexus/api", Project())
        oa = str(spec.openapi("https://example.test/nexus/api", "XZX001"))
        self.assertNotIn("MOTHYS", md)
        self.assertNotIn("MOTHYS", oa)
        self.assertIn('"project_code": "XZX001"', md)
        self.assertIn("MOTHYS", str(spec.ENDPOINTS))  # the shared contract itself is not mutated
        self.assertIn('"project_code": "MOTHYS"', spec.markdown("https://example.test/nexus/api", type("M", (), {"code": "MOTHYS", "name": "Mothys"})()))
