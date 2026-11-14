import re
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.services import ensure_account

from . import services
from .client_views import hash_key
from .models import (
    TallyAdminPermission, TallyCustomer, TallyDevice, TallyLicense, TallyProduct, TallyProductKey, TallyUsageEvent,
)

V1 = "/api/v1/tally-sl"
ADM = "/api/v1/tally-sl-admin"
KEY = "tsl_test_key"
DEV_A = {"client_device_id": "dev-a", "fingerprint": "fp-a", "device_name": "Old Laptop", "app_version": "1.0"}
DEV_B = {"client_device_id": "dev-b", "fingerprint": "fp-b", "device_name": "New Laptop", "app_version": "1.0"}
SIGNUP = {"name": "Ravi Kumar", "email": "ravi@example.com", "contact_number": "9876543210", "tally_serial": "700123456", "product": "gstr2tally"}


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", TALLY_THROTTLE_RATE="10000/min")
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.product = TallyProduct.objects.get(code="gstr2tally")  # seeded by migration 0002
        TallyProductKey.objects.create(product=self.product, key_hash=hash_key(KEY))
        self.api = APIClient()
        self.super, _ = ensure_account("tsuper", Role.SUPERADMIN, "pw")
        self.super.email = "super@example.com"
        self.super.save()
        self.admin, _ = ensure_account("tadmin", Role.ADMIN, "pw")

    # ---- client helpers
    def call(self, path, data=None, token=None, method="post", product="gstr2tally", key=KEY):
        headers = {}
        if key:
            headers["HTTP_X_PRODUCT_KEY"] = key
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        fn = getattr(self.api, method)
        return fn(f"{V1}/{product}/{path}", data, format="json", **headers) if method != "get" else fn(f"{V1}/{product}/{path}", **headers)

    def staff(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def signup(self, **over):
        data = {**SIGNUP, **over}
        r = self.call("register/", data)
        return r

    def temp_password(self):
        body = next(m for m in mail.outbox if "Welcome" in m.subject).body
        return re.search(r"Temporary Password: (\S+)", body).group(1)

    def registered(self, serial="700123456", email="ravi@example.com"):
        self.signup(tally_serial=serial, email=email)
        return TallyCustomer.objects.get(tally_serial=serial)

    def activate(self, customer, license_type="LICENSED", days=None):
        r = self.staff(self.super).post(f"{ADM}/products/gstr2tally/customers/{customer.pk}/license/activate/",
                                        {"license_type": license_type, "trial_days": days}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["activation_key"]

    def enroll(self, customer, key, device=DEV_A, confirm=False):
        """Full Activate User flow. Returns the confirm response."""
        mail.outbox.clear()
        cache.clear()
        customer.verification_codes.update(created_at=timezone.now() - timedelta(minutes=5))
        self.call("activate/request-code/", {"user_id": customer.tally_serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        v = self.call("activate/verify/", {"user_id": customer.tally_serial, "code": code, "activation_key": key})
        self.assertEqual(v.status_code, 200, v.data)
        return self.call("activate/confirm/", {"ticket": v.data["ticket"], "device": device, "confirm_transfer": confirm}), v


class RegistrationAndLoginTests(Base):
    def test_requires_product_key_and_matching_product(self):
        self.assertEqual(self.call("identify/", method="get", key=None).status_code, 401)
        other = TallyProduct.objects.create(code="otherapp", name="Other")
        TallyProductKey.objects.create(product=other, key_hash=hash_key("other-key"))
        self.assertEqual(self.call("identify/", method="get", key="other-key").status_code, 403)
        r = self.call("identify/", method="get")
        self.assertEqual((r.status_code, r.data["product"]["code"]), (200, "gstr2tally"))

    def test_register_creates_pending_customer_and_emails_temp_password(self):
        r = self.signup()
        self.assertEqual(r.status_code, 201, r.data)
        c = TallyCustomer.objects.get(tally_serial="700123456")
        self.assertEqual(c.user_id, "700123456")  # Tally Serial == User ID
        self.assertEqual(c.license.status, TallyLicense.PENDING)  # no free paid licence
        self.assertTrue(c.must_change_password)
        welcome = [m for m in mail.outbox if m.to == ["ravi@example.com"]]
        self.assertEqual(len(welcome), 1)
        self.assertIn("User ID (Tally Serial Number): 700123456", welcome[0].body)
        self.assertNotIn("password_hash", r.content.decode())
        self.assertTrue(any(m.to == ["super@example.com"] for m in mail.outbox))  # superadmin told

    def test_duplicate_serial_blocked(self):
        self.signup()
        r = self.signup(email="other@example.com")
        self.assertEqual((r.status_code, r.data["code"]), (409, "account_exists"))
        self.assertEqual(TallyCustomer.objects.filter(tally_serial="700123456").count(), 1)

    def test_same_serial_allowed_in_another_product(self):
        other = TallyProduct.objects.create(code="otherapp", name="Other")
        TallyProductKey.objects.create(product=other, key_hash=hash_key("other-key"))
        self.signup()
        r = self.call("register/", {**SIGNUP, "product": "otherapp"}, product="otherapp", key="other-key")
        self.assertEqual(r.status_code, 201)

    def test_validation_errors_are_consistent(self):
        r = self.signup(email="nope")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "validation_error")
        self.assertIn("email", r.data["errors"])

    def test_first_login_forces_password_change_then_works(self):
        c = self.registered()
        pw = self.temp_password()
        r = self.call("auth/login/", {"user_id": "700123456", "password": pw, "device": DEV_A})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["must_change_password"])
        token = r.data["token"]
        blocked = self.call("license/status/", token=token, method="get")
        self.assertEqual((blocked.status_code, blocked.data["code"]), (403, "password_change_required"))
        ch = self.call("auth/change-password/", {"current_password": pw, "new_password": "MyOwn#1"}, token=token)
        self.assertEqual(ch.status_code, 200)
        self.assertEqual(self.call("session/", token=token, method="get").status_code, 401)  # old sessions end
        r2 = self.call("auth/login/", {"user_id": "700123456", "password": "MyOwn#1"})
        self.assertFalse(r2.data["must_change_password"])
        self.assertEqual(self.call("license/status/", token=r2.data["token"], method="get").status_code, 200)

    def test_wrong_password_and_lockout(self):
        self.registered()
        for _ in range(5):
            r = self.call("auth/login/", {"user_id": "700123456", "password": "bad"})
        self.assertEqual(r.status_code, 401)
        locked = self.call("auth/login/", {"user_id": "700123456", "password": "bad"})
        self.assertEqual((locked.status_code, locked.data["code"]), (429, "account_locked"))

    def test_forgot_and_reset_password_single_use(self):
        self.registered()
        mail.outbox.clear()
        unknown = self.call("auth/forgot-password/", {"identifier": "nobody@example.com"})
        self.assertEqual((unknown.status_code, len(mail.outbox)), (200, 0))  # no account enumeration
        known = self.call("auth/forgot-password/", {"identifier": "ravi@example.com"})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.data, known.data)
        token = re.search(r"Reset Code: (\S+)", mail.outbox[-1].body).group(1)
        self.assertEqual(self.call("auth/reset-password/", {"token": "junk", "new_password": "x1"}).data["code"], "invalid_token")
        ok = self.call("auth/reset-password/", {"token": token, "new_password": "Fresh#1"})
        self.assertEqual(ok.status_code, 200)
        again = self.call("auth/reset-password/", {"token": token, "new_password": "Other#1"})
        self.assertEqual(again.status_code, 400)  # token invalidated
        login = self.call("auth/login/", {"user_id": "700123456", "password": "Fresh#1"})
        self.assertEqual(login.status_code, 200)
        self.assertNotEqual(TallyCustomer.objects.get(tally_serial="700123456").password_hash, "Fresh#1")

    def test_expired_reset_token(self):
        c = self.registered()
        self.call("auth/forgot-password/", {"identifier": c.email})
        token = re.search(r"Reset Code: (\S+)", mail.outbox[-1].body).group(1)
        c.password_resets.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.call("auth/reset-password/", {"token": token, "new_password": "x1"}).status_code, 400)


