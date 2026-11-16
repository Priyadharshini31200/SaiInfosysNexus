"""Deleting a customer: permission, project scope, cascade of dependants and the audit trail."""
from .models import DeviceRegistration, LicenseAuditLog, ProjectCustomer
from .test_credentials import CredentialsBase
from .test_device import PRN


class DeleteCustomerTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()
        self.customer_id = self.approve("ONE_YEAR").json()["id"]
        self.url = f"/api/projects/{PRN}/customers/{self.customer_id}/"

    def serial(self):
        return ProjectCustomer.objects.filter(pk=self.customer_id).values_list("serial_number", flat=True).first() or "x"

    def test_wrong_serial_confirmation_is_refused(self):
        self.assertEqual(self.s.delete(self.url, {"confirm_serial": "x"}, format="json").status_code, 400)
        self.assertTrue(ProjectCustomer.objects.filter(pk=self.customer_id).exists())

    def test_superadmin_may_delete_test_customer_with_admin_audit_history_which_is_kept(self):
        LicenseAuditLog.objects.create(project=PRN, customer_id=self.customer_id, action="PASSWORD_RESET_BY_ADMIN")
        self.assertEqual(self.s.delete(self.url, {"confirm_serial": self.serial()}, format="json").status_code, 204)
        self.assertTrue(LicenseAuditLog.objects.filter(action="PASSWORD_RESET_BY_ADMIN", customer__isnull=True).exists())

    def test_admin_without_delete_is_refused_and_nothing_is_removed(self):
        self.staff("Admin")
        self.grant("edit", "entry", "view")
        self.assertEqual(self.s.delete(self.url, {"confirm_serial": self.serial()}, format="json").status_code, 403)
        self.assertTrue(ProjectCustomer.objects.filter(pk=self.customer_id).exists())

    def test_delete_removes_customer_keeps_audit_and_is_not_repeatable(self):
        self.assertEqual(self.s.delete(self.url, {"confirm_serial": self.serial()}, format="json").status_code, 204)
        self.assertFalse(ProjectCustomer.objects.filter(pk=self.customer_id).exists())
        self.assertTrue(LicenseAuditLog.objects.filter(action="CUSTOMER_DELETED", customer__isnull=True).exists())
        self.assertEqual(self.s.delete(self.url, {"confirm_serial": self.serial()}, format="json").status_code, 404)

    def test_customer_with_device_history_is_blocked(self):
        c = ProjectCustomer.objects.get(pk=self.customer_id)
        DeviceRegistration.objects.create(customer=c, project=PRN, device_public_key="k", fingerprint_hashes="{}")
        r = self.s.delete(self.url, {"confirm_serial": self.serial()}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertIn("cannot be permanently deleted", r.json()["message"])
        self.assertTrue(ProjectCustomer.objects.filter(pk=self.customer_id).exists())
        self.assertTrue(DeviceRegistration.objects.filter(customer=c).exists())

    def test_inactive_customer_is_blocked_for_a_regular_admin(self):
        self.staff("Admin")
        self.grant("edit", "delete", "entry", "view")
        ProjectCustomer.objects.filter(pk=self.customer_id).update(status="INACTIVE")
        self.assertEqual(self.s.delete(self.url, {"confirm_serial": self.serial()}, format="json").status_code, 409)

    def test_other_project_customer_is_not_deletable_through_this_project(self):
        other = "gstr2tally"
        self.assertEqual(self.s.delete(f"/api/projects/{other}/customers/{self.customer_id}/").status_code, 404)
        self.assertTrue(ProjectCustomer.objects.filter(pk=self.customer_id).exists())
