"""Credential email content per plan, retries + delivery log, request rejection, health check, CORS, JSON error handling."""
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.db import OperationalError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AdminPermission

from .models import EmailLog, LicenseAuditLog, ProjectClient, ProjectCustomer, RegistrationRequest
from .portal_views import hash_key
from .test_portal import REST
from .test_credentials import CredentialsBase
from .test_device import PRN

GSTR = "gstr2tally"
R = f"/api/projects/{PRN}/registration-requests/"


def fmt(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y")


@override_settings(MAIL_RETRY_DELAY_SECONDS=0)
class CredentialEmailContentTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()

    def html(self):
        return mail.outbox[-1].alternatives[0][0]

    def test_trial_email_lists_every_field_but_never_the_trial_length(self):
        r = self.approve("TRIAL", trial_days=9).json()
        msg = mail.outbox[-1]
        for label in ("Product Name: Barcode Auto Print", "Customer Name: Meena", f"User ID: {r['user_id']}", "Temporary Password: ", f"Serial Number: {r['serial_number']}",
                      "Activation Key: ", "License Plan: Trial", "Activation Date: "):
            self.assertIn(label, msg.body)
        self.assertNotIn("Expiry Date", msg.body)
        for text in (msg.body, self.html()):
            self.assertNotRegex(text.lower(), r"\b\d+\s*days?\b")
            self.assertNotIn("9 day", text)
            self.assertNotIn("trial -", text.lower())

    def test_one_year_email_states_the_expiry_date_when_the_year_has_started(self):
        r = self.approve("ONE_YEAR", project=GSTR, email="gstr.customer@example.com").json()
        customer = ProjectCustomer.objects.get(pk=r["id"])
        self.assertEqual(customer.expires_at, customer.activated_at + timedelta(days=365))
        msg = mail.outbox[-1]
        self.assertIn("License Plan: 1 Year", msg.body)
        self.assertIn(f"Activation Date: {fmt(customer.activated_at)}", msg.body)
        self.assertIn(f"Expiry Date: {fmt(customer.expires_at)}", msg.body)

    def test_one_year_email_for_a_device_product_says_the_year_starts_at_first_activation(self):
        self.approve("ONE_YEAR")
        body = mail.outbox[-1].body
        self.assertIn("License Plan: 1 Year", body)
        self.assertRegex(body, r"Expiry Date: One year from the day you first activate")

    def test_lifetime_email_shows_lifetime_and_no_expiry(self):
        self.approve("LICENSED")
        msg = mail.outbox[-1]
        self.assertIn("License Plan: Lifetime", msg.body)
        self.assertNotIn("Expiry", msg.body)
        self.assertNotIn("Expiry", self.html())

    def test_email_is_branded_html_with_a_plain_text_twin_and_escapes_values(self):
        self.approve("TRIAL")
        msg = mail.outbox[-1]
        self.assertEqual(msg.alternatives[0][1], "text/html")
        self.assertIn("SAI INFOSYS", self.html())
        self.assertIn("SAI INFOSYS", msg.body)
        # HTML escaping: a hostile name cannot inject markup
        from . import mailer

        self.assertNotIn("<script>", mailer.render_html("t", "i", [("Name", "<script>alert(1)</script>")]))

    def test_the_delivery_is_logged_without_the_body(self):
        r = self.approve("TRIAL").json()
        log = EmailLog.objects.get(kind="credentials")
        self.assertEqual((log.recipient, log.status, log.attempts, log.error, log.customer_id), ("new.customer@example.com", "accepted", 1, "", r["id"]))
        self.assertFalse(hasattr(log, "body"))

    def test_a_connection_failure_is_retried_and_sends_exactly_one_email(self):
        from django.core.mail.backends.locmem import EmailBackend

        calls = {"n": 0}

        def flaky_connect(self_):  # connecting fails twice (nothing sent yet), then works
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("temporary smtp outage")
            return False

        with patch.object(EmailBackend, "open", flaky_connect):
            r = self.approve("TRIAL").json()
        self.assertEqual(calls["n"], 3)
        self.assertEqual(EmailLog.objects.get(kind="credentials").attempts, 3)
        self.assertEqual(r["credentials_email_status"], "accepted")
        self.assertEqual(len([m for m in mail.outbox if "Temporary Password" in m.body]), 1)  # one email, not three

    def test_a_send_failure_is_not_resent_but_logged_and_reported_without_a_secret_in_the_log(self):
        with patch("apps.registrations.mailer.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            r = self.approve("TRIAL").json()
        log = EmailLog.objects.get(kind="credentials")
        self.assertEqual((log.status, log.attempts), ("failed", 1))  # an error while sending is never resent automatically
        self.assertIn("OSError", log.error)
        self.assertFalse(r["email_sent"])
        self.assertNotIn(r["temporary_password"], log.error + log.subject)
        self.assertTrue(ProjectCustomer.objects.filter(pk=r["id"]).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST)
class FirstLoginPasswordChangeTests(TestCase):
    """The default configuration: the emailed temporary password must be replaced before anything else works."""

    def setUp(self):
        cache.clear()
        with override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True):
            call_command("seed_initial_users", verbosity=0)
        ProjectClient.objects.create(project=PRN, key_hash=hash_key("nxk_first"), reset_url="https://prn.example/reset")
        self.app, self.staff = APIClient(), APIClient()
        self.staff.force_authenticate(get_user_model().objects.get(username="Superadmin"))

    def portal(self, path, data=None, token=None, method="post"):
        h = {"HTTP_X_PROJECT_KEY": "nxk_first", **({"HTTP_X_CUSTOMER_TOKEN": token} if token else {})}
        return getattr(self.app, method)(f"/api/portal/{PRN}/{path}", data or {}, format="json", **h)

    def test_first_login_goes_straight_in_by_default_and_the_password_is_changed_voluntarily(self):
        rid = self.portal("register-request/", {"name": "Ravi", "contact_number": "9876543210", "email": "ravi@example.com"}).json()["id"]
        mail.outbox.clear()
        self.assertEqual(self.staff.post(f"{R}{rid}/approve/", {"license_type": "LICENSED"}, format="json").status_code, 201)
        body = mail.outbox[0].body
        user_id, temp = re.search(r"User ID: (\S+)", body).group(1), re.search(r"Temporary Password: (\S+)", body).group(1)
        self.assertNotIn("asked to choose a new password", body)
        token = self.portal("login/", {"username": user_id, "password": temp}).json()["token"]
        self.assertEqual(self.portal("license/status/", token=token, method="get").status_code, 200)  # no forced change screen
        self.assertEqual(self.portal("change-password/", {"current_password": temp, "new_password": "mine"}, token=token).status_code, 200)

    @override_settings(ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE=True)
    def test_strict_mode_requires_a_new_password_at_first_login(self):
        rid = self.portal("register-request/", {"name": "Ravi", "contact_number": "9876543210", "email": "ravi@example.com"}).json()["id"]
        mail.outbox.clear()
        self.assertEqual(self.staff.post(f"{R}{rid}/approve/", {"license_type": "LICENSED"}, format="json").status_code, 201)
        body = mail.outbox[0].body
        user_id, temp = re.search(r"User ID: (\S+)", body).group(1), re.search(r"Temporary Password: (\S+)", body).group(1)

        login = self.portal("login/", {"username": user_id, "password": temp})
        self.assertEqual(login.status_code, 200, login.content)
        self.assertTrue(login.json()["must_change_password"])
        text = login.content.decode()
        self.assertNotIn(temp, text)  # no authentication API ever returns a password
        self.assertNotIn("password_hash", text)
        token = login.json()["token"]
        self.assertEqual(self.portal("license/status/", token=token, method="get").status_code, 403)  # password_change_required

        self.assertEqual(self.portal("change-password/", {"current_password": temp, "new_password": "Fresh#Pass42x"}, token=token).status_code, 200)
        again = self.portal("login/", {"username": user_id, "password": "Fresh#Pass42x"}).json()
        self.assertFalse(again["must_change_password"])
        self.assertEqual(self.portal("license/status/", token=again["token"], method="get").status_code, 200)


class RegistrationStatusTests(FirstLoginPasswordChangeTests):
    def test_status_follows_the_request_and_needs_id_and_email(self):
        rid = self.portal("register-request/", {"name": "Ravi", "contact_number": "9876543210", "email": "Ravi@example.com"}).json()["id"]
        ask = lambda **kw: self.portal("register-request/status/", {"request_id": rid, "email": "ravi@example.com", **kw})
        self.assertEqual((ask().status_code, ask().json()["status"], ask().json()["status_label"]), (200, "pending", "Pending Approval"))
        self.assertEqual(ask(email="someone@else.com").status_code, 404)
        self.assertEqual(ask(request_id=rid + 999).status_code, 404)
        self.assertNotIn("user_id", ask().json())
        self.staff.post(f"{R}{rid}/approve/", {"license_type": "TRIAL"}, format="json")
        self.assertEqual(ask().json()["status"], "approved")

    def test_a_rejected_request_reports_rejected_and_the_public_device_endpoint_agrees(self):
        rid = self.portal("register-request/", {"name": "Ravi", "contact_number": "9876543210", "email": "ravi@example.com"}).json()["id"]
        self.staff.post(f"{R}{rid}/reject/", {"reason": "no"}, format="json")
        r = self.app.post(f"/api/device/{PRN}/public/register-request/status/", {"request_id": rid, "email": "ravi@example.com"}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "rejected"))
        self.assertEqual(self.app.post(f"/api/device/{PRN}/public/register-request/status/", {"request_id": rid, "email": "x@y.com"}, format="json").status_code, 404)


class RejectRequestTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()

    def pending(self, email="pending@example.com", project=PRN):
        return RegistrationRequest.objects.create(project=project, name="Asha", contact_number="9876543210", email=email)

    def test_reject_marks_the_request_creates_nothing_and_sends_nothing(self):
        req = self.pending()
        before = ProjectCustomer.objects.count()
        r = self.s.post(f"{R}{req.pk}/reject/", {"reason": "Not a customer"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["status"], r.json()["status_label"], r.json()["reject_reason"]), ("rejected", "Rejected", "Not a customer"))
        self.assertEqual(ProjectCustomer.objects.count(), before)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(LicenseAuditLog.objects.filter(action="REQUEST_REJECTED", project=PRN).exists())
        self.assertEqual(self.s.get(R).json()["results"], [])  # no longer pending

    def test_a_rejected_or_approved_request_cannot_be_approved_or_rejected_again(self):
        req = self.pending()
        self.s.post(f"{R}{req.pk}/reject/", {}, format="json")
        self.assertEqual(self.s.post(f"{R}{req.pk}/approve/", {"license_type": "TRIAL"}, format="json").status_code, 409)
        self.assertEqual(self.s.post(f"{R}{req.pk}/reject/", {}, format="json").status_code, 409)
        self.assertEqual(ProjectCustomer.objects.filter(email="pending@example.com").count(), 0)

    def test_a_pending_request_shows_as_pending_approval_and_grants_no_license(self):
        req = self.pending()
        item = self.s.get(f"{R}{req.pk}/").json()
        self.assertEqual((item["status"], item["status_label"]), ("pending", "Pending Approval"))
        self.assertFalse(ProjectCustomer.objects.filter(email="pending@example.com").exists())

    def test_the_same_email_can_ask_again_after_a_rejection_but_not_twice_at_once(self):
        req = self.pending()
        self.s.post(f"{R}{req.pk}/reject/", {}, format="json")
        again = self.pending()  # allowed: only PENDING rows are unique
        self.assertEqual(again.status, "PENDING")

    def test_reject_needs_entry_permission_and_stays_inside_its_project(self):
        req = self.pending()
        AdminPermission.objects.update_or_create(action="entry", defaults={"name": "Entry Access", "enabled": False})
        self.staff("Admin")
        self.assertEqual(self.s.post(f"{R}{req.pk}/reject/", {}, format="json").status_code, 403)  # Admin with Entry switched OFF
        self.assertEqual(RegistrationRequest.objects.get(pk=req.pk).status, "PENDING")
        self.staff("Superadmin")
        self.assertEqual(self.s.post(f"/api/projects/{GSTR}/registration-requests/{req.pk}/reject/", {}, format="json").status_code, 404)
        self.assertEqual(RegistrationRequest.objects.get(pk=req.pk).status, "PENDING")

    def test_anonymous_cannot_reject_or_approve(self):
        from rest_framework.test import APIClient

        req = self.pending()
        anon = APIClient()
        self.assertEqual(anon.post(f"{R}{req.pk}/reject/", {}, format="json").status_code, 401)
        self.assertEqual(anon.post(f"{R}{req.pk}/approve/", {"license_type": "TRIAL"}, format="json").status_code, 401)


