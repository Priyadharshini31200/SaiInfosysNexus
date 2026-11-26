import json
from unittest import mock

from django.core import mail
from django.test import override_settings

from . import pull
from .models import IntegrationCustomer, IntegrationLicense, IntegrationProject, IntegrationSyncCheckpoint, IntegrationSyncLog
from .tests import ADM, Base

CONFIG = {
    "customer_method": "GET", "customer_path": "/customers", "auth_type": "NONE",
    "field_mapping": {"external_customer_id": "id", "customer_name": "name", "email": "contact.email", "contact_number": "contact.phone"},
    "pagination": {"type": "page", "page_param": "page", "size_param": "size", "page_size": 2, "results_path": "data"},
    "sync_mode": "POLL", "sync_interval_minutes": 15, "developer_name": "Dev", "developer_email": "dev@example.com",
}


def rec(i, **over):
    return {"id": f"X-{i}", "name": f"Customer {i}", "contact": {"email": f"c{i}@example.com", "phone": f"98765432{i:02d}"}, **over}


class Resp:
    def __init__(self, status, payload):
        self.status_code, self.is_redirect, self.is_permanent_redirect = status, False, False
        body = json.dumps(payload).encode()
        self.raw = mock.Mock(read=lambda n, decode_content=True: body)


def fake_api(pages):
    """pages: {page_number: list_of_records | int status}."""
    def request(method, url, headers=None, params=None, **kw):
        p = pages.get(params.get("page"), [])
        return Resp(p, {}) if isinstance(p, int) else Resp(200, {"data": p})
    return request


