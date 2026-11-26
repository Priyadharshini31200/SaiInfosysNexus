"""GSTR Register -> Nexus customer -> credential email -> licence -> activation email -> login -> forgot-password OTP -> reset."""
import re
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.utils import timezone

from .models import TallyCustomer
from .tests import ADM, DEV_A, SIGNUP, Base

OTP = re.compile(r"Verification Code \(OTP\): (\d{6})\b")


def mail_body(subject_part):
    return next(m for m in reversed(mail.outbox) if subject_part in m.subject).body


class FullRegistrationFlowTests(Base):
    def test_end_to_end(self):
        self.assertEqual(self.signup().status_code, 201)
        self.assertEqual(self.call("register/", {**SIGNUP}).status_code, 409)  # duplicate serial refused; ownership kept
        c = TallyCustomer.objects.get()
        welcome = mail_body("Welcome")
        self.assertIn("Ravi Kumar", welcome)
        self.assertIn("700123456", welcome)
        temp = re.search(r"Temporary Password: (\S+)", welcome).group(1)
        self.assertNotIn(temp, c.password_hash)  # hashed, never plain text
        self.assertTrue(c.welcome_email_status)
        row = self.staff(self.super).get(f"{ADM}/products/gstr2tally/customers/").json()["results"][0]
        self.assertEqual((row["tally_serial"], row["license_status"], bool(row["welcome_email_status"])), ("700123456", "ACTIVE", True))
        self.assertEqual(self.staff(self.super).get(f"{ADM}/dashboard/").json()["registrations"]["total"], 1)

        login = self.call("auth/login/", {"user_id": "700123456", "password": temp, "device": DEV_A})
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.data["must_change_password"])  # direct login with the temporary password

        key = self.activate(c, "LICENSED")  # Superadmin configures the licence -> separate activation email
        self.assertFalse([m for m in mail.outbox if "activation key" in m.subject.lower()])  # emailed only on customer request
        self.assertNotIn(key, welcome)

        # 5 wrong tries kill the OTP
        self.call("auth/forgot-password/", {"identifier": "ravi@example.com"})
        otp = OTP.search(mail_body("Reset your")).group(1)
        wrong = "999999" if otp != "999999" else "888888"
        for _ in range(5):
            self.assertEqual(self.call("auth/verify-otp/", {"identifier": "ravi@example.com", "otp": wrong}).status_code, 400)
        self.assertEqual(self.call("auth/verify-otp/", {"identifier": "ravi@example.com", "otp": otp}).status_code, 400)

        # a fresh OTP: verified first, then the password changes, once only
        c.password_resets.update(created_at=timezone.now() - timedelta(minutes=5))
        self.call("auth/forgot-password/", {"identifier": "ravi@example.com"})
        otp = OTP.search(mail_body("Reset your")).group(1)
        self.assertEqual(self.call("auth/verify-otp/", {"identifier": "ravi@example.com", "otp": otp}).status_code, 200)
        self.assertEqual(self.call("auth/reset-password/", {"identifier": "700123456", "token": otp, "new_password": "Fresh#Pass9"}).status_code, 200)
        self.assertEqual(self.call("auth/reset-password/", {"identifier": "700123456", "token": otp, "new_password": "Again#Pass9"}).status_code, 400)
        self.assertEqual(self.call("auth/login/", {"user_id": "700123456", "password": "Fresh#Pass9", "device": DEV_A}).status_code, 200)

    def test_otp_expires_and_belongs_to_its_account(self):
        self.signup()
        other = self.signup(tally_serial="700999999", email="b@example.com")
        self.assertEqual(other.status_code, 201)
        self.call("auth/forgot-password/", {"identifier": "ravi@example.com"})
        otp = OTP.search(mail_body("Reset your")).group(1)
        self.assertEqual(self.call("auth/reset-password/", {"identifier": "b@example.com", "token": otp, "new_password": "Hack#Pass9"}).status_code, 400)  # another account's identifier
        TallyCustomer.objects.get(tally_serial="700123456").password_resets.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.call("auth/reset-password/", {"identifier": "ravi@example.com", "token": otp, "new_password": "Late#Pass9"}).status_code, 400)

    def test_credential_email_retry_creates_no_duplicate(self):
        self.signup()
        c = TallyCustomer.objects.get()
        self.call("auth/resend-credentials/", {"user_id": "700123456"})  # inside the cooldown: silent no-op
        self.assertEqual(len([m for m in mail.outbox if "Welcome" in m.subject]), 1)
        c.audit.all().update(created_at=timezone.now() - timedelta(minutes=5))
        self.call("auth/resend-credentials/", {"user_id": "700123456"})
        welcomes = [m for m in mail.outbox if "Welcome" in m.subject]
        self.assertEqual(len(welcomes), 2)
        new = re.search(r"Temporary Password: (\S+)", welcomes[-1].body).group(1)
        self.assertEqual(self.call("auth/login/", {"user_id": "700123456", "password": new, "device": DEV_A}).status_code, 200)
        self.assertEqual(TallyCustomer.objects.count(), 1)
        # once the customer has set their own password a resend is refused (silently)
        self.call("auth/forgot-password/", {"identifier": "700123456"})
        otp = OTP.search(mail_body("Reset your")).group(1)
        self.call("auth/reset-password/", {"identifier": "700123456", "token": otp, "new_password": "Fresh#Pass9"})
        c.audit.all().update(created_at=timezone.now() - timedelta(minutes=5))
        before = len(mail.outbox)
        self.call("auth/resend-credentials/", {"user_id": "700123456"})
        self.assertFalse([m for m in mail.outbox[before:] if "Welcome" in m.subject])
