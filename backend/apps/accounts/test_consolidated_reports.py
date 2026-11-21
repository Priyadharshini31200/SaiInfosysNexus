from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import StaffReportProject, UserProfile
from apps.tallysl.models import TallyCustomer, TallyInfo, TallyLicense, TallyProduct


class ConsolidatedReportsTests(TestCase):
    url = "/api/reports/consolidated/"

    def account(self, name, role):
        user = get_user_model().objects.create_user(name)
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def setUp(self):
        self.product = TallyProduct.objects.get(code="gstr2tally")
        self.customer = TallyCustomer.objects.create(product=self.product, name="Sample Customer", tally_serial="123456789", email="sample@example.invalid", password_hash="unused")
        TallyLicense.objects.update_or_create(customer=self.customer, defaults={"status": "ACTIVE", "activated_at": datetime.fromisoformat("2026-11-01T00:00:00+05:30"), "expires_at": datetime.fromisoformat("2026-11-30T23:59:59+05:30")})
        TallyInfo.objects.create(customer=self.customer, edition="Gold", tss_expiry_date="2026-12-01", license_administrator="Sample Admin")
        self.clock = patch("django.utils.timezone.now", return_value=datetime.fromisoformat("2026-11-20T12:00:00+05:30"))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_tss_and_license_dates_remain_distinct_across_month_boundary(self):
        _, client = self.account("reports-super", "SUPERADMIN")
        filters = {"project": "gstr2tally"}
        expiry = client.get(self.url, filters).json()
        self.assertEqual(expiry["total"], 1)
        self.assertEqual(expiry["summary"]["license_month"], 1)
        self.assertEqual(expiry["summary"]["tss_month"], 0)
        self.assertEqual(expiry["results"][0]["expiry"], "2026-11-30")
        self.assertIsNone(expiry["results"][0]["purchase_date"])
        tally = client.get(self.url, {**filters, "report": "tally", "filter_by": "tss_expiry", "period": "next_month"}).json()
        self.assertEqual(tally["results"][0]["tss_expiry"], "2026-12-01")

    def test_restricted_user_only_reads_assigned_month_and_cannot_export(self):
        user, client = self.account("reports-viewer", "USER")
        StaffReportProject.objects.create(user=user, project_code="gstr2tally")
        self.assertEqual(client.get(self.url).data["total"], 1)
        for filters in ({"project": "prn-auto-print"}, {"report": "performance"}, {"period": "next_month"}, {"filter_by": "activation"}):
            self.assertEqual(client.get(self.url, filters).status_code, 403)
        self.assertEqual(client.post(self.url, {"fmt": "xlsx"}, format="json").status_code, 403)
        self.assertEqual(client.get("/api/staff/expiry-report/?month=2026-12").status_code, 403)

    def test_filters_and_performance_use_recorded_activation_and_login_events(self):
        from apps.tallysl.models import TallyUsageEvent

        _, client = self.account("reports-filters", "SUPERADMIN")
        for kind in ("login", "open"):
            TallyUsageEvent.objects.create(customer=self.customer, kind=kind, occurred_at=datetime.fromisoformat("2026-11-20T12:00:00+05:30"), day="2026-11-20")
        filters = {"report": "performance", "filter_by": "activation", "period": "custom", "from": "2026-11-01", "to": "2026-11-01", "search": "sample@example.invalid"}
        row = client.get(self.url, filters).json()["results"][0]
        self.assertEqual(row["login_count"], 1)
        self.assertIsNone(row["active_minutes"])
        self.assertEqual(client.get(self.url, {**filters, "project_type": "non_tally"}).data["total"], 0)
        self.assertEqual(client.get(self.url, {**filters, "search": "123456789", "period": "this_year"}).data["total"], 1)
        self.assertEqual(client.get(self.url, {**filters, "to": "2026-10-31"}).status_code, 400)

    def test_admin_scope_and_export_grants_apply_to_filtered_documents(self):
        from io import BytesIO

        from openpyxl import load_workbook

        from apps.accounts.models import UserModulePermission
        from apps.registrations.models import ProjectCustomer

        user, client = self.account("reports-admin", "ADMIN")
        grant = UserModulePermission.objects.create(user=user, module="prn-auto-print:expiry", can_reports=True)
        ProjectCustomer.objects.create(project="prn-auto-print", user_id="PRN01", name="=Test", license_type="ONE_YEAR",
                                       activated_at=datetime.fromisoformat("2026-11-02T00:00:00+05:30"),
                                       expires_at=datetime.fromisoformat("2026-11-25T00:00:00+05:30"), activation_key_hash="0" * 64)
        filters = {"filter_by": "purchase_date", "search": "PRN01"}
        self.assertEqual(client.get(self.url, filters).json()["summary"]["purchased_month"], 1)
        self.assertEqual(client.get(self.url, {"project": "gstr2tally"}).status_code, 403)
        self.assertEqual(client.get(self.url, {"report": "performance"}).status_code, 403)
        self.assertEqual(client.post(self.url, {"fmt": "print", "filters": filters}, format="json").status_code, 403)
        grant.can_export_print = True
        grant.save()
        for fmt in ("print", "pdf", "xlsx"):
            response = client.post(self.url, {"fmt": fmt, "filters": filters}, format="json")
            self.assertEqual(response.status_code, 200)
            if fmt == "print":
                self.assertEqual(len(response.data["rows"]), 1)
            elif fmt == "pdf":
                self.assertTrue(response.content.startswith(b"%PDF"))
            else:
                cell = load_workbook(BytesIO(response.content)).active["A5"]
                self.assertEqual(cell.data_type, "s")
                self.assertEqual(cell.value, "'=Test")


    def test_optional_sort_covers_all_rows_keeps_missing_last_and_default_order(self):
        _, client = self.account("reports-sort", "SUPERADMIN")
        second = TallyCustomer.objects.create(product=self.product, name="Another Customer", tally_serial="223456789", email="b@example.invalid", password_hash="unused")
        TallyLicense.objects.update_or_create(customer=second, defaults={"status": "ACTIVE", "activated_at": datetime.fromisoformat("2026-11-02T00:00:00+05:30"), "expires_at": datetime.fromisoformat("2026-11-25T23:59:59+05:30")})
        base = {"project": "gstr2tally"}
        default = [r["customer"] for r in client.get(self.url, base).json()["results"]]
        self.assertEqual(default, ["Another Customer", "Sample Customer"])  # unchanged: by the filter date (expiry)
        self.assertEqual([r["customer"] for r in client.get(self.url, {**base, "sort": "customer"}).json()["results"]], ["Another Customer", "Sample Customer"])
        self.assertEqual([r["customer"] for r in client.get(self.url, {**base, "sort": "-customer"}).json()["results"]], ["Sample Customer", "Another Customer"])
        tally = client.get(self.url, {**base, "report": "tally", "filter_by": "activation", "sort": "-edition"}).json()["results"]
        self.assertEqual([r["edition"] for r in tally], ["Gold", None])  # a customer without reported Tally details is listed last, never invented
        self.assertEqual(client.get(self.url, {**base, "sort": "password_hash"}).json()["total"], 2)  # unknown sort keys are ignored