@mock.patch.object(pull, "BACKOFF", 0)
@mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
class PullSyncTests(Base):
    def configure(self, **over):
        return self.staff.put(f"{ADM}projects/MOTHYS/api-config/", {**CONFIG, **over}, format="json")

    def test_config_validation_and_audit(self, _dns):
        self.assertEqual(self.configure(field_mapping={"email": "x"}).status_code, 400)
        self.assertEqual(self.configure(customer_path="customers").status_code, 400)
        self.assertEqual(self.configure(auth_type="OAUTH").status_code, 400)
        r = self.configure()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["config"]["field_mapping"]["email"], "contact.email")
        self.assertNotIn("secret", json.dumps(r.data).lower().replace("credential_ref", ""))

    def test_ssrf_guard(self, dns):
        dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        self.assertEqual(self.configure().status_code, 400)  # host resolves to loopback
        dns.return_value = [(2, 1, 6, "", ("169.254.169.254", 80))]
        with self.assertRaises(pull.UnsafeUrl):
            pull.validate_outbound_url("http://metadata.example.com/latest")
        dns.return_value = [(2, 1, 6, "", ("10.0.0.5", 443))]
        with self.assertRaises(pull.UnsafeUrl):
            pull.validate_outbound_url("https://internal.example.com/x")
        for bad in ("ftp://a.example.com", "https://user:pw@a.example.com", "file:///etc/passwd", ""):
            with self.assertRaises(pull.UnsafeUrl):
                pull.validate_outbound_url(bad)

    def test_sync_paginates_creates_and_is_idempotent(self, _dns):
        self.configure()
        pages = {1: [rec(1), rec(2)], 2: [rec(3)]}
        with mock.patch("apps.integrations.pull.requests.request", side_effect=fake_api(pages)):
            r = self.staff.post(f"{ADM}projects/MOTHYS/sync/")
            self.assertEqual((r.status_code, r.data["result"]["created"], r.data["result"]["pages"]), (200, 3, 2))
            again = self.staff.post(f"{ADM}projects/MOTHYS/sync/")
            self.assertEqual((again.data["result"]["created"], again.data["result"]["existing"]), (0, 3))
        self.assertEqual(IntegrationCustomer.objects.filter(project=self.project).count(), 3)
        self.assertEqual(IntegrationLicense.objects.filter(customer__project=self.project).count(), 3)  # no duplicate licences
        c = IntegrationCustomer.objects.get(external_customer_id="X-1")
        self.assertEqual((c.email, c.user_id, c.serial_number), ("c1@example.com", c.serial_number, c.serial_number))
        self.assertEqual(len([m for m in mail.outbox if "Your Login Details" in m.subject]), 3)  # welcome mail only for new customers
        self.assertEqual(IntegrationSyncCheckpoint.objects.get(project=self.project).status, "OK")

    def test_bad_record_is_logged_others_saved(self, _dns):
        self.configure()
        bad = rec(2, contact={"email": "not-an-email", "phone": "123"})
        with mock.patch("apps.integrations.pull.requests.request", side_effect=fake_api({1: [rec(1), bad]})):
            r = self.staff.post(f"{ADM}projects/MOTHYS/sync/")
        self.assertEqual((r.data["result"]["created"], r.data["result"]["failed"]), (1, 1))
        log = IntegrationSyncLog.objects.get(project=self.project, status="FAILED")
        self.assertEqual((log.external_customer_id, log.error_code), ("X-2", "VALIDATION_ERROR"))

    def test_api_failure_retries_logs_and_resumes_from_checkpoint(self, _dns):
        self.configure()
        calls = []

        def flaky(method, url, headers=None, params=None, **kw):
            calls.append(params["page"])
            if params["page"] == 2:
                return Resp(503, {})
            return Resp(200, {"data": [rec(1), rec(2)]})

        with mock.patch("apps.integrations.pull.requests.request", side_effect=flaky):
            r = self.staff.post(f"{ADM}projects/MOTHYS/sync/")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(calls.count(2), pull.RETRIES)  # retried with backoff, then gave up
        cp = IntegrationSyncCheckpoint.objects.get(project=self.project)
        self.assertEqual((cp.status, cp.next_page), ("FAILED", 2))
        self.assertTrue(IntegrationSyncLog.objects.filter(project=self.project, status="FAILED", error_code="EXTERNAL_API_UNAVAILABLE").exists())
        seen = []

        def ok(method, url, headers=None, params=None, **kw):
            seen.append(params["page"])
            return Resp(200, {"data": [rec(3)]})

        with mock.patch("apps.integrations.pull.requests.request", side_effect=ok):
            self.assertEqual(self.staff.post(f"{ADM}projects/MOTHYS/sync/").status_code, 200)
        self.assertEqual(seen[0], 2)  # resumed after the last good page
        self.assertEqual(IntegrationCustomer.objects.filter(project=self.project).count(), 3)

    def test_redirect_is_not_followed(self, _dns):
        self.configure()
        redirect = Resp(302, {})
        redirect.is_redirect = True
        with mock.patch("apps.integrations.pull.requests.request", return_value=redirect):
            self.assertEqual(self.staff.post(f"{ADM}projects/MOTHYS/sync/").status_code, 502)

    def test_disabled_or_unconfigured_project_and_permissions(self, _dns):
        self.assertEqual(self.staff.post(f"{ADM}projects/MOTHYS/sync/").status_code, 409)  # not configured
        from rest_framework.test import APIClient
        anon = APIClient()
        self.assertIn(anon.post(f"{ADM}projects/MOTHYS/sync/").status_code, (401, 403))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_send_docs_records_history_and_never_includes_secret(self, _dns):
        mail.outbox.clear()
        r = self.staff.post(f"{ADM}projects/MOTHYS/send-docs/", {"to": "dev@example.com"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.staff.post(f"{ADM}projects/MOTHYS/send-docs/", {"to": "nope"}, format="json").status_code, 400)
        hist = self.staff.get(f"{ADM}projects/MOTHYS/send-docs/").data["history"]
        self.assertEqual([h["to"] for h in hist], ["dev@example.com"])
        sent = [m for m in mail.outbox if "API integration" in m.subject]
        self.assertTrue(sent)
        self.assertNotIn(self.secret, sent[0].body)
        self.assertIn("MOTHYS", sent[0].body)
