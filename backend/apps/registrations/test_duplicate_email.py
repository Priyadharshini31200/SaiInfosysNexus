"""Exactly one credentials email per Save / Approval; no automatic resend after the server may have accepted it; explicit
Retry sends exactly one more; concurrent requests are serialised by a database claim on the customer row."""
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.test import TestCase, override_settings
from django.utils import timezone

from . import portal, services
from .models import EmailLog, ProjectCustomer
from .test_add_customer import PRN, AddCustomerMixin
from .test_credentials import CredentialsBase
from .test_portal import REST


def credential_mails():
    return [m for m in mail.outbox if "Temporary Password" in m.body]


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST, MAIL_RETRY_DELAY_SECONDS=0)
class SaveSendsOnceTests(AddCustomerMixin, TestCase):
    def setUp(self):
        self.start()

    def test_create_customer_sends_exactly_one_email(self):
        r = self.save()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(credential_mails()), 1)
        self.assertEqual(EmailLog.objects.filter(kind="credentials").count(), 1)
        self.assertEqual(r.json()["credentials_email_status"], "accepted")

    def test_double_click_save_creates_one_customer_and_one_email(self):
        body = self.form()
        first, second = self.save(body), self.save(body)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertTrue(second.json()["already_created"])
        self.assertEqual(ProjectCustomer.objects.filter(email=body["email"]).count(), 1)
        self.assertEqual(len(credential_mails()), 1)

    def test_refreshing_the_customer_list_sends_nothing(self):
        pk = self.save().json()["id"]
        sent = len(mail.outbox)
        for _ in range(3):
            self.assertEqual(self.api.get(f"/api/projects/{PRN}/customers/").status_code, 200)
            self.assertEqual(self.api.get(f"/api/projects/{PRN}/customers/{pk}/").status_code, 200)
        self.assertEqual(len(mail.outbox), sent)

    def test_an_error_after_the_server_accepted_the_message_does_not_resend_it(self):
        # the message is handed over, then closing the connection fails (e.g. a timeout on QUIT)
        with patch.object(EmailBackend, "close", side_effect=TimeoutError("timed out")):
            r = self.save().json()
        self.assertEqual(len(credential_mails()), 1)
        self.assertEqual((r["credentials_email_status"], EmailLog.objects.get(kind="credentials").attempts), ("accepted", 1))

    def test_an_uncertain_send_error_is_reported_failed_and_not_retried(self):
        with patch("apps.registrations.mailer.EmailMultiAlternatives.send", side_effect=TimeoutError("no reply after DATA")) as send:
            r = self.save().json()
        self.assertEqual(send.call_count, 1)
        self.assertEqual((r["credentials_email_status"], r["credentials_email_status_label"]), ("failed", "Failed"))

    def test_every_email_carries_a_message_id_from_its_event(self):
        pk = self.save().json()["id"]
        self.assertIn(f"nexus-cred-{pk}-", credential_mails()[0].extra_headers["Message-ID"])


@override_settings(MAIL_RETRY_DELAY_SECONDS=0)
class ApprovalAndRetryOnceTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()

    def retry(self, pk):
        return self.s.post(f"/api/projects/{PRN}/customers/{pk}/retry-credentials-email/", {}, format="json")

    def test_approve_registration_sends_exactly_one_email(self):
        self.approve("TRIAL")
        self.assertEqual(len(credential_mails()), 1)

    def test_explicit_retry_sends_exactly_one_more_and_creates_nothing(self):
        with patch("apps.registrations.mailer.EmailMultiAlternatives.send", side_effect=OSError("down")):
            first = self.approve("TRIAL").json()
        customers = ProjectCustomer.objects.count()
        r = self.retry(first["id"])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(credential_mails()), 1)  # the failed one never reached the outbox; the retry is the one
        self.assertEqual(EmailLog.objects.filter(kind="credentials").count(), 2)
        self.assertEqual(ProjectCustomer.objects.count(), customers)
        self.assertEqual(self.retry(first["id"]).status_code, 409)  # a second click: already sent
        self.assertEqual(len(credential_mails()), 1)

    def test_a_retry_while_a_send_is_in_progress_is_refused(self):
        with patch("apps.registrations.mailer.EmailMultiAlternatives.send", side_effect=OSError("down")):
            first = self.approve("TRIAL").json()
        ProjectCustomer.objects.filter(pk=first["id"]).update(credentials_email_status="sending", credentials_emailed_at=timezone.now())
        sent = len(mail.outbox)
        r = self.retry(first["id"])
        self.assertEqual(r.status_code, 409)
        self.assertIn("already being sent", r.json()["message"])
        self.assertEqual(len(mail.outbox), sent)

    def test_a_stale_sending_mark_from_a_crashed_request_can_be_retried(self):
        with patch("apps.registrations.mailer.EmailMultiAlternatives.send", side_effect=OSError("down")):
            first = self.approve("TRIAL").json()
        ProjectCustomer.objects.filter(pk=first["id"]).update(
            credentials_email_status="sending", credentials_emailed_at=timezone.now() - timedelta(hours=1)
        )
        self.assertEqual(self.retry(first["id"]).status_code, 200)
        self.assertEqual(len(credential_mails()), 1)


class ClaimTests(CredentialsBase):
    """The database claim: one conditional UPDATE, so concurrent requests cannot both send."""

    def setUp(self):
        super().setUp()
        self.staff()

    def test_only_one_of_two_claims_wins(self):
        customer = ProjectCustomer.objects.get(pk=self.approve("TRIAL").json()["id"])
        first = services.claim_credentials_email(ProjectCustomer.objects.get(pk=customer.pk))
        second = services.claim_credentials_email(ProjectCustomer.objects.get(pk=customer.pk))  # a concurrent request
        self.assertTrue(first)
        self.assertEqual(second, "")

    def test_send_credentials_sends_nothing_while_another_request_is_sending(self):
        customer = ProjectCustomer.objects.get(pk=self.approve("TRIAL").json()["id"])
        sent = len(mail.outbox)
        services.claim_credentials_email(customer)
        self.assertFalse(portal.send_credentials(ProjectCustomer.objects.get(pk=customer.pk), "Pw#12345x", "A7K9P2X4M"))
        self.assertEqual(len(mail.outbox), sent)
        self.assertEqual(ProjectCustomer.objects.get(pk=customer.pk).credentials_email_status, "sending")  # untouched
