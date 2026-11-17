"""First-run activation with Serial Number + Activation Key, and device usage reporting."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from . import services
from .models import DeviceRegistration, LicenseAuditLog, ProjectCustomer
from .test_device import E, FP, OTHER_FP, PASSWORD, PRN, PUB, SERIAL, Device, DeviceTestBase, verify_like_the_agent

KEY = "ABCD-EFGH-JKLM-NPQR"


@override_settings(ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE=True)
class ActivationTests(DeviceTestBase):

    def setUp(self):
        super().setUp()
        # a freshly approved account: temporary password, activation key issued
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(
            must_change_password=True, activation_key_hash=services._hash(services.normalize_key(KEY))
        )

    def activate(self, dev=None, serial=SERIAL, key=KEY, **over):
        dev = dev or Device()
        body = {"serial_number": serial, "activation_key": key, "device_public_key": dev.public, "fingerprint": dev.fp,
                "device_name": "Front desk PC", "app_version": "1.0.0", **over}
        r = self.client_post("activate/", body)
        if r.status_code == 200:
            dev.device_id = r.json()["device_id"]
        return r, dev

    def client_post(self, path, body):
        return self.c.post(E + path, body, format="json")

    def login(self, dev, password=PASSWORD):
        r = self.call(dev, "POST", "login/", {"username": SERIAL, "password": password}, token=False)
        if r.status_code == 200:
            dev.token = r.json()["token"]
        return r

    # ---- activation ---------------------------------------------------------------------------------------------
    def test_serial_and_key_issue_a_signed_device_bound_entitlement_without_a_password(self):
        before = self.reload().expires_at
        r, dev = self.activate()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(verify_like_the_agent(body["entitlement"], PUB))
        p = body["entitlement"]["payload"]
        self.assertEqual((p["serial"], p["device_id"], p["device"]), (SERIAL, dev.device_id, FP["smbios"]))
        self.assertNotIn("token", body)  # activation proves the licence, not the person: sign-in still needs the password
        self.assertEqual(body["account"], {"user_id": SERIAL, "serial_number": SERIAL, "must_change_password": True})
        c = self.reload()
        self.assertIsNotNone(c.first_activated_at)
        self.assertEqual(c.expires_at, c.first_activated_at + timedelta(days=7))
        self.assertTrue(LicenseAuditLog.objects.filter(customer=c, action="DEVICE_ENROLLED").exists())
        flat = r.content.decode().lower()
        self.assertNotIn("password_hash", flat)
        self.assertNotIn(KEY.lower(), flat)

    def test_full_first_run_flow_activate_sign_in_change_password_then_work(self):
        r, dev = self.activate()
        self.assertEqual(r.status_code, 200)
        login = self.login(dev)  # the temporary password from the approval email
        self.assertEqual(login.status_code, 200, login.content)
        self.assertTrue(login.json()["must_change_password"])
        # not usable until the password is replaced
        self.assertEqual(self.call(dev, "POST", "usage/", {"event": "print"}).status_code, 403)
        done = self.call(dev, "POST", "change-password/", {"current_password": PASSWORD, "new_password": "Brand#New9pass"})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(self.login(dev, PASSWORD).status_code, 401)  # the old one stopped working
        relog = self.login(dev, "Brand#New9pass")
        self.assertEqual(relog.status_code, 200)
        self.assertFalse(relog.json()["must_change_password"])
        self.assertEqual(self.call(dev, "GET", "license/status/").status_code, 200)

    def test_wrong_serial_or_key_are_indistinguishable_and_change_nothing(self):
        for serial, key in ((SERIAL, "AAAA-BBBB-CCCC-DDDD"), ("799999999", KEY), ("", KEY), (SERIAL, "")):
            r, _ = self.activate(serial=serial, key=key)
            self.assertIn(r.status_code, (400, 401), r.content)
            if r.status_code == 401:
                self.assertEqual(r.json()["code"], "invalid_activation")
        self.assertFalse(DeviceRegistration.objects.exists())
        self.assertIsNone(self.reload().first_activated_at)

    def test_key_is_forgiving_about_case_and_dashes_and_the_api_is_prn_only(self):
        r, _ = self.activate(key=KEY.lower().replace("-", ""))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.post("/api/device/gstr2tally/activate/", {}, format="json").status_code, 404)

    def test_only_one_computer_per_licence_and_reinstall_keeps_dates(self):
        r1, d1 = self.activate()
        first = self.reload().first_activated_at
        r2, _ = self.activate(Device(OTHER_FP))
        self.assertEqual((r2.status_code, r2.json()["code"]), (409, "device_limit"))
        r3, d3 = self.activate(Device(FP))  # same computer, fresh key
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()["device_id"], r1.json()["device_id"])
        self.assertEqual(self.reload().first_activated_at, first)
        self.assertEqual(r3.json()["entitlement"]["payload"]["generation"], 1)
        self.assertEqual(self.call(d1, "POST", "entitlement/", {"fingerprint": FP}).status_code, 401)  # old key is dead

    def test_cancelled_inactive_and_expired_licences_cannot_activate(self):
        expired = {"license_type": ProjectCustomer.TRIAL, "expires_at": timezone.now() - timedelta(days=1), "first_activated_at": timezone.now() - timedelta(days=8)}
        for fields, code in (
            ({"status": ProjectCustomer.INACTIVE}, "account_inactive"),
            ({"status": ProjectCustomer.TRIAL_CANCELLED}, "license_cancelled"),
            ({"status": ProjectCustomer.ACTIVE, **expired}, "license_expired"),
        ):
            ProjectCustomer.objects.filter(pk=self.customer.pk).update(**fields)
            r, _ = self.activate()
            self.assertEqual((r.status_code, r.json()["code"]), (403, code))
        self.assertFalse(DeviceRegistration.objects.exists())

    @override_settings(LICENSE_SIGNING_PRIVATE_KEY="")
    def test_activation_fails_closed_without_a_signing_key(self):
        r, _ = self.activate()
        self.assertEqual((r.status_code, r.json()["code"]), (503, "licensing_unavailable"))
        self.assertFalse(DeviceRegistration.objects.exists())

    # ---- usage ------------------------------------------------------------------------------------------------
    def test_usage_is_recorded_only_for_a_signed_in_active_device(self):
        _, dev = self.activate()
        self.assertEqual(DeviceRegistration.objects.get().last_used_at, None)  # activating is not using
        self.assertEqual(self.refresh(dev).status_code, 200)
        self.assertIsNone(DeviceRegistration.objects.get().last_used_at)  # nor is a background refresh
        self.login(dev)
        self.call(dev, "POST", "change-password/", {"current_password": PASSWORD, "new_password": "Brand#New9pass"})
        self.login(dev, "Brand#New9pass")
        row = DeviceRegistration.objects.get()
        self.assertIsNotNone(row.last_used_at)  # a signed-in session is use
        used = self.call(dev, "POST", "usage/", {"event": "print", "count": 3, "app_version": "1.2.0"})
        self.assertEqual(used.status_code, 200, used.content)
        row.refresh_from_db()
        self.assertEqual((row.use_count, row.app_version), (4, "1.2.0"))
        self.assertEqual(self.call(dev, "POST", "usage/", {"event": "bogus"}).status_code, 400)
        self.assertEqual(self.call(dev, "POST", "usage/", {"event": "print", "count": 99999}).status_code, 400)

    def test_usage_needs_a_valid_signature_token_and_licence(self):
        _, dev = self.activate()
        self.login(dev)
        self.call(dev, "POST", "change-password/", {"current_password": PASSWORD, "new_password": "Brand#New9pass"})
        self.login(dev, "Brand#New9pass")
        stranger = Device()
        stranger.device_id, stranger.token = dev.device_id, dev.token
        self.assertEqual(self.call(dev, "POST", "usage/", {}, signer=stranger).status_code, 401)  # not signed by the device
        self.assertEqual(self.call(dev, "POST", "usage/", {}, token=False).status_code, 401)  # no customer token
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(status=ProjectCustomer.INACTIVE)
        count = DeviceRegistration.objects.get().use_count
        self.assertEqual(self.call(dev, "POST", "usage/", {}).status_code, 403)
        self.assertEqual(DeviceRegistration.objects.get().use_count, count)  # an inactive licence is never counted as used

    def test_staff_device_view_shows_usage_but_no_secrets(self):
        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        _, dev = self.activate()
        with override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True):
            call_command("seed_initial_users", verbosity=0)
        staff = get_user_model().objects.get(username="Superadmin")
        self.c.force_authenticate(staff)
        r = self.c.get(f"/api/projects/{PRN}/customers/{self.customer.pk}/device/")
        self.assertEqual(r.status_code, 200)
        flat = r.content.decode()
        self.assertIn("last_used_at", flat)
        self.assertNotIn(dev.public, flat)
        self.assertNotIn("a" * 64, flat)