@override_settings(MAIL_RETRY_DELAY_SECONDS=0)
class RetryCredentialsEmailTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()

    def failed_customer(self):
        with patch("apps.registrations.mailer.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            return self.approve("TRIAL").json()

    def retry(self, pk, project=PRN):
        return self.s.post(f"/api/projects/{project}/customers/{pk}/retry-credentials-email/", {}, format="json")

    def test_retry_after_a_failed_email_sends_fresh_credentials_that_work_and_never_returns_them(self):
        first = self.failed_customer()
        self.assertFalse(first["email_sent"])
        old_key = first["activation_key"].replace("-", "")
        r = self.retry(first["id"])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["email_sent"])
        self.assertEqual(r.json()["credentials_email_status"], "accepted")
        self.assertFalse({"password", "temporary_password", "activation_key"} & set(r.json()))
        body = mail.outbox[-1].body
        new_pw, new_key = re.search(r"Temporary Password: (\S+)", body).group(1), re.search(r"Activation Key: (\S+)", body).group(1)
        self.assertNotEqual(new_pw, first["temporary_password"])
        self.assertNotEqual(new_key.replace("-", ""), old_key)
        customer = ProjectCustomer.objects.get(pk=first["id"])
        self.assertNotIn(new_pw, customer.password_hash)
        self.assertEqual(ProjectCustomer.objects.filter(email=customer.email).count(), 1)  # no second customer

    def test_a_second_retry_after_success_is_refused_so_no_duplicate_email_goes_out(self):
        first = self.failed_customer()
        self.assertEqual(self.retry(first["id"]).status_code, 200)
        sent = len(mail.outbox)
        self.assertEqual(self.retry(first["id"]).status_code, 409)
        self.assertEqual(len(mail.outbox), sent)

    def test_retry_is_refused_once_the_customer_has_used_the_account(self):
        first = self.failed_customer()
        ProjectCustomer.objects.filter(pk=first["id"]).update(password_changed_at=timezone.now())
        self.assertEqual(self.retry(first["id"]).status_code, 409)

    def test_retry_needs_entry_permission_and_stays_inside_its_project(self):
        first = self.failed_customer()
        self.assertEqual(self.retry(first["id"], project=GSTR).status_code, 404)
        self.s.logout()
        self.assertIn(self.retry(first["id"]).status_code, (401, 403))


