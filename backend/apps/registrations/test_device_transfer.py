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

    # ---- one computer, several customers (a computer is not exclusive to one customer) ----------------------------------
    def test_customer_b_registers_the_same_computer_independently_of_customer_a(self):
        r, a = self.enroll()
        self.assertEqual(r.status_code, 200)
        r, b = self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 2)
        self.assertEqual(set(DeviceRegistration.objects.values_list("customer_id", flat=True)), {self.customer.pk, self.customer_b.pk})
        self.assertFalse(DeviceTransferRequest.objects.exists())            # no reassignment request was needed
        self.assertEqual(self.call(b, "POST", "login/", {"username": B_SERIAL, "password": B_PASSWORD}).status_code, 200)
        # B's key cannot read A's data: A's credentials through B's key ask the app to sign in again, and return nothing of A
        r = self.call(b, "POST", "login/", {"username": SERIAL, "password": PASSWORD})
        self.assertEqual((r.status_code, r.json()["code"]), (401, "device_auth_required"))
        self.assertNotIn("Ravi", r.content.decode())

    def test_an_available_computer_registers_normally(self):
        r, _ = self.enroll(dev=Device(OTHER_FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_customer_can_reinstall_on_their_own_computer(self):
        self.enroll()
        r, _ = self.enroll(dev=Device(FP))                       # same customer, same hardware, new key
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 1)

    # ---- a transfer is only for a customer's OWN licence moving to a different computer -------------------------------------
    def test_transfer_request_needs_the_customers_own_valid_credentials(self):
        self.enroll()
        r = self.ask(username=SERIAL, password="Wrong#Pass1", fp=OTHER_FP)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_a_different_customer_on_a_registered_computer_needs_no_transfer(self):
        self.enroll()
        r = self.ask()                                            # B asking for A's computer
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_transfer_needed"))
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_a_different_computer_for_the_same_customer_still_needs_a_transfer_approval(self):
        self.enroll()
        r, _ = self.enroll(dev=Device(OTHER_FP))
        self.assertEqual((r.status_code, r.json()["code"]), (409, "device_limit"))
        r = self.ask(username=SERIAL, password=PASSWORD, fp=OTHER_FP)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["kind"], r.json()["status"], r.json()["from_user_id"], r.json()["to_user_id"]), ("transfer", "pending", SERIAL, SERIAL))
        self.assertEqual(self.ask(username=SERIAL, password=PASSWORD, fp=OTHER_FP).json()["id"], r.json()["id"])   # idempotent
        self.assertEqual(DeviceTransferRequest.objects.count(), 1)

    # ---- historical cross-customer requests stay decidable ---------------------------------------------------------------
    def legacy_request(self):
        _, a = self.enroll()
        reg = DeviceRegistration.objects.get()
        return a, reg, DeviceTransferRequest.objects.create(project=PRN, device=reg, from_customer=self.customer, to_customer=self.customer_b)

    def test_only_a_super_admin_can_list_and_decide(self):
        _, _, req = self.legacy_request()
        admin = APIClient()
        admin.post("/api/auth/login/", {"username": "Admin", "password": "Admin"}, format="json")
        for client in (admin, APIClient()):
            self.assertIn(client.get(f"/api/projects/{PRN}/device-transfer-requests/").status_code, (401, 403))
            self.assertIn(client.post(f"/api/projects/{PRN}/device-transfer-requests/{req.pk}/approve/", {}, format="json").status_code, (401, 403))
        self.assertEqual(self.staff.get(f"/api/projects/{PRN}/device-transfer-requests/?status=pending").json()["results"][0]["id"], req.pk)
        req.refresh_from_db()
        self.assertEqual(req.status, "PENDING")

    def test_approving_a_historical_request_revokes_the_old_binding_keeps_history_and_audits(self):
        a, reg, req = self.legacy_request()
        r = self.staff.post(f"/api/projects/{PRN}/device-transfer-requests/{req.pk}/approve/", {"reason": "Shop sold to B"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        reg.refresh_from_db()
        self.assertIsNotNone(reg.revoked_at)                                  # revoked, NOT deleted
        self.assertEqual(self.call(a, "POST", "entitlement/", {"fingerprint": FP}).json()["code"], "device_revoked")
        actions = set(LicenseAuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"DEVICE_REVOKED", "DEVICE_TRANSFER_APPROVED"} <= actions)
        self.assertEqual(self.staff.post(f"/api/projects/{PRN}/device-transfer-requests/{req.pk}/approve/", {}, format="json").status_code, 409)

    def test_a_licence_that_is_not_usable_cannot_ask(self):
        self.enroll()
        type(self.customer).objects.filter(pk=self.customer.pk).update(status="INACTIVE")
        r = self.ask(username=SERIAL, password=PASSWORD, fp=OTHER_FP)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertFalse(DeviceTransferRequest.objects.exists())

    def test_the_drawer_shows_pending_requests_to_the_super_admin(self):
        self.enroll()
        self.ask(username=SERIAL, password=PASSWORD, fp=OTHER_FP)
        info = self.staff.get(f"/api/projects/{PRN}/customers/{self.customer.pk}/device/").json()
        self.assertEqual(len(info["transfer_requests"]), 1)
        self.assertNotIn("fingerprint", str(info))
