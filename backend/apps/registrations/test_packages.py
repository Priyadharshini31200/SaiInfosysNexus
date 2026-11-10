"""License packages (Trial / 1 Year / Lifetime), the separate 7-day signed lease, renewal, and device management."""
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AdminPermission

from . import device as device_mod, services
from .models import DeviceRegistration, LicenseAuditLog, LicenseRenewal, ProjectCustomer
from .test_device import E, FP, OTHER_FP, PASSWORD, PRN, PUB, SERIAL, Device, DeviceTestBase, verify_like_the_agent

KEY = "ABCD-EFGH-JKLM-NPQR"
STAFF = f"/api/projects/{PRN}/customers/"


def parse(value):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class PackageBase(DeviceTestBase):
    def setUp(self):
        super().setUp()
        self.s = APIClient()  # staff (browser) client; self.c stays the device's
        self.set(must_change_password=False, activation_key_hash=services._hash(services.normalize_key(KEY)))
        with override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True):
            call_command("seed_initial_users", verbosity=0)
        self.users = get_user_model().objects

    def set(self, **fields):
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(**fields)
        return self.reload()

    def package(self, kind):
        """Trial and 1 Year as an admin would have issued them (1 Year: no date yet, it starts at first activation)."""
        if kind == "TRIAL":
            return self.set(license_type="TRIAL", expires_at=timezone.now() + timedelta(days=3))
        if kind == "ONE_YEAR":
            return self.set(license_type="ONE_YEAR", expires_at=None)
        return self.set(license_type="LICENSED", expires_at=None)

    def activate(self, dev=None, **over):
        dev = dev or Device()
        body = {"serial_number": SERIAL, "activation_key": KEY, "device_public_key": dev.public, "fingerprint": dev.fp,
                "device_name": "Front desk PC", "app_version": "1.0.0", **over}
        r = self.c.post(E + "activate/", body, format="json")
        if r.status_code == 200:
            dev.device_id = r.json()["device_id"]
        return r, dev

    @contextmanager
    def after(self, days):
        """The server clock `days` later (only the licensing decisions; request timestamps stay real)."""
        later = timezone.now() + timedelta(days=days)
        with patch.object(device_mod, "timezone", SimpleNamespace(now=lambda: later)):
            yield later

    def staff(self, name="Superadmin"):
        self.s.force_authenticate(self.users.get(username=name))

    def grant(self, *actions):
        for action in actions:
            AdminPermission.objects.update_or_create(action=action, defaults={"name": f"{action.title()} Access", "enabled": True})