class HealthAndErrorTests(CredentialsBase):
    def test_health_is_public_and_reports_the_database(self):
        r = self.c.get("/api/health/")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok", "database": "ok"}))

    def test_health_answers_503_json_when_the_database_is_down(self):
        with patch("apps.core.views.connection") as conn:
            conn.cursor.side_effect = OperationalError("cannot open server")
            r = self.c.get("/api/health/")
        self.assertEqual((r.status_code, r.json()["status"]), (503, "unavailable"))
        self.assertNotIn("cannot open server", r.content.decode())

    def test_a_database_failure_inside_an_api_call_is_a_readable_503_not_an_html_page(self):
        self.staff()
        with patch("apps.registrations.views.ProjectCustomer.objects") as objects:
            objects.filter.side_effect = OperationalError("login timeout expired")
            r = self.s.get(f"/api/projects/{PRN}/customers/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["code"], "database_unavailable")
        self.assertNotIn("login timeout", r.content.decode())


class CorsTests(SimpleTestCase):
    def middleware(self, origins):
        from apps.core.middleware import ProjectApiCorsMiddleware
        from django.http import HttpResponse

        with override_settings(CORS_ALLOWED_ORIGINS=origins):
            return ProjectApiCorsMiddleware(lambda request: HttpResponse("ok"))

    def call(self, mw, path, origin, method="GET"):
        return mw(RequestFactory().generic(method, path, HTTP_ORIGIN=origin))

    def test_nothing_is_allowed_by_default(self):
        self.assertNotIn("Access-Control-Allow-Origin", self.call(self.middleware([]), "/api/portal/x/login/", "https://app.example"))

    def test_a_listed_origin_gets_headers_on_project_apis_only(self):
        mw = self.middleware(["https://app.example"])
        ok = self.call(mw, "/api/portal/prn-auto-print/login/", "https://app.example")
        self.assertEqual(ok["Access-Control-Allow-Origin"], "https://app.example")
        self.assertNotIn("Access-Control-Allow-Credentials", ok)
        self.assertNotIn("Access-Control-Allow-Origin", self.call(mw, "/api/auth/login/", "https://app.example"))
        self.assertNotIn("Access-Control-Allow-Origin", self.call(mw, "/api/portal/x/login/", "https://evil.example"))

    def test_preflight_is_answered_without_reaching_the_view(self):
        pre = self.call(self.middleware(["https://app.example"]), "/api/device/prn-auto-print/login/", "https://app.example", "OPTIONS")
        self.assertEqual(pre.status_code, 204)
        self.assertIn("X-Device-Signature", pre["Access-Control-Allow-Headers"])
