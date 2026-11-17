"""Registration mail goes to the address that was entered AND saved for the new customer, once; errors say why."""
import re

from django.core import mail

from .models import TallyCustomer
from .tests import ADM, DEV_A, KEY, SIGNUP, Base


class RegisterRecipientTests(Base):
    def test_welcome_mail_goes_to_the_saved_customer_email_only(self):
        r = self.signup(email="New.Person@Example.com")
        self.assertEqual(r.status_code, 201)
        c = TallyCustomer.objects.get()
        self.assertEqual(c.email, "new.person@example.com")  # saved
        welcome = [m for m in mail.outbox if "Welcome" in m.subject]
        self.assertEqual(len(welcome), 1)
        self.assertEqual(welcome[0].to, [c.email])  # recipient == saved email, never the sender or an admin
        self.assertNotIn(c.email, [a for m in welcome for a in (m.cc + m.bcc)])
        self.assertEqual(r.data["email_sent_to"], "ne***@example.com")
        self.assertIn("ne***@example.com", r.data["message"])
        self.assertNotIn("new.person@example.com", str(r.data))  # the full address is never echoed

    def test_existing_serial_is_refused_clearly_and_changes_nothing(self):
        self.signup(email="owner@example.com")
        before = TallyCustomer.objects.get()
        mail.outbox.clear()
        r = self.signup(email="someone.else@example.com")
        self.assertEqual((r.status_code, r.data["code"]), (409, "account_exists"))
        self.assertEqual(TallyCustomer.objects.count(), 1)
        after = TallyCustomer.objects.get()
        self.assertEqual((after.email, after.password_hash, after.pk), (before.email, before.password_hash, before.pk))
        self.assertEqual(mail.outbox, [])  # nothing is sent to anyone

    def test_activation_key_goes_only_to_the_licence_owner_and_only_on_request(self):
        self.signup(email="owner@example.com")
        c = TallyCustomer.objects.get()
        mail.outbox.clear()
        self.activate(c, "LICENSED")
        self.assertEqual(mail.outbox, [])  # issuing the licence does not email the key
        self.call("activate/request-code/", {"user_id": SIGNUP["tally_serial"]})
        keys = [m for m in mail.outbox if "Activation Key" in m.subject]
        self.assertEqual([m.to for m in keys], [["owner@example.com"]])

    def test_validation_errors_use_the_standard_envelope_with_the_real_reason(self):
        r = self.signup(email="not-an-email")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "validation_error")
        self.assertIn("email", r.data["errors"])
        self.assertTrue(r.data["message"])
        self.assertEqual(TallyCustomer.objects.count(), 0)
        bad_phone = self.signup(contact_number="abc")
        self.assertIn("contact_number", bad_phone.data["errors"])

    def test_nested_validation_errors_are_flattened_not_stringified(self):
        self.signup()
        r = self.call("auth/login/", {"user_id": "700123456", "password": "x", "device": {"client_device_id": "", "fingerprint": ""}})
        self.assertEqual((r.status_code, r.data["code"]), (400, "validation_error"))
        self.assertIn("device.fingerprint", r.data["errors"])
        self.assertTrue(all(isinstance(m, str) and "ErrorDetail" not in m for ms in r.data["errors"].values() for m in ms))
        self.assertNotIn("{", r.data["message"])
