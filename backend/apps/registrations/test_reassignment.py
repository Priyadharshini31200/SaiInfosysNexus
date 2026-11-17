"""Shared computers and reassignment. A computer is no longer exclusive to one customer: a second customer on the same laptop goes to
FIRST ACTIVATION approval of their own, never to a conflict, and nothing of the first customer is released, deleted or reset. A request
that an older Nexus already approved (another customer's computer released to this customer) still registers without a second approval."""
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from .models import DeviceRegistration, DeviceTransferRequest, LicenseAuditLog
from .test_device import E, FP, OTHER_FP, PASSWORD, PRN, SERIAL, Device, DeviceTestBase
from .test_portal import make_customer

B_SERIAL, B_PASSWORD = "712999001", "Other#Pass9"
BASE = f"/api/projects/{PRN}/"


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True, DEVICE_ACTIVATION_APPROVAL_REQUIRED=True)
class SharedComputerTests(DeviceTestBase):
    def setUp(self):
        super().setUp()
        call_command("seed_initial_users", verbosity=0)
        self.customer_b = make_customer(PRN, B_SERIAL, "other@example.com", password=B_PASSWORD, must_change_password=False)
        self.staff = APIClient()
        self.staff.post("/api/auth/login/", {"username": "Superadmin", "password": "Superadmin"}, format="json")
        # customer A's first activation is approved: A owns computer FP
        r, self.dev_a = self.enroll()
        self.assertEqual(r.json()["code"], "activation_pending")
        first = DeviceTransferRequest.objects.get(kind="FIRST_ACTIVATION", status="PENDING")
        ra = self.staff.post(f"{BASE}device-transfer-requests/{first.pk}/approve/", {}, format="json")
        self.assertEqual(ra.status_code, 200, ra.content)
        self.assertEqual(DeviceRegistration.objects.filter(customer=self.customer, revoked_at__isnull=True).count(), 1)

    def ask(self, fp=FP, **over):
        body = {"username": B_SERIAL, "password": B_PASSWORD, "fingerprint": fp, "device_name": "Counter PC", **over}
        return self.c.post(E + "transfer-request/", body, format="json")

    def decide(self, pk, decision):
        return self.staff.post(f"{BASE}device-transfer-requests/{pk}/{decision}/", {"reason": "checked"}, format="json")

    def active(self, customer):
        return DeviceRegistration.objects.filter(customer=customer, revoked_at__isnull=True).count()

    # -- a second customer on the same computer -> first activation, not a conflict ------------------------------------------
    def test_second_customer_on_the_same_computer_goes_to_first_activation_pending(self):
        r, _ = self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "activation_pending"))
        pending = DeviceTransferRequest.objects.get(to_customer=self.customer_b, status="PENDING")
        self.assertEqual(pending.kind, "FIRST_ACTIVATION")
        self.assertEqual((self.active(self.customer), self.active(self.customer_b)), (1, 0))   # A untouched, B not bound before approval

    def test_repeated_attempts_never_duplicate_the_pending_request(self):
        for _ in range(3):
            self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual(DeviceTransferRequest.objects.filter(to_customer=self.customer_b, status="PENDING").count(), 1)

    def test_approving_the_second_customer_registers_them_without_touching_the_first(self):
        self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        req = DeviceTransferRequest.objects.get(to_customer=self.customer_b, status="PENDING")
        self.assertEqual(self.decide(req.pk, "approve").status_code, 200)
        self.assertEqual((self.active(self.customer), self.active(self.customer_b)), (1, 1))
        self.assertEqual(DeviceRegistration.objects.count(), 2)                                 # nothing deleted
        self.assertIn("DEVICE_ACTIVATION_APPROVED", set(LicenseAuditLog.objects.values_list("action", flat=True)))
        self.assertEqual(self.decide(req.pk, "approve").status_code, 409)                        # cannot be decided twice

    def test_rejecting_the_second_customer_binds_nothing_and_keeps_the_first(self):
        self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        req = DeviceTransferRequest.objects.get(to_customer=self.customer_b, status="PENDING")
        self.assertEqual(self.decide(req.pk, "reject").status_code, 200)
        self.assertEqual((self.active(self.customer), self.active(self.customer_b)), (1, 0))
        r, _ = self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "activation_rejected"))

    def test_the_other_customers_key_never_signs_in_this_customer_and_unbinds_nothing(self):
        r, dev_a = self.enroll(dev=self.dev_a, username=SERIAL, password=PASSWORD)
        self.assertEqual(r.status_code, 200, r.content)
        r = self.call(dev_a, "POST", "login/", {"username": B_SERIAL, "password": B_PASSWORD})
        self.assertEqual((r.status_code, r.json()["code"]), (401, "device_auth_required"))     # the app then enrolls B itself
        self.assertEqual(self.active(self.customer), 1)

    def test_a_transfer_is_not_needed_for_a_computer_nobody_else_holds(self):
        r = self.ask(fp=OTHER_FP)
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_transfer_needed"))

    # -- a request an older Nexus already approved -----------------------------------------------------------------------------
    def test_a_legacy_approved_release_of_another_customers_computer_needs_no_second_approval(self):
        a_reg = DeviceRegistration.objects.get(customer=self.customer)
        DeviceRegistration.objects.filter(pk=a_reg.pk).update(revoked_at=a_reg.enrolled_at)
        DeviceTransferRequest.objects.create(project=PRN, kind="TRANSFER", device=a_reg, from_customer=self.customer, to_customer=self.customer_b,
                                             status="APPROVED", requesting_device_name="Counter PC")
        r, _ = self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(DeviceTransferRequest.objects.filter(kind="FIRST_ACTIVATION", to_customer=self.customer_b, status="PENDING").count(), 0)
