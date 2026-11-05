"""Device transfer between customers: a computer registered to one customer is never handed to another without Super Admin approval."""
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from .models import DeviceRegistration, DeviceTransferRequest, LicenseAuditLog
from .test_device import E, FP, OTHER_FP, PASSWORD, PRN, SERIAL, Device, DeviceTestBase
from .test_portal import make_customer

B_SERIAL, B_PASSWORD = "712999001", "Other#Pass9"


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class DeviceTransferTests(DeviceTestBase):
    def setUp(self):
        super().setUp()
        call_command("seed_initial_users", verbosity=0)
        self.customer_b = make_customer(PRN, B_SERIAL, "other@example.com", password=B_PASSWORD, must_change_password=False)
        self.staff = APIClient()
        self.staff.post("/api/auth/login/", {"username": "Superadmin", "password": "Superadmin"}, format="json")

    def ask(self, path="transfer-request/", username=B_SERIAL, password=B_PASSWORD, fp=FP, **over):
        return self.c.post(E + path, {"username": username, "password": password, "fingerprint": fp, "device_name": "Counter PC", **over}, format="json")

    # ---- the starting point: the computer belongs to customer A -----------------------------------------------------
    def test_customer_a_registered_then_customer_b_is_refused_on_the_same_computer(self):
        r, a = self.enroll()
        self.assertEqual(r.status_code, 200)
        # B has no key on this computer (reinstall / lost key file) and tries to register it: refused, nothing is created
        r, _ = self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "device_bound_to_other"))
        self.assertEqual(DeviceRegistration.objects.count(), 1)
        self.assertEqual(DeviceRegistration.objects.get().customer_id, self.customer.pk)
        # B signs in with the key that belongs to A's registration: refused as another customer's computer
        r = self.call(a, "POST", "login/", {"username": B_SERIAL, "password": B_PASSWORD})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "wrong_device_account"))
        # A keeps working
        self.assertEqual(self.call(a, "POST", "login/", {"username": SERIAL, "password": PASSWORD}).status_code, 200)

    def test_an_available_computer_registers_normally(self):
        r, _ = self.enroll(dev=Device(OTHER_FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_customer_can_reinstall_on_their_own_computer(self):
        self.enroll()
        r, _ = self.enroll(dev=Device(FP))                       # same customer, same hardware, new key
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 1)

    # ---- the request -------------------------------------------------------------------------------------------------
    def test_transfer_request_needs_the_customers_own_valid_credentials(self):
        self.enroll()
        r = self.ask(password="Wrong#Pass1")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_request_is_recorded_pending_and_is_idempotent(self):
        self.enroll()
        r = self.ask()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["status"], body["from_user_id"], body["to_user_id"]), ("pending", SERIAL, B_SERIAL))
        self.assertNotIn("fingerprint", str(body))
        again = self.ask()
        self.assertEqual(again.json()["id"], body["id"])
        self.assertEqual(DeviceTransferRequest.objects.count(), 1)
        self.assertTrue(LicenseAuditLog.objects.filter(action="DEVICE_TRANSFER_REQUESTED", customer=self.customer_b).exists())
        # nothing changed yet: A's registration is intact and B still cannot register
        self.assertIsNone(DeviceRegistration.objects.get().revoked_at)
        r, _ = self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual(r.json()["code"], "device_bound_to_other")

    def test_no_request_when_no_transfer_is_needed(self):
        r = self.ask()                                            # nobody has registered this computer
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_transfer_needed"))
        self.enroll()
        r = self.ask(username=SERIAL, password=PASSWORD)          # A asking for a computer that is already A's
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_transfer_needed"))

    def test_status_reports_none_pending_rejected(self):
        self.enroll()
        self.assertEqual(self.ask("transfer-request/status/").json(), {"needed": True, "state": "none"})
        req_id = self.ask().json()["id"]
        s = self.ask("transfer-request/status/").json()
        self.assertEqual((s["needed"], s["state"], s["id"]), (True, "pending", req_id))
        url = f"/api/projects/{PRN}/device-transfer-requests/{req_id}/reject/"
        self.assertEqual(self.staff.post(url, {"reason": "Not authorised"}, format="json").status_code, 200)
        s = self.ask("transfer-request/status/").json()
        self.assertEqual((s["state"], s["decision_reason"]), ("rejected", "Not authorised"))
        self.assertIsNone(DeviceRegistration.objects.get().revoked_at)       # rejection changes no registration
        self.assertEqual(self.ask().status_code, 201)                         # a new request may follow

    # ---- the decision -----------------------------------------------------------------------------------------------
    def test_only_a_super_admin_can_list_and_decide(self):
        self.enroll()
        req_id = self.ask().json()["id"]
        admin = APIClient()
        admin.post("/api/auth/login/", {"username": "Admin", "password": "Admin"}, format="json")
        for client in (admin, APIClient()):
            self.assertIn(client.get(f"/api/projects/{PRN}/device-transfer-requests/").status_code, (401, 403))
            self.assertIn(client.post(f"/api/projects/{PRN}/device-transfer-requests/{req_id}/approve/", {}, format="json").status_code, (401, 403))
        self.assertEqual(self.staff.get(f"/api/projects/{PRN}/device-transfer-requests/?status=pending").json()["results"][0]["id"], req_id)
        self.assertEqual(DeviceTransferRequest.objects.get().status, "PENDING")

    def test_approval_revokes_the_old_binding_keeps_history_and_lets_b_register(self):
        _, a = self.enroll()
        old_id = DeviceRegistration.objects.get().device_id
        req_id = self.ask().json()["id"]
        r = self.staff.post(f"/api/projects/{PRN}/device-transfer-requests/{req_id}/approve/", {"reason": "Shop sold to B"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["status"], r.json()["decided_by"]), ("approved", "Superadmin"))
        # the previous binding is revoked, NOT deleted
        old = DeviceRegistration.objects.get(pk=old_id)
        self.assertIsNotNone(old.revoked_at)
        self.assertIn(B_SERIAL, old.revoked_reason)
        self.assertEqual(DeviceRegistration.objects.count(), 1)
        # A's old key is told it was revoked (it proved it holds the key)
        r = self.call(a, "POST", "entitlement/", {"fingerprint": FP})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "device_revoked"))
        # B now registers this computer normally, with a NEW registration
        r, b = self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotEqual(r.json()["device_id"], str(old_id))
        self.assertEqual(DeviceRegistration.objects.count(), 2)
        self.assertEqual(DeviceRegistration.objects.get(pk=r.json()["device_id"]).customer_id, self.customer_b.pk)
        self.assertEqual(self.call(b, "POST", "login/", {"username": B_SERIAL, "password": B_PASSWORD}).status_code, 200)
        # A can no longer take the computer back without a transfer of their own
        r, _ = self.enroll(dev=Device(FP))
        self.assertEqual((r.status_code, r.json()["code"]), (403, "device_bound_to_other"))
        # the whole story is in the audit log
        actions = set(LicenseAuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"DEVICE_ENROLLED", "DEVICE_TRANSFER_REQUESTED", "DEVICE_REVOKED", "DEVICE_TRANSFER_APPROVED"} <= actions)
        # a decided request cannot be decided twice
        again = self.staff.post(f"/api/projects/{PRN}/device-transfer-requests/{req_id}/approve/", {}, format="json")
        self.assertEqual(again.status_code, 409)

    def test_a_licence_that_is_not_usable_cannot_ask(self):
        self.enroll()
        type(self.customer_b).objects.filter(pk=self.customer_b.pk).update(status="INACTIVE")
        r = self.ask()
        self.assertEqual(r.status_code, 403, r.content)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_the_drawer_shows_pending_requests_to_the_super_admin(self):
        self.enroll()
        self.ask()
        info = self.staff.get(f"/api/projects/{PRN}/customers/{self.customer.pk}/device/").json()
        self.assertEqual(len(info["transfer_requests"]), 1)
        self.assertEqual(info["transfer_requests"][0]["to_user_id"], B_SERIAL)
        self.assertNotIn("fingerprint", str(info))
