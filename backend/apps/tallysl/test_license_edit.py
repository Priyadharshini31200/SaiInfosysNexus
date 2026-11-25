"""Customer edit, license change and trial extension (GSTR2Tally)."""
from datetime import timedelta

from django.contrib.auth.hashers import check_password
from django.utils import timezone

from .models import TallyAuditLog, TallyCustomer
from .tests import ADM, Base

P = f"{ADM}/products/gstr2tally/customers/"


class LicenseEditTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.registered("710000001", "e@example.com")
        self.s = self.staff(self.super)

    def url(self, a):
        return f"{P}{self.c.pk}/license/{a}/"

    def lic(self):
        return TallyCustomer.objects.get(pk=self.c.pk).license

    def test_edit_keeps_password_unless_given(self):
        self.activate(self.c, "LIFETIME")
        before = TallyCustomer.objects.get(pk=self.c.pk)
        r = self.s.patch(f"{P}{self.c.pk}/", {"name": "New Name"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        after = TallyCustomer.objects.get(pk=self.c.pk)
        self.assertEqual(after.password_hash, before.password_hash)
        self.assertEqual(after.tally_serial, before.tally_serial)
        self.s.patch(f"{P}{self.c.pk}/", {"password": "Brand-New-1"}, format="json")
        self.assertTrue(check_password("Brand-New-1", TallyCustomer.objects.get(pk=self.c.pk).password_hash))

    def test_change_license_and_confirmation(self):
        self.activate(self.c, "TRIAL")
        key = self.lic().activation_key_hash
        self.assertEqual(self.s.post(self.url("change"), {"license_plan": "YEARS", "years": 2}, format="json").status_code, 200)
        self.assertEqual(self.lic().license_type, "LICENSED")
        self.s.post(self.url("change"), {"license_plan": "LIFETIME"}, format="json")
        self.assertIsNone(self.lic().expires_at)
        r = self.s.post(self.url("change"), {"license_plan": "TRIAL", "trial_days": 5}, format="json")
        self.assertEqual(r.status_code, 409)
        r = self.s.post(self.url("change"), {"license_plan": "TRIAL", "trial_days": 5, "confirm": True}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.lic().activation_key_hash, key)
        self.assertEqual(TallyCustomer.objects.filter(tally_serial="710000001").count(), 1)

    def test_extend_active_and_expired_and_invalid(self):
        self.activate(self.c, "TRIAL")
        old = self.lic().expires_at
        self.assertEqual(self.s.post(self.url("extend-trial"), {"days": 3, "reason": "demo"}, format="json").status_code, 200)
        self.assertEqual(self.lic().expires_at, old + timedelta(days=3))
        for bad in (0, -2, "x", 1.5):
            self.assertEqual(self.s.post(self.url("extend-trial"), {"days": bad}, format="json").status_code, 400)
        lic = self.lic()
        lic.expires_at = timezone.now() - timedelta(days=10)
        lic.save()
        self.assertEqual(self.s.post(self.url("extend-trial"), {"days": 7}, format="json").status_code, 200)
        self.assertGreater(self.lic().expires_at, timezone.now() + timedelta(days=6))
        log = TallyAuditLog.objects.filter(customer=self.c, action="TRIAL_EXTENDED").first()
        self.assertIn("old expiry", log.detail)
        self.assertIn("new expiry", log.detail)

    def test_lifetime_cannot_extend(self):
        self.activate(self.c, "LIFETIME")
        self.assertEqual(self.s.post(self.url("extend-trial"), {"days": 3}, format="json").status_code, 409)
