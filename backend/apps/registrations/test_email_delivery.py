"""Credentials email: content, status labels, Resend Credentials, SMTP failure handling and secret-free logging."""
import io
import re
import smtplib
from unittest.mock import patch

from django.core import mail
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from . import mailer, services
from .models import EmailLog, LicenseAuditLog, ProjectCustomer
from .test_credentials import CredentialsBase
from .test_device import PRN

GSTR = "gstr2tally"
SEND = "apps.registrations.mailer.EmailMultiAlternatives.send"


@override_settings(MAIL_RETRY_DELAY_SECONDS=0)
class ResendCredentialsTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()

    def resend(self, pk, project=PRN):
        return self.s.post(f"/api/projects/{project}/customers/{pk}/resend-credentials/", {}, format="json")

    def test_email_names_customer_and_product_and_user_id_is_the_serial(self):
        r = self.approve("ONE_YEAR").json()
        body = mail.outbox[-1].body
        for needle in ("Customer Name: Meena", "Product Name: Barcode Auto Print", f"User ID: {r['serial_number']}",
                       f"Serial Number: {r['serial_number']}", "Temporary Password:", "Activation Key:", "License Plan: 1 Year"):
            self.assertIn(needle, body)
        self.assertEqual(r["credentials_email_status_label"], "Submitted")

    def test_resend_after_a_sent_email_issues_fresh_credentials_for_the_same_customer(self):
        first = self.approve().json()
        _, old_pw, old_key = self.issued()
        before = (ProjectCustomer.objects.count(), first["serial_number"], first["user_id"], first["license_type"], first["expires_at"])
        r = self.resend(first["id"])
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["email_sent"])
        self.assertFalse({"password", "temporary_password", "activation_key"} & set(body))  # never returned
        new_pw = re.search(r"Temporary Password: (\S+)", mail.outbox[-1].body).group(1)
        self.assertNotEqual(new_pw, old_pw)
        # the key was already submitted once, so it is kept, not regenerated
        self.assertIn(f"use the key from your first email (ending {old_key.replace('-', '')[-4:]})", mail.outbox[-1].body)
        self.assertEqual(ProjectCustomer.objects.get(pk=first["id"]).activation_key_hash, services._hash(old_key.replace("-", "")))
        after = (ProjectCustomer.objects.count(), body["serial_number"], body["user_id"], body["license_type"], body["expires_at"])
        self.assertEqual(before, after)  # no new customer, serial number or licence
        self.assertTrue(LicenseAuditLog.objects.filter(customer_id=first["id"], action="CREDENTIALS_RESENT").exists())
        self.assertNotIn(new_pw, ProjectCustomer.objects.get(pk=first["id"]).password_hash)  # stored only as a hash

    def test_retry_still_refuses_after_success_but_resend_is_allowed(self):
        first = self.approve().json()
        self.assertEqual(self.s.post(f"/api/projects/{PRN}/customers/{first['id']}/retry-credentials-email/", {}, format="json").status_code, 409)
        self.assertEqual(self.resend(first["id"]).status_code, 200)

    def test_resend_is_refused_once_the_customer_has_used_the_account(self):
        first = self.approve().json()
        ProjectCustomer.objects.filter(pk=first["id"]).update(password_changed_at=timezone.now())
        sent = len(mail.outbox)
        r = self.resend(first["id"])
        self.assertEqual(r.status_code, 409)
        self.assertIn("Reset Password", r.json()["message"])
        self.assertEqual(len(mail.outbox), sent)

    def test_resend_needs_entry_permission_and_stays_inside_its_project(self):
        first = self.approve().json()
        self.assertEqual(self.resend(first["id"], project=GSTR).status_code, 404)
        self.s.logout()
        self.assertIn(self.resend(first["id"]).status_code, (401, 403))

    def test_a_failed_resend_reports_failed_and_keeps_the_customer(self):
        first = self.approve().json()
        with patch(SEND, side_effect=OSError("smtp down")):
            r = self.resend(first["id"]).json()
        self.assertEqual((r["email_sent"], r["credentials_email_status"], r["credentials_email_status_label"]), (False, "failed", "Failed"))
        self.assertEqual(ProjectCustomer.objects.filter(pk=first["id"]).count(), 1)


@override_settings(MAIL_RETRY_DELAY_SECONDS=0, MAIL_SEND_ATTEMPTS=3, EMAIL_HOST_PASSWORD="app-pass-secret")
class SmtpFailureTests(CredentialsBase):
    def test_a_refused_gmail_sign_in_is_not_retried(self):
        err = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        with patch(SEND, side_effect=err) as send:
            status = mailer.deliver("s", "body with Temporary Password: Abc123", "x@example.com", kind="test")
        self.assertEqual((status, send.call_count), ("failed", 1))
        log = EmailLog.objects.latest("pk")
        self.assertEqual(log.attempts, 1)
        self.assertIn("SMTPAuthenticationError", log.error)

    def test_connection_errors_are_retried_but_send_errors_are_not(self):
        from django.core.mail.backends.locmem import EmailBackend

        with patch.object(EmailBackend, "open", side_effect=OSError("connect timed out")) as connect:
            self.assertEqual(mailer.deliver("s", "b", "x@example.com"), "failed")
        self.assertEqual(connect.call_count, 3)
        with patch(SEND, side_effect=OSError("timed out waiting for the reply")) as send:
            self.assertEqual(mailer.deliver("s", "b", "x@example.com"), "failed")
        self.assertEqual(send.call_count, 1)

    def test_the_smtp_password_and_message_body_never_reach_the_log(self):
        with patch(SEND, side_effect=OSError("auth app-pass-secret rejected")), self.assertLogs("apps.registrations.mailer", "WARNING") as logs:
            mailer.deliver("s", "Temporary Password: Zz9-secret / Activation Key: KEYKEY", "x@example.com")
        stored = EmailLog.objects.latest("pk").error
        for text in [stored, *logs.output]:
            for secret in ("app-pass-secret", "Zz9-secret", "KEYKEY"):
                self.assertNotIn(secret, text)


class MailSettingsTests(SimpleTestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend", EMAIL_HOST="")
    def test_console_backend_is_not_configured(self):
        self.assertFalse(mailer.is_configured())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="")
    def test_smtp_without_a_host_is_not_configured(self):
        self.assertFalse(mailer.is_configured())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="smtp.gmail.com")
    def test_smtp_with_a_host_is_configured(self):
        self.assertTrue(mailer.is_configured())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend", EMAIL_HOST="")
    def test_send_test_email_explains_a_missing_mail_server(self):
        with self.assertRaisesMessage(CommandError, "No mail server is configured"):
            call_command("send_test_email", "someone@example.com", stdout=io.StringIO())


class ExpiryLabelTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()

    def test_an_unactivated_device_trial_describes_its_expiry_without_changing_it(self):
        r = self.approve("TRIAL").json()
        stored = ProjectCustomer.objects.get(pk=r["id"]).expires_at
        self.assertIsNone(r["effective_expires_at"])  # the trial clock starts at first activation
        self.assertRegex(r["expiry_label"], r"^\d+ days? from first activation$")
        self.assertEqual(ProjectCustomer.objects.get(pk=r["id"]).expires_at, stored)  # nothing written

    def test_a_started_licence_shows_its_date(self):
        r = self.approve("TRIAL").json()
        ProjectCustomer.objects.filter(pk=r["id"]).update(first_activated_at=timezone.now())
        body = self.s.get(f"/api/projects/{PRN}/customers/{r['id']}/").json()
        self.assertRegex(body["expiry_label"], r"^\d{2}/\d{2}/\d{4}$")