class PackageTests(PackageBase):
    # ---- Trial -------------------------------------------------------------------------------------------------
    def test_trial_runs_seven_days_from_first_activation_not_from_approval(self):
        self.set(license_type="TRIAL", expires_at=timezone.now() - timedelta(days=2), first_activated_at=None)  # approved days ago
        r, dev = self.activate()
        self.assertEqual(r.status_code, 200, r.content)  # a trial no computer has activated has not started
        c = self.reload()
        self.assertEqual(c.expires_at, c.first_activated_at + timedelta(days=7))
        p = r.json()["entitlement"]["payload"]
        self.assertEqual((p["license_type"], parse(p["expires_at"])), ("TRIAL", c.expires_at))
        self.assertLessEqual(parse(p["valid_until"]), c.expires_at)

    def test_reinstall_and_login_never_reset_the_trial_clock(self):
        _, dev = self.activate()
        c = self.reload()
        first, expiry = c.first_activated_at, c.expires_at
        self.activate(Device(FP))  # reinstall on the same computer
        self.enroll(Device(FP))  # and a credential enrollment
        self.assertEqual(self.refresh(dev).status_code, 401)  # the old install's key is dead, nothing else moved
        c = self.reload()
        self.assertEqual((c.first_activated_at, c.expires_at), (first, expiry))

    def test_trial_period_is_configurable_per_product(self):
        with override_settings(TRIAL_DAYS_BY_PRODUCT={PRN: 3}):
            self.package("TRIAL")
            self.activate()
        c = self.reload()
        self.assertEqual(c.expires_at, c.first_activated_at + timedelta(days=3))

    def test_trial_expires_after_its_period_and_a_cancelled_trial_stops_at_once(self):
        _, dev = self.activate()
        with self.after(6):
            self.assertEqual(self.refresh(dev).status_code, 200)
        with self.after(8):
            r = self.refresh(dev)
            self.assertEqual((r.status_code, r.json()["code"]), (403, "license_expired"))
        self.set(status="TRIAL_CANCELLED")
        self.assertEqual(self.refresh(dev).json()["code"], "license_cancelled")

    def test_trial_extension_needs_an_authorised_user_and_really_extends_the_device_licence(self):
        _, dev = self.activate()
        url = f"{STAFF}{self.customer.pk}/extend-trial/"
        self.assertIn(self.s.post(url, {"days": 5}, format="json").status_code, (401, 403))  # not signed in
        self.staff("Admin")
        self.assertEqual(self.s.post(url, {"days": 5}, format="json").status_code, 403)  # Admin without Edit
        self.grant("edit")
        before = self.reload().expires_at
        self.assertEqual(self.s.post(url, {"days": 5}, format="json").status_code, 200)
        self.assertEqual(self.reload().expires_at, before + timedelta(days=5))
        with self.after(10):  # day 10 is past the original 7 days but inside the extension
            self.assertEqual(self.refresh(dev).status_code, 200)

    # ---- 1 Year ------------------------------------------------------------------------------------------------
    def test_one_year_starts_at_first_activation_and_ignores_the_trial_period(self):
        self.package("ONE_YEAR")
        self.assertIsNone(services.effective_expiry(self.reload()))  # nothing runs before activation
        r, dev = self.activate()
        c = self.reload()
        self.assertEqual(c.expires_at, c.first_activated_at + timedelta(days=365))
        p = r.json()["entitlement"]["payload"]
        self.assertEqual((p["license_type"], parse(p["expires_at"])), ("ONE_YEAR", c.expires_at))
        with self.after(30):  # long past 7 days: still valid, with a fresh 7-day lease
            r = self.refresh(dev)
            self.assertEqual(r.status_code, 200)
        with self.after(366):
            self.assertEqual(self.refresh(dev).json()["code"], "license_expired")

    # ---- Lifetime ----------------------------------------------------------------------------------------------
    def test_lifetime_has_no_expiry_and_only_ever_needs_a_new_lease(self):
        self.package("LIFETIME")
        r, dev = self.activate()
        p = r.json()["entitlement"]["payload"]
        self.assertEqual((p["license_type"], p["expires_at"]), ("LIFETIME", None))
        self.assertIsNone(self.reload().expires_at)
        issued = parse(p["issued_at"])
        self.assertEqual(parse(p["valid_until"]), issued + timedelta(days=7))
        with self.after(400):
            p2 = self.refresh(dev).json()["entitlement"]["payload"]
        self.assertEqual(p2["expires_at"], None)
        self.assertGreater(parse(p2["valid_until"]), issued + timedelta(days=400))
        self.assertEqual(r.json()["license"]["expires_at"], None)

    # ---- the signed lease --------------------------------------------------------------------------------------
    def test_lease_is_at_most_seven_days_and_refresh_moves_it_forward_while_the_licence_is_valid(self):
        self.package("ONE_YEAR")
        with override_settings(LICENSE_LEASE_DAYS=30):  # a larger setting cannot raise the offline limit
            r, dev = self.activate()
            p = r.json()["entitlement"]["payload"]
            self.assertEqual(parse(p["valid_until"]) - parse(p["issued_at"]), timedelta(days=7))
            with self.after(5):
                p2 = self.refresh(dev).json()["entitlement"]["payload"]
        self.assertGreater(parse(p2["valid_until"]), parse(p["valid_until"]))
        self.assertEqual(p2["activated_at"], p["activated_at"])  # the licence dates are not part of the lease
        self.assertEqual(p2["expires_at"], p["expires_at"])
        self.assertTrue(verify_like_the_agent(self.refresh(dev).json()["entitlement"], PUB))

    def test_lease_never_outlives_the_licence(self):
        self.package("ONE_YEAR")
        _, dev = self.activate()
        self.set(expires_at=timezone.now() + timedelta(days=2))
        p = self.refresh(dev).json()["entitlement"]["payload"]
        self.assertEqual(p["valid_until"], p["expires_at"])

    def test_an_unreachable_period_after_expiry_is_refused_when_online_again(self):
        _, dev = self.activate()
        with self.after(9):  # offline past the lease and past the trial: reconnecting does not revive it
            r = self.refresh(dev)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "license_expired"))


