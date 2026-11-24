from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import services
from .test_portal import REST, make_customer

PRN = "prn-auto-print"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST)
class OtpResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c = make_customer(PRN, "712345678", "ravi@example.com", password="Old#Pass1")
        self.app = APIClient()

    def post(self, p, d):
        return self.app.post(f"/api/device/{PRN}/public/{p}/", d, format="json")

    def test_otp_flow(self):
        self.assertEqual(self.post("forgot-password-otp", {"identifier": "ravi@example.com"}).status_code, 200)
        body = [m for m in mail.outbox if "OTP" in m.subject][-1].body
        otp = body.split("code is: ")[1][:6]
        self.assertEqual(self.post("reset-password-otp", {"identifier": "ravi@example.com", "otp": "000000x", "new_password": "N1"}).status_code, 400)
        r = self.post("reset-password-otp", {"identifier": "ravi@example.com", "otp": otp, "new_password": "New#Pass2"})
        self.assertEqual(r.status_code, 200)
        self.c.refresh_from_db()
        self.assertTrue(services.verify_password(self.c, "New#Pass2"))
        self.assertEqual(self.post("reset-password-otp", {"identifier": "ravi@example.com", "otp": otp, "new_password": "Again1"}).status_code, 400)
        self.assertNotIn("Old#Pass1", body)
