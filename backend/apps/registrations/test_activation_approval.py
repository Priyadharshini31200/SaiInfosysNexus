"""FIRST ACTIVATION / DEVICE TRANSFER / DEVICE REVOKE requests: a Super Admin decides, nothing is bound or released without it."""
from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone
from django.test import override_settings
from rest_framework.test import APIClient

from .models import DeviceRegistration, DeviceTransferRequest, LicenseAuditLog, ProjectCustomer
from .test_device import E, FP, OTHER_FP, PASSWORD, PRN, SERIAL, Device, DeviceTestBase
from .test_portal import make_customer

B_SERIAL, B_PASSWORD = "712999001", "Other#Pass9"
BASE = f"/api/projects/{PRN}/"


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True, DEVICE_ACTIVATION_APPROVAL_REQUIRED=True)
class ActivationApprovalTests(DeviceTestBase):
    def setUp(self):
        super().setUp()
        call_command("seed_initial_users", verbosity=0)
        self.staff, self.admin = APIClient(), APIClient()
        self.staff.post("/api/auth/login/", {"username": "Superadmin", "password": "Superadmin"}, format="json")
        self.admin.post("/api/auth/login/", {"username": "Admin", "password": "Admin"}, format="json")

    def pending(self, kind=DeviceTransferRequest.FIRST_ACTIVATION):
        return DeviceTransferRequest.objects.filter(kind=kind, status=DeviceTransferRequest.PENDING)

    def decide(self, req, decision, client=None):
        return (client or self.staff).post(f"{BASE}device-transfer-requests/{req.pk}/{decision}/", {}, format="json")

    # ---- first installation ----------------------------------------------------------------------------------------------
    def test_first_installation_waits_as_a_pending_first_activation_and_binds_nothing(self):
        r, _ = self.enroll()
        self.assertEqual((r.status_code, r.json()["code"]), (403, "activation_pending"))
        self.assertEqual(DeviceRegistration.objects.count(), 0)
        self.assertIsNone(self.reload().first_activated_at)
        req = self.pending().get()
        self.assertEqual((req.to_customer_id, req.kind, req.status), (self.customer.pk, "FIRST_ACTIVATION", "PENDING"))
        self.assertNotIn(FP["smbios"], req.fingerprint_hashes)           # only protected hashes are kept
        panel = self.staff.get(f"{BASE}customers/{self.customer.pk}/device/").json()
        self.assertEqual((panel["device_status"], panel["device_status_label"]), ("activation_pending", "Awaiting activation approval"))
        self.assertEqual([q["kind"] for q in panel["transfer_requests"]], ["first_activation"])

    def test_repeated_attempts_never_create_duplicate_pending_requests(self):
        for _ in range(3):
            self.enroll()
        self.assertEqual(self.pending().count(), 1)
        r = self.c.post(E + "activate/", {"serial_number": SERIAL, "activation_key": "x", "device_public_key": Device().public,
                                          "fingerprint": FP, "device_name": "PC", "app_version": "1"}, format="json")
        self.assertEqual(r.status_code, 401)                              # a wrong key creates nothing
        self.assertEqual(DeviceTransferRequest.objects.count(), 1)

    def test_status_endpoint_reports_the_waiting_activation(self):
        self.enroll()
        body = {"username": SERIAL, "password": PASSWORD, "fingerprint": FP}
        r = self.c.post(E + "transfer-request/status/", body, format="json")
        self.assertEqual((r.json()["kind"], r.json()["state"]), ("first_activation", "pending"))

    # ---- approval ----------------------------------------------------------------------------------------------------------
    def test_approval_binds_the_device_starts_the_validity_and_the_customer_then_signs_in(self):
        _, dev = self.enroll()
        req = self.pending().get()
        r = self.decide(req, "approve")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["kind"], r.json()["status"]), ("first_activation", "approved"))
        device = DeviceRegistration.objects.get()
        self.assertEqual((device.customer_id, device.generation, device.device_name, device.revoked_at), (self.customer.pk, 1, "Front desk PC", None))
        customer = self.reload()
        self.assertIsNotNone(customer.first_activated_at)                  # the validity period starts at the approved activation
        self.assertIsNotNone(customer.expires_at)
        panel = self.staff.get(f"{BASE}customers/{customer.pk}/device/").json()
        self.assertEqual(panel["device_status"], "active")
        self.assertEqual(panel["device"]["device_name"], "Front desk PC")
        self.assertIsNotNone(panel["first_activated_at"])
        for action in ("DEVICE_ENROLLED", "DEVICE_ACTIVATION_REQUESTED", "DEVICE_ACTIVATION_APPROVED"):
            self.assertTrue(LicenseAuditLog.objects.filter(action=action, customer=customer).exists(), action)
        # the app's next sign-in is the same computer: it re-registers its key without a second approval and works
        r, dev = self.enroll(dev=Device(FP))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(DeviceRegistration.objects.count(), 1)
        self.assertEqual(self.call(dev, "POST", "login/", {"username": SERIAL, "password": PASSWORD}).status_code, 200)
        self.assertIsNotNone(DeviceRegistration.objects.get().last_used_at)    # Last Used follows real use

    def test_approval_twice_or_after_the_customer_was_bound_elsewhere_is_refused(self):
        self.enroll()
        req = self.pending().get()
        self.assertEqual(self.decide(req, "approve").status_code, 200)
        self.assertEqual(self.decide(req, "approve").status_code, 409)     # already decided
        self.assertEqual(DeviceRegistration.objects.count(), 1)

    def test_a_computer_registered_to_another_customer_is_still_a_first_activation_for_a_new_customer(self):
        b = make_customer(PRN, B_SERIAL, "other@example.com", password=B_PASSWORD, must_change_password=False)
        with override_settings(DEVICE_ACTIVATION_APPROVAL_REQUIRED=False):
            self.enroll(dev=Device(FP), username=B_SERIAL, password=B_PASSWORD)    # the laptop already belongs to B
        r, _ = self.enroll(dev=Device(FP))                                 # A on the same laptop: a FIRST ACTIVATION, not a reassignment
        self.assertEqual(r.json()["code"], "activation_pending")
        req = self.pending().get()
        self.assertEqual((req.kind, req.device_id, req.from_customer_id), ("FIRST_ACTIVATION", None, None))
        self.assertFalse(self.pending(DeviceTransferRequest.TRANSFER).exists())
        self.assertEqual(self.decide(req, "approve").status_code, 200)
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 2)   # both customers, same laptop
        self.assertEqual(DeviceRegistration.objects.filter(customer=b, revoked_at__isnull=True).count(), 1)   # B untouched

    # ---- rejection -------------------------------------------------------------------------------------------------------
    def test_rejection_binds_nothing_and_the_same_computer_cannot_spam_new_requests(self):
        self.enroll()
        req = self.pending().get()
        self.assertEqual(self.decide(req, "reject").status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, "REJECTED")
        self.assertEqual(DeviceRegistration.objects.count(), 0)
        self.assertIsNone(self.reload().first_activated_at)
        r, _ = self.enroll()
        self.assertEqual((r.status_code, r.json()["code"]), (403, "activation_rejected"))
        self.assertEqual(DeviceTransferRequest.objects.count(), 1)
        r, _ = self.enroll(dev=Device(OTHER_FP))                           # a different computer may ask
        self.assertEqual(r.json()["code"], "activation_pending")
        self.assertEqual(DeviceTransferRequest.objects.count(), 2)

    # ---- authorization -----------------------------------------------------------------------------------------------------
    def test_only_a_super_admin_can_list_or_decide(self):
        self.enroll()
        req = self.pending().get()
        self.assertIn(self.admin.get(f"{BASE}device-transfer-requests/?status=pending").status_code, (401, 403))
        self.assertIn(self.decide(req, "approve", self.admin).status_code, (401, 403))
        self.assertIn(self.decide(req, "approve", APIClient()).status_code, (401, 403))
        listed = self.staff.get(f"{BASE}device-transfer-requests/?status=pending&kind=first_activation").json()["results"]
        self.assertEqual([q["id"] for q in listed], [req.pk])
        self.assertEqual(DeviceRegistration.objects.count(), 0)

    # ---- transfer ----------------------------------------------------------------------------------------------------------
    def a_is_registered(self):
        with override_settings(DEVICE_ACTIVATION_APPROVAL_REQUIRED=False):
            r, dev = self.enroll()
        self.assertEqual(r.status_code, 200)
        return dev

    def test_transfer_is_only_for_a_different_laptop_of_the_same_customer_and_approval_moves_the_binding_atomically(self):
        a = self.a_is_registered()                                          # the customer's licence is active on laptop 1
        new = Device(OTHER_FP)                                              # laptop 2
        r, _ = self.enroll(dev=new)
        self.assertEqual((r.status_code, r.json()["code"]), (409, "device_limit"))
        self.assertFalse(DeviceTransferRequest.objects.exists())
        r = self.c.post(E + "transfer-request/", {"username": SERIAL, "password": PASSWORD, "fingerprint": OTHER_FP, "device_name": "Laptop 2",
                                                  "device_public_key": new.public}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["kind"], "transfer")
        req = self.pending(DeviceTransferRequest.TRANSFER).get()
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 1)   # nothing moved yet
        self.assertEqual(self.decide(req, "approve").status_code, 200)
        active = DeviceRegistration.objects.filter(revoked_at__isnull=True).get()
        self.assertEqual((active.customer_id, active.generation, active.device_name), (self.customer.pk, 2, "Laptop 2"))   # bound in the same step
        self.assertIsNotNone(DeviceRegistration.objects.get(generation=1).revoked_at)           # laptop 1 kept as history, revoked
        self.assertEqual(self.call(a, "POST", "entitlement/", {"fingerprint": FP}).json()["code"], "device_revoked")
        for action in ("DEVICE_REVOKED", "DEVICE_TRANSFER_APPROVED", "DEVICE_ENROLLED"):
            self.assertTrue(LicenseAuditLog.objects.filter(action=action).exists(), action)

    # ---- several customers on one laptop -------------------------------------------------------------------------------------
    def three_customers_on_one_laptop(self):
        users = [(SERIAL, PASSWORD, self.customer)]
        for serial, password, key in (("712999001", "Other#Pass9", "b" * 64), ("712999002", "Third#Pass7", "c" * 64)):
            users.append((serial, password, make_customer(PRN, serial, f"{serial}@example.com", password=password, must_change_password=False,
                                                         activation_key_hash=key)))
        devices = {}
        for serial, password, customer in users:
            dev = Device(FP)                                                # the SAME physical laptop for all three
            r, _ = self.enroll(dev=dev, username=serial, password=password)
            self.assertEqual((r.status_code, r.json()["code"]), (403, "activation_pending"))          # each needs its own approval
            self.assertEqual(self.decide(self.pending().get(to_customer=customer), "approve").status_code, 200)
            r, dev = self.enroll(dev=dev, username=serial, password=password)
            self.assertEqual(r.status_code, 200, r.content)
            devices[serial] = dev
        return users, devices

    def test_customers_a_b_and_c_use_the_same_laptop_after_their_own_approvals(self):
        users, devices = self.three_customers_on_one_laptop()
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 3)
        self.assertEqual(DeviceTransferRequest.objects.filter(kind="FIRST_ACTIVATION", status="APPROVED").count(), 3)
        for serial, password, customer in users:
            r = self.call(devices[serial], "POST", "login/", {"username": serial, "password": password})
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(r.json()["account"]["user_id"], serial)
            self.assertEqual(self.refresh(devices[serial]).status_code, 200)
        # a customer's key never signs another customer in, and reveals nothing about them
        r = self.call(devices[SERIAL], "POST", "login/", {"username": "712999001", "password": "Other#Pass9"})
        self.assertEqual((r.status_code, r.json()["code"]), (401, "device_auth_required"))
        self.assertNotIn("712999001@example.com", r.content.decode())

    def test_unapproved_customer_cannot_log_in_and_a_wrong_password_or_key_is_refused(self):
        self.three_customers_on_one_laptop()
        d = make_customer(PRN, "712999003", "d@example.com", password="Fourth#Pass1", must_change_password=False, activation_key_hash="d" * 64)
        r, _ = self.enroll(dev=Device(FP), username="712999003", password="Fourth#Pass1")
        self.assertEqual(r.json()["code"], "activation_pending")
        self.assertEqual(DeviceRegistration.objects.filter(customer=d).count(), 0)
        r, _ = self.enroll(dev=Device(FP), username="712999003", password="Wrong#Pass1")
        self.assertEqual(r.status_code, 401)
        r = self.c.post(E + "activate/", {"serial_number": "712999003", "activation_key": "WRONG-KEY", "device_public_key": Device().public,
                                          "fingerprint": FP, "device_name": "PC", "app_version": "1"}, format="json")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(DeviceTransferRequest.objects.filter(to_customer=d).count(), 1)   # the pending one only; nothing created by the bad attempts

    def test_revoking_one_customer_leaves_the_other_customers_on_the_same_laptop_working(self):
        users, devices = self.three_customers_on_one_laptop()
        url = f"{BASE}customers/{self.customer.pk}/device/revoke/"
        self.assertEqual(self.staff.post(url, {"confirm": True, "block": True}, format="json").status_code, 200)
        self.assertEqual(self.refresh(devices[SERIAL]).json()["code"], "device_revoked")
        for serial, password, _ in users[1:]:
            self.assertEqual(self.call(devices[serial], "POST", "login/", {"username": serial, "password": password}).status_code, 200)
        self.assertEqual(DeviceRegistration.objects.filter(revoked_at__isnull=True).count(), 2)

    def test_an_expired_licence_blocks_only_that_customer_on_a_shared_laptop(self):
        users, devices = self.three_customers_on_one_laptop()
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(expires_at=timezone.now() - timedelta(days=1))
        r = self.call(devices[SERIAL], "POST", "login/", {"username": SERIAL, "password": PASSWORD})
        self.assertEqual((r.json()["license"]["authorized"], r.json()["license"]["state"]), (False, "expired"))   # sign-in reports it, the app refuses
        self.assertEqual(self.refresh(devices[SERIAL]).status_code, 403)
        serial, password, _ = users[1]
        r = self.call(devices[serial], "POST", "login/", {"username": serial, "password": password})
        self.assertEqual((r.status_code, r.json()["license"]["authorized"]), (200, True))
        self.assertEqual(self.refresh(devices[serial]).status_code, 200)

    def test_the_same_customer_on_a_second_laptop_still_needs_a_transfer_even_when_others_share_that_laptop(self):
        self.three_customers_on_one_laptop()
        r, _ = self.enroll(dev=Device(OTHER_FP))                            # customer A on laptop 2
        self.assertEqual((r.status_code, r.json()["code"]), (409, "device_limit"))

    # ---- revoke ------------------------------------------------------------------------------------------------------------
    def test_revoke_needs_explicit_confirmation_is_recorded_and_never_approves_anything(self):
        self.a_is_registered()
        b = make_customer(PRN, B_SERIAL, "other@example.com", password=B_PASSWORD, must_change_password=False)
        self.enroll(dev=Device(OTHER_FP), username=B_SERIAL, password=B_PASSWORD)            # B's first activation is pending
        url = f"{BASE}customers/{self.customer.pk}/device/revoke/"
        self.assertEqual(self.staff.post(url, {"reason": "lost"}, format="json").status_code, 400)   # not confirmed: nothing happens
        self.assertIsNone(DeviceRegistration.objects.get(customer=self.customer).revoked_at)
        self.assertEqual(self.staff.post(url, {"reason": "lost", "confirm": True}, format="json").status_code, 200)
        self.assertIsNotNone(DeviceRegistration.objects.get(customer=self.customer).revoked_at)
        revoke = DeviceTransferRequest.objects.get(kind="REVOKE")
        self.assertEqual((revoke.status, revoke.decided_by, revoke.to_customer_id), ("APPROVED", "Superadmin", self.customer.pk))
        self.assertEqual(DeviceRegistration.objects.filter(customer=b).count(), 0)           # B was NOT approved by the revoke
        self.assertEqual(self.pending().count(), 1)
        self.assertEqual(self.decide(revoke, "approve").status_code, 409)                    # a revoke is history, not a request
        self.assertIn(self.admin.post(url, {"confirm": True}, format="json").status_code, (401, 403))

    def test_reactivation_after_a_revoke_needs_no_second_first_activation(self):
        self.a_is_registered()
        url = f"{BASE}customers/{self.customer.pk}/device/revoke/"
        self.assertEqual(self.staff.post(url, {"confirm": True}, format="json").status_code, 200)   # freed, not blocked
        first_activated = self.reload().first_activated_at
        r, dev = self.enroll(dev=Device(OTHER_FP))
        self.assertEqual(r.status_code, 200, r.content)                                     # the Super Admin already decided this licence
        self.assertEqual(DeviceRegistration.objects.get(revoked_at__isnull=True).generation, 2)
        self.assertEqual(self.reload().first_activated_at, first_activated)                 # validity is not restarted
        self.assertFalse(DeviceTransferRequest.objects.filter(kind="FIRST_ACTIVATION").exists())

    def test_existing_customers_with_a_registered_device_are_unaffected_by_the_approval_rule(self):
        self.a_is_registered()
        r, _ = self.enroll(dev=Device(FP))                                                  # reinstall of the same computer
        self.assertEqual(r.status_code, 200)
        self.assertFalse(DeviceTransferRequest.objects.exists())
        self.assertEqual(ProjectCustomer.objects.count(), 1)