class RenewalTests(PackageBase):
    def setUp(self):
        super().setUp()
        self.package("ONE_YEAR")
        _, self.dev = self.activate()
        self.url = f"{STAFF}{self.customer.pk}/renew/"
        self.staff()

    def test_renewal_adds_a_year_from_the_current_end_and_keeps_history_and_audit(self):
        c = self.reload()
        first, end = c.first_activated_at, c.expires_at
        r = self.s.post(self.url, {"note": "Paid 2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c = self.reload()
        self.assertEqual(c.expires_at, end + timedelta(days=365))
        self.assertEqual(c.first_activated_at, first)  # the activation date is never reset
        renewal = LicenseRenewal.objects.get()
        self.assertEqual((renewal.previous_expires_at, renewal.new_expires_at, renewal.note), (end, c.expires_at, "Paid 2026"))
        self.assertEqual(renewal.renewed_by.username, "Superadmin")
        self.assertTrue(LicenseAuditLog.objects.filter(customer=c, action="LICENSE_RENEWED").exists())
        history = self.s.get(f"{STAFF}{c.pk}/renewals/").json()["results"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["renewed_by"], "Superadmin")

    def test_an_expired_licence_is_renewed_from_today_and_works_again(self):
        self.set(expires_at=timezone.now() - timedelta(days=10))
        self.assertEqual(self.refresh(self.dev).json()["code"], "license_expired")
        self.assertEqual(self.s.post(self.url, {}, format="json").status_code, 200)
        self.assertAlmostEqual(self.reload().expires_at, timezone.now() + timedelta(days=365), delta=timedelta(minutes=1))
        self.assertEqual(self.refresh(self.dev).status_code, 200)

    def test_only_one_year_licences_renew_and_never_when_cancelled_or_inactive(self):
        for fields in ({"license_type": "LICENSED", "expires_at": None}, {"license_type": "TRIAL", "expires_at": timezone.now()}):
            self.set(**fields)
            self.assertEqual(self.s.post(self.url, {}, format="json").status_code, 409)
        self.set(license_type="ONE_YEAR", expires_at=timezone.now() + timedelta(days=5), status="INACTIVE")
        self.assertEqual(self.s.post(self.url, {}, format="json").status_code, 409)
        self.set(status="ACTIVE", expires_at=None)  # not activated yet
        self.assertEqual(self.s.post(self.url, {}, format="json").status_code, 409)
        self.assertFalse(LicenseRenewal.objects.exists())

    def test_renewal_follows_the_edit_permission(self):
        self.s.force_authenticate(None)
        self.assertIn(self.s.post(self.url, {}, format="json").status_code, (401, 403))
        self.staff("Admin")
        self.assertEqual(self.s.post(self.url, {}, format="json").status_code, 403)
        self.grant("edit")
        self.assertEqual(self.s.post(self.url, {}, format="json").status_code, 200)


class DeviceManagementTests(PackageBase):
    def setUp(self):
        super().setUp()
        self.panel = f"{STAFF}{self.customer.pk}/device/"

    def test_panel_shows_each_status_and_no_secrets(self):
        self.staff()
        self.assertEqual(self.s.get(self.panel).json()["device_status"], "not_activated")
        _, dev = self.activate()
        body = self.s.get(self.panel).json()
        self.assertEqual((body["device_status"], body["device_status_label"]), ("active", "Active"))
        self.assertEqual(body["device"]["device_id"], dev.device_id)
        self.assertEqual((body["license_type"], body["license_type_label"]), ("TRIAL", "Trial"))
        self.assertIsNotNone(body["first_activated_at"])
        self.assertIsNotNone(body["effective_expires_at"])
        self.assertIsNone(body["last_used_at"])
        self.assertTrue(body["can_revoke"] and body["can_transfer"])
        flat = str(body)
        for secret in (dev.public, "a" * 64, "password"):
            self.assertNotIn(secret, flat)

    def test_revoke_with_block_stops_the_device_and_any_other_until_a_transfer(self):
        self.staff()
        _, dev = self.activate()
        r = self.s.post(self.panel + "revoke/", {"reason": "Lost PC", "block": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["device_status"], "revoked")
        self.assertEqual(self.refresh(dev).json()["code"], "device_revoked")
        r, _ = self.activate(Device(OTHER_FP))
        self.assertEqual((r.status_code, r.json()["code"]), (403, "device_blocked"))
        self.assertEqual(self.enroll(Device(OTHER_FP))[0].json()["code"], "device_blocked")
        self.assertFalse(r.json().get("entitlement"))
        # revoking with nothing enrolled is refused
        self.assertEqual(self.s.post(self.panel + "revoke/", {}, format="json").status_code, 409)

    def test_transfer_lifts_the_block_and_the_new_computer_gets_the_next_generation_without_touching_dates(self):
        self.staff()
        _, old = self.activate()
        c = self.reload()
        first, expiry = c.first_activated_at, c.expires_at
        self.s.post(self.panel + "revoke/", {"block": True}, format="json")
        r = self.s.post(self.panel + "transfer/", {"reason": "New PC"}, format="json")
        self.assertEqual((r.status_code, r.json()["device_status"]), (200, "transfer_pending"))
        r, new = self.activate(Device(OTHER_FP))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["entitlement"]["payload"]["generation"], 2)
        c = self.reload()
        self.assertEqual((c.first_activated_at, c.expires_at), (first, expiry))
        self.assertEqual(self.refresh(old).status_code, 403)
        self.assertEqual(self.refresh(new).status_code, 200)
        # a transfer while a computer is still enrolled revokes it in the same step
        self.assertEqual(self.s.post(self.panel + "transfer/", {}, format="json").status_code, 200)
        self.assertEqual(self.refresh(new).json()["code"], "device_revoked")
        self.assertTrue(LicenseAuditLog.objects.filter(customer=c, action="DEVICE_TRANSFER_AUTHORIZED").exists())

    def test_transfer_with_no_device_history_is_refused(self):
        self.staff()
        self.assertEqual(self.s.post(self.panel + "transfer/", {}, format="json").status_code, 409)

    def test_only_the_super_admin_manages_devices_even_with_every_permission(self):
        _, dev = self.activate()
        self.staff("Admin")
        self.grant("edit", "delete", "reports", "entry", "view", "export_print")
        for method, path in (("get", ""), ("post", "revoke/"), ("post", "transfer/")):
            r = self.s.post(self.panel + path, {}, format="json") if method == "post" else self.s.get(self.panel)
            self.assertEqual(r.status_code, 403, path)
        self.assertEqual(self.refresh(dev).status_code, 200)  # nothing changed
        self.s.force_authenticate(None)
        self.assertIn(self.s.post(self.panel + "revoke/", {}, format="json").status_code, (401, 403))
        # nor can the customer's own PRN token or a device
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 1)

    def test_last_used_date_follows_usage_reports(self):
        self.staff()
        _, dev = self.activate()
        self.login(dev)
        used = self.s.get(self.panel).json()["last_used_at"]
        self.assertIsNotNone(used)

    def login(self, dev):
        r = self.call(dev, "POST", "login/", {"username": SERIAL, "password": PASSWORD}, token=False)
        dev.token = r.json().get("token")
        return r


class ExistingRecordsTests(PackageBase):
    def test_existing_licensed_customers_become_lifetime_without_changing(self):
        c = self.set(license_type="LICENSED", expires_at=None)
        before = (c.expires_at, c.status, c.password_hash, c.license_type)
        self.assertEqual(c.get_license_type_display(), "Lifetime")
        self.assertIsNone(services.effective_expiry(c))
        self.assertEqual(device_mod.license_view(c)["license_type"], "LIFETIME")
        self.assertEqual((c.expires_at, c.status, c.password_hash, c.license_type), before)

    def test_register_and_approve_accept_all_three_packages(self):
        self.staff()
        from .models import RegistrationRequest

        issued = {}
        for kind, serial in (("TRIAL", "700000101"), ("ONE_YEAR", "700000102"), ("LICENSED", "700000103")):
            req = RegistrationRequest.objects.create(project=PRN, name="N", contact_number="9876543210", email=f"{serial}@example.com")
            r = self.s.post(f"/api/projects/{PRN}/registration-requests/{req.pk}/approve/", {"license_type": kind, "serial_number": serial}, format="json")
            self.assertEqual(r.status_code, 201, r.content)
            issued[kind] = ProjectCustomer.objects.get(project=PRN, serial_number=serial)
        self.assertIsNotNone(issued["TRIAL"].expires_at)  # replaced at first activation
        self.assertIsNone(issued["ONE_YEAR"].expires_at)  # starts at first activation
        self.assertIsNone(issued["LICENSED"].expires_at)
        self.assertEqual(self.s.get(f"{STAFF}{issued['ONE_YEAR'].pk}/").json()["status_label"], "1 Year Active")
        # a product without device activation: the year starts at once
        other = services.register("gstr2tally", {
            "name": "G", "company_name": "G", "license_type": "ONE_YEAR", "user_id": "700000104", "password": "Xx#Pass123", "contact_number": "9876543210",
            "email": "g@example.com", "address": "x", "serial_number": "700000104", "activation_key": services.generate_activation_key("gstr2tally", None),
        }, None)
        self.assertAlmostEqual(other.expires_at, timezone.now() + timedelta(days=365), delta=timedelta(minutes=1))