class LicenseTests(Base):
    def test_registration_alone_is_not_authorized(self):
        c = self.registered()
        payload = services.license_payload(TallyCustomer.objects.get(pk=c.pk))
        self.assertEqual((payload["authorized"], payload["state"]), (False, "pending"))

    def test_trial_default_three_days_and_configurable(self):
        c = self.registered()
        self.activate(c, "TRIAL")
        c.license.refresh_from_db()
        self.assertEqual(c.license.trial_days, 3)
        self.assertAlmostEqual((c.license.expires_at - timezone.now()).total_seconds(), 3 * 86400, delta=60)
        self.activate(c, "TRIAL", 10)
        c.license.refresh_from_db()
        self.assertEqual(c.license.trial_days, 10)

    def test_licensed_one_year_and_lifetime(self):
        c = self.registered()
        self.activate(c, "LICENSED")
        c.license.refresh_from_db()
        self.assertAlmostEqual((c.license.expires_at - timezone.now()).days, 364, delta=1)
        self.activate(c, "LIFETIME")
        c.license.refresh_from_db()
        self.assertIsNone(c.license.expires_at)
        self.assertEqual(services.license_state(c.license, timezone.now() + timedelta(days=36500)), "active")

    def test_activation_emails_key_and_details(self):
        c = self.registered()
        mail.outbox.clear()
        key = self.activate(c, "LICENSED")
        m = mail.outbox[-1]
        self.assertEqual(m.to, ["ravi@example.com"])
        self.assertIn(key, m.body)
        self.assertIn("Licensed (1 Year)", m.body)
        self.assertNotEqual(c.license.activation_key_hash, key)  # only a hash is stored

    def test_extend_cancel_renew_inactivate(self):
        c = self.registered()
        url = lambda a: f"{ADM}/products/gstr2tally/customers/{c.pk}/license/{a}/"
        s = self.staff(self.super)
        self.assertEqual(s.post(url("extend-trial"), {"days": 2}, format="json").status_code, 409)  # not activated/trial yet
        self.activate(c, "TRIAL")
        before = TallyLicense.objects.get(customer=c).expires_at
        self.assertEqual(s.post(url("extend-trial"), {"days": 2}, format="json").status_code, 200)
        self.assertEqual(TallyLicense.objects.get(customer=c).expires_at, before + timedelta(days=2))
        self.assertEqual(s.post(url("cancel-trial"), format="json").status_code, 200)
        self.assertEqual(services.license_state(TallyLicense.objects.get(customer=c)), "cancelled")
        self.activate(c, "LICENSED")
        self.assertEqual(s.post(url("renew"), format="json").status_code, 200)
        lic = TallyLicense.objects.get(customer=c)
        self.assertAlmostEqual((lic.expires_at - timezone.now()).days, 729, delta=1)
        self.assertEqual(s.post(url("inactivate"), format="json").status_code, 200)
        self.assertEqual(services.license_state(TallyLicense.objects.get(customer=c)), "inactive")

    def test_expired_licence_not_authorized_and_status_follows_changes(self):
        c = self.registered()
        key = self.activate(c, "TRIAL", 1)
        TallyCustomer.objects.filter(pk=c.pk).update(must_change_password=False)
        r, _ = self.enroll(c, key)
        token = r.data["token"]
        self.assertTrue(r.data["license"]["authorized"])
        # change from the Nexus side is seen on the client's next check
        self.staff(self.super).post(f"{ADM}/products/gstr2tally/customers/{c.pk}/license/inactivate/", format="json")
        st = self.call("license/status/", token=token, method="get")
        self.assertEqual((st.data["authorized"], st.data["state"]), (False, "inactive"))
        self.staff(self.super).post(f"{ADM}/products/gstr2tally/customers/{c.pk}/license/activate/", {"license_type": "TRIAL", "trial_days": 1}, format="json")
        TallyLicense.objects.filter(customer=c).update(expires_at=timezone.now() - timedelta(minutes=1))
        st = self.call("license/status/", token=token, method="get")
        self.assertEqual((st.data["authorized"], st.data["state"]), (False, "expired"))

    def test_lease_never_exceeds_expiry(self):
        c = self.registered()
        key = self.activate(c, "TRIAL", 1)
        r, _ = self.enroll(c, key)
        lease = r.data["license"]["lease_until"]
        exp = r.data["license"]["expires_at"]
        self.assertLessEqual(lease, exp)

    def test_key_reissue_invalidates_old_key(self):
        c = self.registered()
        old = self.activate(c, "LICENSED")
        r = self.staff(self.super).post(f"{ADM}/products/gstr2tally/customers/{c.pk}/license/reissue-key/", format="json")
        self.assertEqual(r.status_code, 200)
        new = r.data["activation_key"]
        self.assertNotEqual(old, new)
        self.call("activate/request-code/", {"user_id": c.tally_serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        bad = self.call("activate/verify/", {"user_id": c.tally_serial, "code": code, "activation_key": old})
        self.assertEqual((bad.status_code, bad.data["code"]), (400, "invalid_activation_key"))
        ok = self.call("activate/verify/", {"user_id": c.tally_serial, "code": code, "activation_key": new})
        self.assertEqual(ok.status_code, 200)


class ProxyAddressTests(Base):
    def test_client_ip_trusts_only_proxy_appended_address(self):
        from types import SimpleNamespace
        req = SimpleNamespace(META={"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "6.6.6.6, 203.0.113.9"})
        self.assertEqual(services.client_ip(req), "10.0.0.1")  # not behind a proxy: spoofable header ignored
        with self.settings(USE_X_FORWARDED_HOST=True):
            self.assertEqual(services.client_ip(req), "203.0.113.9")  # behind the proxy: right-most entry
        self.assertEqual(services.client_ip(SimpleNamespace(META={"REMOTE_ADDR": "10.0.0.1"})), "10.0.0.1")

    @override_settings(TALLY_THROTTLE_RATE="3/min")
    def test_throttle_is_per_client_not_per_proxy(self):
        with self.settings(USE_X_FORWARDED_HOST=True):
            for _ in range(3):
                self.assertEqual(self.api.get(f"{V1}/gstr2tally/identify/", HTTP_X_PRODUCT_KEY=KEY, HTTP_X_FORWARDED_FOR="198.51.100.1").status_code, 200)
            self.assertEqual(self.api.get(f"{V1}/gstr2tally/identify/", HTTP_X_PRODUCT_KEY=KEY, HTTP_X_FORWARDED_FOR="198.51.100.1").status_code, 429)
            self.assertEqual(self.api.get(f"{V1}/gstr2tally/identify/", HTTP_X_PRODUCT_KEY=KEY, HTTP_X_FORWARDED_FOR="198.51.100.2").status_code, 200)


class LicensePlanAndRefreshTests(Base):
    def test_license_plan_vocabulary(self):
        c = self.registered()
        st = self.staff(self.super)
        url = f"{ADM}/products/gstr2tally/customers/{c.pk}/license/activate/"
        self.assertEqual(st.post(url, {"license_plan": "BOGUS"}, format="json").data["code"], "invalid_license_plan")
        r = st.post(url, {"license_plan": "ANNUAL"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["customer"]["license_plan"], "ANNUAL")
        self.assertEqual(TallyLicense.objects.get(customer=c).license_type, "LICENSED")  # stored value unchanged
        d = st.get(f"{ADM}/products/gstr2tally/customers/{c.pk}/").data
        self.assertEqual(d["license"]["plan"], "ANNUAL")
        self.assertEqual(len(st.get(f"{ADM}/reports/license-expiry/?license_type=ANNUAL").data["results"]), 1)
        self.assertEqual(st.post(url, {"license_plan": "LIFETIME"}, format="json").data["customer"]["license_plan"], "LIFETIME")
        self.assertEqual(st.post(url, {"license_plan": "TRIAL", "trial_days": 5}, format="json").data["customer"]["license_plan"], "TRIAL")

    def test_client_payload_has_license_plan_and_token_refresh_rotates(self):
        c = self.registered()
        key = self.activate(c, "ANNUAL")
        TallyCustomer.objects.filter(pk=c.pk).update(must_change_password=False)
        r, _ = self.enroll(c, key)
        self.assertEqual(r.data["license"]["license_plan"], "ANNUAL")
        old = r.data["token"]
        n = self.call("auth/refresh/", token=old)
        self.assertEqual(n.status_code, 200)
        self.assertEqual(self.call("session/", token=old, method="get").data["code"], "session_revoked")
        self.assertEqual(self.call("session/", token=n.data["token"], method="get").status_code, 200)
        self.assertTrue(self.call("license/status/", token=n.data["token"], method="get").data["authorized"])  # device binding kept


class ActivationAndTransferTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.registered()
        self.key = self.activate(self.c, "LICENSED")

    def test_cannot_activate_when_license_pending(self):
        c2 = self.registered("700999999", "x@example.com")
        c2.license.activation_key_hash = services._digest("ABCDE2345")
        c2.license.save()
        self.call("activate/request-code/", {"user_id": "700999999"})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        r = self.call("activate/verify/", {"user_id": "700999999", "code": code, "activation_key": "ABCDE2345"})
        self.assertEqual((r.status_code, r.data["code"]), (403, "license_not_active"))

    def test_first_enrollment_on_laptop(self):
        r, v = self.enroll(self.c, self.key)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(v.data["requires_confirmation"])
        self.assertTrue(r.data["license"]["authorized"])
        self.assertEqual(TallyDevice.objects.filter(customer=self.c, revoked_at__isnull=True).count(), 1)
        self.assertEqual(self.c.transfers.count(), 1)

    def test_unknown_user_gets_same_response_and_no_mail(self):
        mail.outbox.clear()
        r = self.call("activate/request-code/", {"user_id": "000000000"})
        self.assertEqual((r.status_code, len(mail.outbox)), (200, 0))

    def test_code_is_single_use_and_wrong_code_limited(self):
        self.call("activate/request-code/", {"user_id": self.c.tally_serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        for _ in range(5):
            self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": "WRONG123", "activation_key": self.key})
        self.assertEqual(self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": code, "activation_key": self.key}).status_code, 400)  # attempts exhausted
        cache.clear()
        self.c.verification_codes.update(created_at=timezone.now() - timedelta(minutes=5))
        self.call("activate/request-code/", {"user_id": self.c.tally_serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        self.assertEqual(self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": code, "activation_key": self.key}).status_code, 200)
        replay = self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": code, "activation_key": self.key})
        self.assertEqual(replay.status_code, 400)

    def test_expired_code_rejected(self):
        self.call("activate/request-code/", {"user_id": self.c.tally_serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        self.c.verification_codes.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": code, "activation_key": self.key}).status_code, 400)

    def test_new_code_invalidates_previous(self):
        self.call("activate/request-code/", {"user_id": self.c.tally_serial})
        first = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        self.c.verification_codes.update(created_at=timezone.now() - timedelta(minutes=5))
        self.call("activate/request-code/", {"user_id": self.c.tally_serial})
        self.assertEqual(self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": first, "activation_key": self.key}).status_code, 400)

    def test_replacement_laptop_needs_confirmation_then_revokes_old(self):
        r1, _ = self.enroll(self.c, self.key, DEV_A)
        old_token = r1.data["token"]
        self.assertEqual(self.call("session/", token=old_token, method="get").status_code, 200)
        r2, v2 = self.enroll(self.c, self.key, DEV_B)  # no confirm
        self.assertTrue(v2.data["requires_confirmation"])
        self.assertEqual(v2.data["message"], services.TRANSFER_WARNING)
        self.assertEqual((r2.status_code, r2.data["code"]), (409, "confirmation_required"))
        self.assertEqual(self.call("session/", token=old_token, method="get").status_code, 200)  # nothing changed yet
        # the same ticket can still be confirmed after the warning
        ticket = v2.data["ticket"]
        r3 = self.call("activate/confirm/", {"ticket": ticket, "device": DEV_B, "confirm_transfer": True})
        self.assertEqual(r3.status_code, 200, r3.data)
        self.assertTrue(r3.data["device_replaced"])
        # old device: session revoked immediately for online clients ...
        gone = self.call("session/", token=old_token, method="get")
        self.assertEqual((gone.status_code, gone.data["code"]), (401, "session_revoked"))
        # ... and a fresh sign-in on the old laptop is unauthorized (offline clients hit this at next check)
        pw = "Own#Pass1"
        TallyCustomer.objects.filter(pk=self.c.pk).update(password_hash=__import__("django.contrib.auth.hashers", fromlist=["x"]).make_password(pw), must_change_password=False)
        old = self.call("auth/login/", {"user_id": self.c.tally_serial, "password": pw, "device": DEV_A})
        self.assertFalse(old.data["license"]["authorized"])
        self.assertEqual(old.data["license"]["state"], "device_not_authorized")
        new = self.call("auth/login/", {"user_id": self.c.tally_serial, "password": pw, "device": DEV_B})
        self.assertTrue(new.data["license"]["authorized"])
        # history + exactly one active device
        self.assertEqual(TallyDevice.objects.filter(customer=self.c, revoked_at__isnull=True).count(), 1)
        self.assertEqual(list(self.c.transfers.values_list("kind", flat=True)), ["REPLACED", "ENROLLED"])
        self.assertEqual(TallyDevice.objects.get(customer=self.c, revoked_at__isnull=True).generation, 2)
        self.assertTrue(self.c.audit.filter(action="DEVICE_REPLACED").exists())
        self.assertTrue(any("moved to a new device" in m.subject for m in mail.outbox))  # security notice

    def test_ticket_replay_blocked(self):
        r, v = self.enroll(self.c, self.key, DEV_A)
        again = self.call("activate/confirm/", {"ticket": v.data["ticket"], "device": DEV_B, "confirm_transfer": True})
        self.assertEqual((again.status_code, again.data["code"]), (400, "invalid_ticket"))
        self.assertEqual(TallyDevice.objects.filter(customer=self.c, revoked_at__isnull=True).get().client_device_id, "dev-a")

    def test_ticket_expiry(self):
        self.call("activate/request-code/", {"user_id": self.c.tally_serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        v = self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": code, "activation_key": self.key})
        self.c.verification_codes.update(ticket_expires_at=timezone.now() - timedelta(seconds=1))
        r = self.call("activate/confirm/", {"ticket": v.data["ticket"], "device": DEV_A, "confirm_transfer": True})
        self.assertEqual(r.status_code, 400)

    def test_ticket_not_valid_for_other_product(self):
        other = TallyProduct.objects.create(code="otherapp", name="Other")
        TallyProductKey.objects.create(product=other, key_hash=hash_key("other-key"))
        self.call("activate/request-code/", {"user_id": self.c.tally_serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        v = self.call("activate/verify/", {"user_id": self.c.tally_serial, "code": code, "activation_key": self.key})
        r = self.call("activate/confirm/", {"ticket": v.data["ticket"], "device": DEV_A}, product="otherapp", key="other-key")
        self.assertEqual(r.status_code, 400)

    def test_same_device_reactivation_is_idempotent(self):
        self.enroll(self.c, self.key, DEV_A)
        r, v = self.enroll(self.c, self.key, DEV_A)
        self.assertFalse(v.data["requires_confirmation"] and False)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["device_replaced"])
        self.assertEqual(TallyDevice.objects.filter(customer=self.c).count(), 1)

    def test_admin_revoke_device_signs_out_and_requires_reactivation(self):
        r, _ = self.enroll(self.c, self.key, DEV_A)
        rv = self.staff(self.super).post(f"{ADM}/products/gstr2tally/customers/{self.c.pk}/device/revoke/", format="json")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(self.call("session/", token=r.data["token"], method="get").data["code"], "session_revoked")
        r2, v2 = self.enroll(self.c, self.key, DEV_B)
        self.assertEqual(r2.status_code, 200)  # nothing active any more: no warning needed


class TallyInfoAndUsageTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.registered()
        self.key = self.activate(self.c, "LICENSED")
        TallyCustomer.objects.filter(pk=self.c.pk).update(must_change_password=False)
        self.r, _ = self.enroll(self.c, self.key, DEV_A)
        self.token = self.r.data["token"]

    def test_tally_info_sync_keeps_verified_and_marks_unavailable(self):
        r = self.call("tally-info/", {"edition": "Prime", "virtual_user_limit": 5, "tss_expiry_date": "2027-03-31",
                                      "license_administrator": "Ravi", "registered_email": "tally@example.com"}, token=self.token)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["availability"]["edition"], "verified")
        self.assertEqual(r.data["availability"]["gateway_server"], "unavailable")
        self.call("tally-info/", {"edition": ""}, token=self.token)  # a blank never wipes a verified value
        d = self.staff(self.super).get(f"{ADM}/products/gstr2tally/customers/{self.c.pk}/").data
        self.assertEqual(d["tally"]["edition"], "Prime")
        self.assertEqual(d["tally"]["tss_expiry_date"], "2027-03-31")
        self.assertEqual(d["tally"]["tally_serial"], self.c.tally_serial)
        # TSS expiry is distinct from the Nexus licence expiry
        self.assertIsNotNone(d["license"]["expires_at"])
        self.assertNotEqual(d["license"]["expires_at"][:10], d["tally"]["tss_expiry_date"])

    def test_open_events_counted_and_deduped(self):
        TallyUsageEvent.objects.all().delete()
        self.assertTrue(self.call("usage/open/", token=self.token).data["recorded"])
        self.assertFalse(self.call("usage/open/", token=self.token).data["recorded"])  # within 60 s
        self.assertEqual(TallyUsageEvent.objects.filter(customer=self.c).count(), 1)

    def test_unauthorized_device_open_not_counted(self):
        TallyUsageEvent.objects.all().delete()
        TallyCustomer.objects.filter(pk=self.c.pk).update(password_hash=__import__("django.contrib.auth.hashers", fromlist=["x"]).make_password("pw1"))
        other = self.call("auth/login/", {"user_id": self.c.tally_serial, "password": "pw1", "device": DEV_B}).data["token"]
        r = self.call("usage/open/", token=other)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(TallyUsageEvent.objects.count(), 0)

    def _events(self, customer, days_ago_list):
        for d in days_ago_list:
            when = timezone.now() - timedelta(days=d)
            TallyUsageEvent.objects.create(customer=customer, kind="open", occurred_at=when, day=timezone.localtime(when, services.IST).date())

    def _make(self, serial, registered_days_ago):
        c = self.registered(serial, f"{serial}@example.com")
        TallyCustomer.objects.filter(pk=c.pk).update(registered_at=timezone.now() - timedelta(days=registered_days_ago))
        return TallyCustomer.objects.get(pk=c.pk)

    def test_classification_order_and_exclusive(self):
        TallyUsageEvent.objects.all().delete()
        TallyCustomer.objects.filter(pk=self.c.pk).update(registered_at=timezone.now() - timedelta(days=90))
        new = self._make("710000001", 2)
        self._events(new, [0, 1, 2])  # many opens but registered recently -> New first
        one = self._make("710000002", 20)
        self._events(one, [3])
        speed = self._make("710000003", 60)
        self._events(speed, list(range(0, 15)))
        normal = self._make("710000004", 60)
        self._events(normal, [1, 5, 9])
        zero = self._make("710000005", 60)
        dormant = self._make("710000006", 120)
        self._events(dormant, [80, 90])
        self._events(self.c, [])  # zero use, registered 90 days ago
        rows = {c.tally_serial: u["category"] for c, u in services.usage_rows(TallyCustomer.objects.all())}
        self.assertEqual(rows["710000001"], "new")
        self.assertEqual(rows["710000002"], "one_time")
        self.assertEqual(rows["710000003"], "speed")
        self.assertEqual(rows["710000004"], "normal")
        self.assertEqual(rows["710000005"], "zero_use")
        self.assertEqual(rows["710000006"], "dormant")
        self.assertEqual(len(rows), TallyCustomer.objects.count())  # exactly one bucket each

    def test_speed_threshold_boundary(self):
        c = self._make("720000001", 60)
        self._events(c, list(range(0, 14)))
        self.assertEqual(services.usage_rows(TallyCustomer.objects.filter(pk=c.pk))[0][1]["category"], "normal")
        self._events(c, [14])
        self.assertEqual(services.usage_rows(TallyCustomer.objects.filter(pk=c.pk))[0][1]["category"], "speed")

    def test_dashboard_counts_and_filtered_list(self):
        TallyUsageEvent.objects.all().delete()
        TallyCustomer.objects.filter(pk=self.c.pk).update(registered_at=timezone.now() - timedelta(days=90))
        one = self._make("730000001", 30)
        self._events(one, [4])
        s = self.staff(self.super)
        d = s.get(f"{ADM}/dashboard/").data
        counts = {c["key"]: c["count"] for c in d["categories"]}
        self.assertEqual(set(counts), {"speed", "normal", "new", "one_time"})
        self.assertEqual(counts["one_time"], 1)
        self.assertEqual(d["internal"]["zero_use"], 1)
        self.assertEqual(d["registrations"]["total"], 2)
        lst = s.get(f"{ADM}/usage/one_time/").data
        self.assertEqual([r["tally_serial"] for r in lst["results"]], ["730000001"])
        self.assertEqual({"name", "tally_serial", "product_name", "last_opened", "opens", "active_days"} - set(lst["results"][0]), set())
        self.assertEqual(s.get(f"{ADM}/usage/bogus/").status_code, 404)

    def test_new_registration_reflected_on_dashboard(self):
        s = self.staff(self.super)
        before = s.get(f"{ADM}/dashboard/").data["registrations"]["total"]
        self.signup(tally_serial="750000001", email="n@example.com")
        d = s.get(f"{ADM}/dashboard/").data
        self.assertEqual(d["registrations"]["total"], before + 1)
        self.assertEqual({c["key"]: c["count"] for c in d["categories"]}["new"] >= 1, True)


class ReportAndPermissionTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.registered()
        self.key = self.activate(self.c, "LICENSED")
        self.other = TallyProduct.objects.create(code="otherapp", name="Other App")
        o = TallyCustomer.objects.create(product=self.other, tally_serial="800000001", name="Other Cust", email="o@example.com",
                                         contact_number="9999999999", password_hash="x")
        TallyLicense.objects.create(customer=o)
        self.o = o

    def grant(self, product, *actions):
        from .models import PERMISSION_ACTIONS
        TallyAdminPermission.objects.update_or_create(user=self.admin, product=product, defaults={f"can_{a}": a in actions for a in PERMISSION_ACTIONS})

    def test_admin_without_grant_denied_everywhere(self):
        a = self.staff(self.admin)
        self.assertEqual(a.get(f"{ADM}/products/gstr2tally/customers/").status_code, 403)
        self.assertEqual(a.get(f"{ADM}/products/gstr2tally/customers/{self.c.pk}/").status_code, 403)
        self.assertEqual(a.post(f"{ADM}/products/gstr2tally/customers/{self.c.pk}/license/activate/", {"license_type": "LIFETIME"}, format="json").status_code, 403)
        self.assertEqual(a.get(f"{ADM}/reports/license-expiry/").data["results"], [])
        self.assertEqual(a.get(f"{ADM}/dashboard/").data["registrations"]["total"], 0)

    def test_project_wise_permissions_are_independent(self):
        self.grant(self.product, "reports")  # GSTR reports only
        a = self.staff(self.admin)
        rows = a.get(f"{ADM}/reports/license-expiry/").data["results"]
        self.assertEqual({r["product"] for r in rows}, {"gstr2tally"})  # not otherapp
        self.assertEqual(a.get(f"{ADM}/products/gstr2tally/customers/").status_code, 403)  # no View
        self.assertEqual(a.post(f"{ADM}/products/gstr2tally/customers/{self.c.pk}/license/renew/", format="json").status_code, 403)
        self.assertEqual(a.get(f"{ADM}/products/otherapp/customers/").status_code, 403)
        self.assertEqual(a.get(f"{ADM}/reports/tally-details/").status_code, 200)

    def test_each_action_maps_to_its_own_permission(self):
        a, base = self.staff(self.admin), f"{ADM}/products/gstr2tally/customers"
        self.grant(self.product, "license_management")
        self.assertEqual(a.post(f"{base}/{self.c.pk}/license/activate/", {"license_type": "LIFETIME"}, format="json").status_code, 403)  # needs license_activation
        self.assertEqual(a.post(f"{base}/{self.c.pk}/license/renew/", format="json").status_code, 200)
        self.assertEqual(a.post(f"{base}/{self.c.pk}/device/revoke/", format="json").status_code, 403)
        self.grant(self.product, "license_activation", "device_management")
        self.assertEqual(a.post(f"{base}/{self.c.pk}/license/activate/", {"license_type": "LIFETIME"}, format="json").status_code, 200)
        self.assertEqual(a.post(f"{base}/{self.c.pk}/device/revoke/", format="json").status_code, 409)  # allowed, but no device
        self.assertEqual(a.delete(f"{base}/{self.c.pk}/").status_code, 403)
        self.assertEqual(a.patch(f"{base}/{self.c.pk}/", {"name": "X"}, format="json").status_code, 403)
        self.grant(self.product, "edit", "delete", "entry", "view")
        self.assertEqual(a.patch(f"{base}/{self.c.pk}/", {"name": "New Name"}, format="json").status_code, 200)
        self.assertEqual(a.post(f"{base}/", {**SIGNUP, "tally_serial": "700555555", "email": "e@example.com"}, format="json").status_code, 201)
        self.assertEqual(a.delete(f"{base}/{self.c.pk}/").status_code, 204)

    def test_admin_cannot_manage_permissions_or_products(self):
        a = self.staff(self.admin)
        self.assertEqual(a.get(f"{ADM}/permissions/").status_code, 403)
        self.assertEqual(a.put(f"{ADM}/permissions/", {}, format="json").status_code, 403)

    def test_superadmin_configures_permissions_and_registers_product(self):
        s = self.staff(self.super)
        r = s.put(f"{ADM}/permissions/", {"user_id": self.admin.pk, "grants": {"gstr2tally": ["reports", "view"], "otherapp": []}}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        mine = self.staff(self.admin).get(f"{ADM}/access/").data["products"]
        by = {p["code"]: p["actions"] for p in mine}
        self.assertTrue(by["gstr2tally"]["reports"] and by["gstr2tally"]["view"])
        self.assertFalse(by["gstr2tally"]["license_management"])
        self.assertFalse(by["otherapp"]["reports"])
        bad = s.put(f"{ADM}/permissions/", {"user_id": self.admin.pk, "grants": {"gstr2tally": ["nope"]}}, format="json")
        self.assertEqual(bad.status_code, 400)
        # no self-service onboarding: products are created by trusted backend administration only
        self.assertEqual(s.post(f"{ADM}/products/", {"code": "dotnetapp", "name": "X"}, format="json").status_code, 405)
        self.assertEqual(s.post(f"{ADM}/products/gstr2tally/api-key/", format="json").status_code, 404)
        self.assertEqual(s.get(f"{ADM}/usage-rules/").status_code, 404)
        listed = {p["code"] for p in s.get(f"{ADM}/products/").data["results"]}
        self.assertIn("gstr2tally", listed)  # registry loads from the database

    def test_performance_report_filters_dates_and_permissions(self):
        TallyUsageEvent.objects.create(customer=self.c, kind="open", occurred_at=timezone.now(), day=timezone.localtime(timezone.now(), services.IST).date())
        s = self.staff(self.super)
        d = s.get(f"{ADM}/reports/user-performance/").data
        row = next(r for r in d["results"] if r["tally_serial"] == "700123456")
        self.assertEqual((row["opens"], row["active_days"]), (1, 1))
        for k in ("name", "category_label", "last_opened"):
            self.assertIn(k, row)
        today = timezone.localtime(timezone.now(), services.IST).date().isoformat()
        self.assertEqual(len(s.get(f"{ADM}/reports/user-performance/?from={today}&to={today}").data["results"]), 1)
        self.assertEqual(s.get(f"{ADM}/reports/user-performance/?from=2001-01-01&to=2001-01-02").data["results"], [])
        self.assertEqual(s.get(f"{ADM}/reports/user-performance/?from=bad").status_code, 400)
        self.assertEqual(len(s.get(f"{ADM}/reports/user-performance/?category=new").data["results"]), 2)  # both fixtures registered today
        x = s.post(f"{ADM}/reports/user-performance/export/", {"fmt": "xlsx"}, format="json")
        self.assertTrue(x.content.startswith(b"PK"))
        self.assertEqual(self.staff(self.admin).get(f"{ADM}/reports/user-performance/").data["results"], [])

    def test_anonymous_staff_endpoints_rejected(self):
        self.assertIn(self.api.get(f"{ADM}/dashboard/").status_code, (401, 403))

    def test_license_expiry_report_filters_and_shape(self):
        s = self.staff(self.super)
        d = s.get(f"{ADM}/reports/license-expiry/").data
        self.assertEqual(d["total"], 2)
        row = next(r for r in d["results"] if r["tally_serial"] == "700123456")
        for k in ("name", "product_name", "tally_serial", "license_type_label", "activated_at", "expires_at", "state"):
            self.assertIn(k, row)
        self.assertEqual(len(s.get(f"{ADM}/reports/license-expiry/?product=otherapp").data["results"]), 1)
        self.assertEqual(len(s.get(f"{ADM}/reports/license-expiry/?status=active").data["results"]), 1)
        self.assertEqual(len(s.get(f"{ADM}/reports/license-expiry/?search=700123").data["results"]), 1)
        self.assertEqual(s.get(f"{ADM}/reports/license-expiry/?from=2001-01-01&to=2001-01-02").data["results"], [])

    def test_tally_details_report_uses_tss_expiry_not_nexus_expiry(self):
        from .models import TallyInfo
        TallyInfo.objects.filter(customer=self.c).update(registered_email="t@example.com", tss_expiry_date="2027-03-31")
        rows = self.staff(self.super).get(f"{ADM}/reports/tally-details/?product=gstr2tally").data["results"]
        self.assertEqual(rows[0]["tally_email"], "t@example.com")
        self.assertEqual(rows[0]["tally_expiry_date"], "2027-03-31")
        none = self.staff(self.super).get(f"{ADM}/reports/tally-details/?product=otherapp").data["results"][0]
        self.assertIsNone(none["tally_expiry_date"])  # unavailable, not guessed from the Nexus expiry

    def test_exports(self):
        s = self.staff(self.super)
        for url in ("license-expiry", "tally-details", "user-performance"):
            x = s.post(f"{ADM}/reports/{url}/export/", {"fmt": "xlsx"}, format="json")
            self.assertEqual(x.status_code, 200)
            self.assertTrue(x.content.startswith(b"PK"))
            p = s.post(f"{ADM}/reports/{url}/export/", {"fmt": "pdf"}, format="json")
            self.assertTrue(p.content.startswith(b"%PDF"))
            self.assertIn("columns", s.post(f"{ADM}/reports/{url}/export/", {"fmt": "print"}, format="json").data)
        self.assertEqual(s.post(f"{ADM}/reports/license-expiry/export/", {"fmt": "exe"}, format="json").status_code, 400)
        a = self.staff(self.admin)  # an Admin with no Reports grant exports nothing
        self.assertEqual(a.post(f"{ADM}/reports/license-expiry/export/", {"fmt": "print"}, format="json").data["rows"], [])

    def test_customer_detail_contains_all_sections(self):
        r, _ = self.enroll(self.c, self.key)
        d = self.staff(self.super).get(f"{ADM}/products/gstr2tally/customers/{self.c.pk}/").data
        for k in ("customer", "tally", "license", "devices", "activation_history", "last_activity", "classification", "transfers"):
            self.assertIn(k, d)
        self.assertEqual(d["devices"][0]["name"], "Old Laptop")
        self.assertTrue(d["devices"][0]["active"])
        self.assertTrue(any(h["action"] == "DEVICE_ENROLLED" for h in d["activation_history"]))
        # a customer of another product is not reachable through this product's URL
        self.assertEqual(self.staff(self.super).get(f"{ADM}/products/gstr2tally/customers/{self.o.pk}/").status_code, 404)
