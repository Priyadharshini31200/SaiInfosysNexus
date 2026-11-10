from datetime import timedelta
from io import BytesIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AdminPermission

from .models import ProjectCustomer

REPORT = "/api/reports/license-expiry/"


def make(project, serial, expires_in_days, license_type=ProjectCustomer.TRIAL, status=ProjectCustomer.ACTIVE):
    now = timezone.now()
    return ProjectCustomer.objects.create(
        project=project, name=f"Cust {serial}", company_name=f"Co {serial}", license_type=license_type, user_id=serial,
        password_hash="x", contact_number="9876543210", email=f"{serial}@example.com", address="Chennai", gstin="33AFHPM6103Q1Z8",
        serial_number=serial, status=status, activation_key_hash=f"hash{serial}", activation_key_suffix="ABCD", activated_at=now,
        expires_at=None if expires_in_days is None else now + timedelta(days=expires_in_days),
    )


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class ReportTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_initial_users", verbosity=0)
        self.client = APIClient(enforce_csrf_checks=True)
        self.expired = make("gstr2tally", "711111111", -2)
        self.upcoming = make("prn-auto-print", "722222222", 10, ProjectCustomer.ONE_YEAR)
        self.no_expiry = make("gstr2tally", "733333333", None, ProjectCustomer.LICENSED)
        self.inactive = make("gstr2tally", "744444444", 5, status=ProjectCustomer.INACTIVE)
        self.lifetime_with_a_date = make("gstr2tally", "755555555", 5, ProjectCustomer.LICENSED)  # Lifetime never expires
        self.unstarted_prn_trial = make("prn-auto-print", "766666666", 3)  # no computer has activated it yet: no expiry yet

    def csrf(self):
        self.client.get("/api/auth/csrf/")
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["nexus_csrftoken"].value}

    def login(self, name):
        self.client.post("/api/auth/login/", {"username": name, "password": name}, format="json", **self.csrf())

    def export(self, url, fmt, ids):
        return self.client.post(url, {"fmt": fmt, "ids": ids}, format="json", **self.csrf())

    def test_report_lists_active_licenses_with_an_expiry_across_projects(self):
        self.login("Superadmin")
        rows = self.client.get(REPORT).json()["results"]
        self.assertEqual([r["id"] for r in rows], [self.expired.id, self.upcoming.id])  # soonest first; Lifetime, unstarted, no-expiry and inactive left out
        self.assertEqual([r["expiry_status"] for r in rows], ["Expired", "Upcoming"])
        self.assertEqual({r["product"] for r in rows}, {"GSTR2Tally", "Barcode Auto Print"})

    def test_report_needs_the_reports_permission(self):
        self.assertEqual(self.client.get(REPORT).status_code, 401)
        self.login("Admin")
        self.assertEqual(self.client.get(REPORT).status_code, 403)
        AdminPermission.objects.create(name="Reports Access", action="reports", enabled=True)
        self.assertEqual(self.client.get(REPORT).status_code, 200)

    def test_report_export_files(self):
        self.login("Superadmin")
        ids = [self.upcoming.id, self.expired.id, 999999]  # the order sent is kept; unknown ids are ignored
        r = self.export(REPORT + "export/", "xlsx", ids)
        self.assertEqual(r.status_code, 200)
        from openpyxl import load_workbook

        sheet = load_workbook(BytesIO(r.content)).active
        self.assertEqual([c.value for c in sheet[4]][:4], ["S.No", "Name", "Company Name", "Product"])
        self.assertEqual(sheet["B5"].value, self.upcoming.name)
        self.assertEqual(sheet.max_row, 6)
        pdf = self.export(REPORT + "export/", "pdf", ids)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        printed = self.export(REPORT + "export/", "print", ids).json()
        self.assertEqual(printed["columns"][-1], "Status")
        self.assertEqual(len(printed["rows"]), 2)

    def test_report_export_never_includes_records_outside_the_report(self):
        self.login("Superadmin")
        rows = self.export(REPORT + "export/", "print", [self.inactive.id, self.no_expiry.id]).json()["rows"]
        self.assertEqual(rows, [])

    def test_report_export_needs_reports_and_export_permissions(self):
        self.login("Admin")
        AdminPermission.objects.create(name="Reports Access", action="reports", enabled=True)
        self.assertEqual(self.export(REPORT + "export/", "pdf", [self.expired.id]).status_code, 403)
        AdminPermission.objects.create(name="Export / Print Access", action="export_print", enabled=True)
        self.assertEqual(self.export(REPORT + "export/", "pdf", [self.expired.id]).status_code, 200)

    def test_customer_export_is_per_project_and_permission_checked(self):
        url = "/api/projects/gstr2tally/customers/export/"
        self.login("Admin")
        self.assertEqual(self.export(url, "xlsx", [self.expired.id]).status_code, 403)
        AdminPermission.objects.create(name="Export / Print Access", action="export_print", enabled=True)
        AdminPermission.objects.create(name="View Access", action="view", enabled=False)
        self.assertEqual(self.export(url, "xlsx", [self.expired.id]).status_code, 403)  # needs View as the list does
        AdminPermission.objects.filter(action="view").update(enabled=True)
        r = self.export(url, "print", [self.expired.id, self.upcoming.id])  # the PRN customer belongs to another project
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data["rows"]), 1)
        self.assertIn("Email", data["columns"])
        self.assertNotIn("Password", data["columns"])
        self.assertEqual(self.export(url, "csv", []).status_code, 400)
        self.assertEqual(self.export("/api/projects/nope/customers/export/", "pdf", []).status_code, 404)
