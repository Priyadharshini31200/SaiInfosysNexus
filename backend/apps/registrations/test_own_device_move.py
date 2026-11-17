"""A customer moves THEIR OWN licence to a new Windows computer: password-verified request -> Super Admin decision -> re-enrollment.
The Activation Key alone never moves a licence."""
from datetime import timedelta

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import DeviceRegistration, DeviceTransferRequest, LicenseAuditLog, ProjectCustomer
from .test_device import E, FP, OTHER_FP, PASSWORD, PRN, SERIAL, Device, DeviceTestBase

KEY = "ABCD-EFGH-JKLM-NPQR"
NEW_FP = {"smbios": "c" * 64, "machine": "d" * 64, "v3": "e" * 64}


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class OwnDeviceMoveTests(DeviceTestBase):
    def setUp(self):
        super().setUp()
        call_command("seed_initial_users", verbosity=0)
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(activation_key_hash=services._hash(services.normalize_key(KEY)))
        self.staff = APIClient()
        self.staff.post("/api/auth/login/", {"username": "Superadmin", "password": "Superadmin"}, format="json")

    def ask(self, path="transfer-request/", username=SERIAL, password=PASSWORD, fp=NEW_FP, **over):
        return self.c.post(E + path, {"username": username, "password": password, "fingerprint": fp, "device_name": "New PC", **over}, format="json")

    def decide(self, pk, verb, **body):
        return self.staff.post(f"/api/projects/{PRN}/device-transfer-requests/{pk}/{verb}/", body, format="json")

    def enroll_old(self):
        r, old = self.enroll()
        self.assertEqual(r.status_code, 200, r.content)
        return old

    # ---- activation key alone cannot move a licence ------------------------------------------------------------------
    def test_activation_key_alone_cannot_take_a_licence_that_is_on_another_computer(self):
        self.enroll_old()
        dev = Device(NEW_FP)
        r = self.c.post(E + "activate/", {"serial_number": SERIAL, "activation_key": KEY, "device_public_key": dev.public,
                                           "fingerprint": NEW_FP, "device_name": "New PC", "app_version": "1.0.0"}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "device_limit"))
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 1)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_a_transfer_request_cannot_be_made_with_the_activation_key_instead_of_the_password(self):
        self.enroll_old()
        r = self.ask(password=KEY)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_wrong_password_and_unknown_user_create_nothing(self):
        self.enroll_old()
        self.assertEqual(self.ask(password="Wrong#Pass1").status_code, 401)
        self.assertEqual(self.ask(username="799999999").status_code, 401)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_no_request_without_credentials(self):
        self.enroll_old()
        r = self.c.post(E + "transfer-request/", {"fingerprint": NEW_FP}, format="json")
        self.assertEqual(r.status_code, 400)

    # ---- the request ------------------------------------------------------------------------------------------------
    def test_request_is_pending_idempotent_and_changes_nothing(self):
        old = self.enroll_old()
        r = self.ask()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["status"], body["from_user_id"], body["to_user_id"]), ("pending", SERIAL, SERIAL))
        self.assertEqual(self.ask().json()["id"], body["id"])
        self.assertEqual(DeviceTransferRequest.objects.count(), 1)
        self.assertEqual(self.ask("transfer-request/status/").json(), {"needed": True, "state": "pending", "id": body["id"],
                                                                      "decided_at": None, "decision_reason": ""})
        # the old computer still works, and the new one is still refused (409 device_limit)
        self.assertEqual(self.call(old, "POST", "login/", {"username": SERIAL, "password": PASSWORD}).status_code, 200)
        r, _ = self.enroll(dev=Device(NEW_FP))
        self.assertEqual((r.status_code, r.json()["code"]), (409, "device_limit"))
        self.assertTrue(LicenseAuditLog.objects.filter(action="DEVICE_TRANSFER_REQUESTED").exists())

    def test_same_computer_needs_no_transfer(self):
        self.enroll_old()
        r = self.ask(fp=FP)
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_transfer_needed"))
        self.assertEqual(self.ask("transfer-request/status/", fp=FP).json(), {"needed": False, "state": "none"})

    def test_first_time_activation_needs_no_transfer(self):
        r = self.ask()
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_transfer_needed"))
        r, _ = self.enroll(dev=Device(NEW_FP))
        self.assertEqual(r.status_code, 200, r.content)

    def test_blocked_licence_is_refused(self):
        self.enroll_old()
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(device_blocked=True)
        r = self.ask()
        self.assertEqual((r.status_code, r.json()["code"]), (403, "device_blocked"))

    # ---- approval ---------------------------------------------------------------------------------------------------
    def test_approval_revokes_the_old_computer_and_the_new_one_registers_with_dates_unchanged(self):
        old = self.enroll_old()
        before = self.reload()
        first, expires = before.first_activated_at, before.expires_at
        rid = self.ask().json()["id"]
        r = self.decide(rid, "approve", reason="Customer bought a new PC")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "approved")
        # old authorisation is revoked, kept as history
        reg = DeviceRegistration.objects.get(device_id=old.device_id)
        self.assertIsNotNone(reg.revoked_at)
        self.assertIn("Transferred", reg.revoked_reason)
        self.assertEqual(self.refresh(old).status_code, 403)
        self.assertEqual(self.refresh(old).json()["code"], "device_revoked")
        # approval itself registers nothing; the new computer signs in with the customer's own credentials
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 0)
        # once the old computer is revoked nothing stands in the way: status says "no transfer needed" = sign in normally
        self.assertEqual(self.ask("transfer-request/status/").json(), {"needed": False, "state": "none"})
        r, new = self.enroll(dev=Device(NEW_FP))
        self.assertEqual(r.status_code, 200, r.content)
        active = DeviceRegistration.objects.filter(revoked_at__isnull=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get().generation, 2)
        after = self.reload()
        self.assertEqual((after.first_activated_at, after.expires_at), (first, expires))
        for action in ("DEVICE_TRANSFER_REQUESTED", "DEVICE_REVOKED", "DEVICE_TRANSFER_APPROVED", "DEVICE_ENROLLED"):
            self.assertTrue(LicenseAuditLog.objects.filter(action=action).exists(), action)

    def test_after_approval_the_old_computer_cannot_take_the_licence_back_without_a_new_transfer(self):
        old = self.enroll_old()
        self.decide(self.ask().json()["id"], "approve")
        self.enroll(dev=Device(NEW_FP))
        r, _ = self.enroll(dev=Device(FP))                       # old computer tries to register again
        self.assertEqual((r.status_code, r.json()["code"]), (409, "device_limit"))
        self.assertEqual(self.refresh(old).status_code, 403)

    def test_a_decided_request_cannot_be_decided_again(self):
        self.enroll_old()
        rid = self.ask().json()["id"]
        self.assertEqual(self.decide(rid, "approve").status_code, 200)
        self.assertEqual(self.decide(rid, "approve").status_code, 409)
        self.assertEqual(self.decide(rid, "reject").status_code, 409)
        self.assertEqual(DeviceTransferRequest.objects.get(pk=rid).status, "APPROVED")

    def test_approval_after_an_admin_already_moved_the_licence_does_not_fail_or_double_revoke(self):
        old = self.enroll_old()
        rid = self.ask().json()["id"]
        self.staff.post(f"/api/projects/{PRN}/customers/{self.customer.pk}/device/transfer/", {"reason": "manual"}, format="json")
        self.assertEqual(self.decide(rid, "approve").status_code, 200)
        self.assertEqual(DeviceRegistration.objects.filter(device_id=old.device_id).get().revoked_reason, "manual")

    # ---- rejection --------------------------------------------------------------------------------------------------
    def test_rejection_changes_no_registration_and_a_new_request_is_possible(self):
        old = self.enroll_old()
        rid = self.ask().json()["id"]
        r = self.decide(rid, "reject", reason="Not verified")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "rejected"))
        self.assertIsNone(DeviceRegistration.objects.get(device_id=old.device_id).revoked_at)
        self.assertEqual(self.call(old, "POST", "login/", {"username": SERIAL, "password": PASSWORD}).status_code, 200)
        r, _ = self.enroll(dev=Device(NEW_FP))
        self.assertEqual(r.json()["code"], "device_limit")
        self.assertEqual(self.ask("transfer-request/status/").json()["state"], "rejected")
        self.assertEqual(self.ask().status_code, 201)             # may ask again
        self.assertTrue(LicenseAuditLog.objects.filter(action="DEVICE_TRANSFER_REJECTED").exists())

    # ---- permissions ------------------------------------------------------------------------------------------------
    def test_only_a_super_admin_can_decide_or_list(self):
        self.enroll_old()
        rid = self.ask().json()["id"]
        for client in (APIClient(), self.c):                       # anonymous and the device/customer API client
            self.assertIn(client.post(f"/api/projects/{PRN}/device-transfer-requests/{rid}/approve/", {}, format="json").status_code, (401, 403))
            self.assertIn(client.get(f"/api/projects/{PRN}/device-transfer-requests/").status_code, (401, 403))
        admin = APIClient()
        admin.post("/api/auth/login/", {"username": "Admin", "password": "Admin"}, format="json")
        self.assertEqual(admin.post(f"/api/projects/{PRN}/device-transfer-requests/{rid}/approve/", {}, format="json").status_code, 403)
        self.assertEqual(DeviceTransferRequest.objects.get(pk=rid).status, "PENDING")
        self.assertEqual(self.staff.get(f"/api/projects/{PRN}/device-transfer-requests/?status=pending").json()["results"][0]["id"], rid)

    def test_a_customer_cannot_use_the_device_to_approve_their_own_request(self):
        old = self.enroll_old()
        rid = self.ask().json()["id"]
        r = self.call(old, "POST", f"../../../projects/{PRN}/device-transfer-requests/{rid}/approve/", {})
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(DeviceTransferRequest.objects.get(pk=rid).status, "PENDING")

    # ---- licence types and expiry -----------------------------------------------------------------------------------
    def _move(self, **fields):
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(**fields)
        old = self.enroll_old()
        before = self.reload().expires_at
        rid = self.ask().json()["id"]
        self.assertEqual(self.decide(rid, "approve").status_code, 200)
        r, new = self.enroll(dev=Device(NEW_FP))
        return r, before, old

    def test_trial_move_keeps_the_trial_end(self):
        r, before, _ = self._move(license_type="TRIAL")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.reload().expires_at, before)
        self.assertEqual(r.json()["license"]["license_type"], "TRIAL")

    def test_one_year_move_keeps_the_end_date(self):
        r, before, _ = self._move(license_type="ONE_YEAR", expires_at=timezone.now() + timedelta(days=200))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.reload().expires_at, before)

    def test_lifetime_move_stays_without_expiry(self):
        r, _, _ = self._move(license_type="LICENSED", expires_at=None)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json()["license"]["expires_at"])
        self.assertEqual(r.json()["license"]["license_type"], "LIFETIME")

    def test_an_expired_licence_cannot_ask_for_a_transfer_or_enroll(self):
        old = self.enroll_old()
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.ask().status_code, 403)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_expiry_between_request_and_approval_still_blocks_the_new_computer(self):
        self.enroll_old()
        rid = self.ask().json()["id"]
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.decide(rid, "approve").status_code, 200)
        r, _ = self.enroll(dev=Device(NEW_FP))
        self.assertEqual((r.status_code, r.json()["code"]), (403, "license_expired"))

    def test_one_active_device_per_licence_always(self):
        self.enroll_old()
        self.decide(self.ask().json()["id"], "approve")
        self.enroll(dev=Device(NEW_FP))
        r, _ = self.enroll(dev=Device(OTHER_FP))
        self.assertEqual(r.json()["code"], "device_limit")
        self.assertEqual(DeviceRegistration.objects.filter(customer=self.customer, revoked_at__isnull=True).count(), 1)
