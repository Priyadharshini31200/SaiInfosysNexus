from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.registrations.test_portal import make_customer

from . import usage

User = get_user_model()

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=usage.IST)  # "current month" = October 2026, IST

GSTR_DDL = [
    "CREATE TABLE licensed_device_tbl (id bigint PRIMARY KEY, license_id bigint, device_fingerprint nvarchar(128), device_name nvarchar(255), status nvarchar(20))",
    "CREATE TABLE product_license_tbl (id bigint PRIMARY KEY, customer_id int, licensed_gstin nvarchar(30), licensed_tally_serial nvarchar(128))",
    "CREATE TABLE superadmin_customerprofile (id bigint PRIMARY KEY, user_id int, business_name nvarchar(255))",
    "CREATE TABLE companydetails_tbl (id bigint PRIMARY KEY, gstin nvarchar(30), company_name nvarchar(255))",
    "CREATE TABLE local_tally_agent_tbl (id bigint PRIMARY KEY, device_id bigint)",
    "CREATE TABLE gst_import_batch_tbl (id bigint PRIMARY KEY, product_license_id bigint)",
    "CREATE TABLE tally_import_job_tbl (id bigint PRIMARY KEY, status nvarchar(20), local_agent_id bigint, batch_id bigint, started_at datetimeoffset, completed_at datetimeoffset)",
]
PRN_DDL = [
    "CREATE TABLE Customers (id bigint PRIMARY KEY, name nvarchar(120))",
    "CREATE TABLE Licenses (id bigint PRIMARY KEY, customer_id bigint, serial_number nvarchar(9), status nvarchar(24), activated_device_name nvarchar(128), activated_at datetimeoffset, last_validated_at datetimeoffset)",
    "CREATE TABLE LicenseActivations (id bigint PRIMARY KEY, license_id bigint, device_name nvarchar(128), last_seen_at datetimeoffset)",
]


def sql(statement, *params):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)


def insert(table, **row):
    columns = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    sql(f"INSERT INTO {table} ({columns}) VALUES ({marks})", *row.values())


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class ProjectUsageTests(TestCase):
    """Both project databases are stood in for by tables created in the test database, so the real SQL runs."""

    def setUp(self):
        cache.clear()
        db = connection.settings_dict["NAME"]
        self.settings_override = override_settings(GSTR_DB_NAME=db, PRN_DB_NAME=db)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        for ddl in GSTR_DDL + PRN_DDL:
            sql(ddl)
        call_command("seed_initial_users", verbosity=0)
        self.client = APIClient(enforce_csrf_checks=True)
        self.seed_gstr()
        self.seed_prn()

    # ---- fixtures
    def seed_gstr(self):
        acme = User.objects.create_user("acme_user", password="x")
        other = User.objects.create_user("other_user", password="x")
        insert("superadmin_customerprofile", id=1, user_id=acme.id, business_name="Acme Traders")
        insert("companydetails_tbl", id=1, gstin="33AAAAA0000A1Z5", company_name="Beta Company")
        for pid, user, gstin, serial in ((1, acme.id, "", "111111111"), (2, other.id, "33AAAAA0000A1Z5", "222222222"), (3, acme.id, "", "333333333"), (4, other.id, "", "444444444")):
            insert("product_license_tbl", id=pid, customer_id=user, licensed_gstin=gstin, licensed_tally_serial=serial)
        # D1 used twice (once last month, once this month), D2 used via batch only (last month),
        # D3 has only a FAILED job, D4 is REPLACED (not registered), D5 never used
        for did, lic, name, status in ((1, 1, "PC-ONE", "ACTIVE"), (2, 2, "PC-TWO", "ACTIVE"), (3, 3, "PC-THREE", "ACTIVE"), (4, 4, "PC-OLD", "REPLACED"), (5, 1, "PC-FIVE", "ACTIVE")):
            insert("licensed_device_tbl", id=did, license_id=lic, device_fingerprint=f"fp{did}", device_name=name, status=status)
        for aid, dev in ((1, 1), (3, 3), (4, 4)):
            insert("local_tally_agent_tbl", id=aid, device_id=dev)
        insert("gst_import_batch_tbl", id=1, product_license_id=2)
        jobs = [
            (1, "COMPLETED", 1, 1, "2026-09-10T05:00:00+00:00"),
            (2, "COMPLETED", 1, 1, "2026-10-14T05:00:00+00:00"),
            (3, "COMPLETED", None, 1, "2026-09-30T18:29:59+00:00"),  # 23:59:59 IST on 30 Sep: last month
            (4, "FAILED", 3, 1, "2026-10-10T05:00:00+00:00"),
            (5, "COMPLETED", 4, 1, "2026-10-12T05:00:00+00:00"),  # replaced device: ignored
        ]
        for jid, status, agent, batch, at in jobs:
            insert("tally_import_job_tbl", id=jid, status=status, local_agent_id=agent, batch_id=batch, started_at=at, completed_at=at)

    def seed_prn(self):
        for cid, name in ((1, "Never Customer"), (2, "Active Customer"), (3, "Last Month Customer"), (4, "Revoked Customer"), (5, "Two Licences")):
            insert("Customers", id=cid, name=name)
        licences = [
            (1, 1, "100000001", "ISSUED", "", None, None),
            (2, 2, "100000002", "ACTIVE", "PRN-PC", "2026-10-02T05:00:00+00:00", None),
            (3, 3, "100000003", "ACTIVE", "", None, "2026-09-20T05:00:00+00:00"),
            (4, 4, "100000004", "REVOKED", "", None, None),
            (5, 5, "100000005", "ACTIVE", "", "2026-10-03T05:00:00+00:00", None),
            (6, 5, "100000006", "ISSUED", "", None, None),
        ]
        for lid, cid, serial, status, device, activated, validated in licences:
            insert("Licenses", id=lid, customer_id=cid, serial_number=serial, status=status, activated_device_name=device, activated_at=activated, last_validated_at=validated)
        insert("LicenseActivations", id=1, license_id=3, device_name="OLD-PC", last_seen_at="2026-08-01T05:00:00+00:00")

    # ---- counts
    def test_gstr_counts(self):
        self.assertEqual(usage.summary("gstr2tally", NOW), {"total": 4, "used": 2, "not_used": 2, "expiring": 0})

    def test_prn_counts(self):
        # licences 1,6 never used; revoked customer appears once (no live licence) and is unused
        self.assertEqual(usage.summary("prn-auto-print", NOW), {"total": 6, "used": 3, "not_used": 3, "expiring": 0})

    def test_lists_match_counts_and_contain_only_their_category(self):
        for code in usage.PROJECTS:
            counts = usage.summary(code, NOW)
            by_cat = {c: usage.listing(code, c, now=NOW, page_size=100) for c in usage.CATEGORIES}
            self.assertEqual(by_cat["expiring"]["total"], counts["expiring"])
            self.assertEqual(by_cat["used"]["total"], counts["used"])
            self.assertEqual(by_cat["not-used"]["total"], counts["not_used"])
            for row in by_cat["not-used"]["results"]:
                self.assertEqual((row["status"], row["last_used"]), ("Not Used", None))
            for row in by_cat["used"]["results"]:
                self.assertEqual(row["status"], "Used")
            # identity is the device/licence, so no unit is ever in both Used and Not Used
            used_units = {(r["customer"], r["device"]) for r in by_cat["used"]["results"]}
            unused_units = {(r["customer"], r["device"]) for r in by_cat["not-used"]["results"]}
            self.assertFalse(used_units & unused_units)
            self.assertEqual(len(used_units), counts["used"])

    def test_each_registered_unit_is_listed_once(self):
        gstr = usage.listing("gstr2tally", "used", now=NOW)["results"]
        self.assertEqual(sorted(r["device"] for r in gstr), ["PC-ONE · 111111111", "PC-TWO · 222222222"])
        self.assertEqual([r["customer"] for r in gstr], ["Acme Traders", "Beta Company"])  # profile name, else company
        prn = usage.listing("prn-auto-print", "used", now=NOW)["results"]
        self.assertEqual(sorted(r["device"] for r in prn), ["100000005", "OLD-PC · 100000003", "PRN-PC · 100000002"])

    def test_month_bounds_use_kolkata_time(self):
        self.assertEqual([d.isoformat() for d in usage.month_bounds(NOW)], ["2026-10-01T00:00:00+05:30", "2026-11-01T00:00:00+05:30"])
        # a UTC clock still on 30 Sep (18:45 UTC = 00:15 IST on 1 Oct) is already in October in Kolkata
        utc_now = datetime(2026, 9, 30, 18, 45, tzinfo=usage.ZoneInfo("UTC"))
        self.assertEqual(usage.month_bounds(utc_now)[0].isoformat(), "2026-10-01T00:00:00+05:30")
        december = usage.month_bounds(datetime(2026, 12, 5, tzinfo=usage.IST))
        self.assertEqual(december[1].isoformat(), "2027-01-01T00:00:00+05:30")

    # ---- Expiring This Month (Nexus license records)
    def test_expiring_this_month_counts_by_expiry_date_and_status(self):
        def at(day, hour=12, minute=0, second=0, month=10):
            return datetime(2026, month, day, hour, minute, second, tzinfo=usage.IST)

        make_customer("gstr2tally", "700000001", "a@x.com", expires_at=at(20))                       # later this month
        make_customer("gstr2tally", "700000002", "b@x.com", expires_at=at(3))                        # already expired this month
        make_customer("gstr2tally", "700000003", "c@x.com", expires_at=at(31, 23, 59, 59))           # last second of October
        make_customer("gstr2tally", "700000004", "d@x.com", expires_at=at(1, 0, 0, 0, month=11))     # first instant of November
        make_customer("gstr2tally", "700000005", "e@x.com", expires_at=at(2, month=11))              # spec example: expires 02/11
        make_customer("gstr2tally", "700000006", "f@x.com", expires_at=at(1, 0, 0, 0))               # first instant of October
        make_customer("gstr2tally", "700000007", "g@x.com", expires_at=at(30, 23, 59, 59, month=9))  # September
        make_customer("gstr2tally", "700000008", "h@x.com", expires_at=at(10), status="TRIAL_CANCELLED")
        make_customer("gstr2tally", "700000009", "i@x.com", expires_at=at(10), status="INACTIVE")
        make_customer("gstr2tally", "700000010", "j@x.com", license_type="LICENSED", expires_at=None)
        make_customer("prn-auto-print", "700000011", "k@x.com", expires_at=at(12), first_activated_at=at(5))  # other project
        make_customer("prn-auto-print", "700000012", "l@x.com", expires_at=at(12))                   # PRN trial no computer activated: not started
        make_customer("prn-auto-print", "700000013", "m@x.com", license_type="LICENSED", expires_at=at(12))  # Lifetime: excluded
        make_customer("prn-auto-print", "700000014", "n@x.com", license_type="ONE_YEAR", expires_at=at(15))  # 1 Year: counted
        self.assertEqual(usage.summary("gstr2tally", NOW)["expiring"], 4)
        self.assertEqual(usage.summary("prn-auto-print", NOW)["expiring"], 2)
        # in November the 02/11 trial counts (and the one at 01/11 00:00); in December neither does
        november = datetime(2026, 11, 20, tzinfo=usage.IST)
        self.assertEqual(usage.summary("gstr2tally", november)["expiring"], 2)
        self.assertEqual(usage.summary("gstr2tally", datetime(2026, 12, 1, tzinfo=usage.IST))["expiring"], 0)
        rows = usage.listing("gstr2tally", "expiring", now=NOW)
        self.assertEqual(rows["total"], 4)
        self.assertEqual([r["device"] for r in rows["results"]], ["700000006", "700000002", "700000001", "700000003"])  # by expiry date
        self.assertNotIn("password", str(rows).lower().replace("must_change_password", "").replace("password_status_label", "").replace("password_status", "").replace("password_changed_at", ""))
        self.assertEqual(usage.listing("gstr2tally", "expiring", search="700000003", now=NOW)["total"], 1)

    def test_last_used_is_latest_event_in_kolkata_time(self):
        row = usage.listing("gstr2tally", "used", now=NOW)["results"][0]
        self.assertEqual(row["last_used"], "2026-10-14T10:30:00+05:30")

    def test_search_and_pagination(self):
        page = usage.listing("prn-auto-print", "not-used", page=1, page_size=2, now=NOW)
        self.assertEqual((page["total"], len(page["results"]), page["results"][0]["sno"]), (3, 2, 1))
        page2 = usage.listing("prn-auto-print", "not-used", page=2, page_size=2, now=NOW)
        self.assertEqual((len(page2["results"]), page2["results"][0]["sno"]), (1, 3))
        found = usage.listing("prn-auto-print", "not-used", search="revoked", now=NOW)
        self.assertEqual([r["customer"] for r in found["results"]], ["Revoked Customer"])
        self.assertEqual(usage.listing("prn-auto-print", "used", search="100000005", now=NOW)["total"], 1)
        self.assertEqual(usage.listing("prn-auto-print", "used", search="%", now=NOW)["total"], 0)  # wildcards are literal
        self.assertEqual(usage.listing("prn-auto-print", "used", search="[", now=NOW)["total"], 0)

    # ---- API
    def _csrf(self):
        self.client.get("/api/auth/csrf/")
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["nexus_csrftoken"].value}

    def login(self, name):
        self.client.post("/api/auth/login/", {"username": name, "password": name}, format="json", **self._csrf())

    def test_api_is_superadmin_only(self):
        urls = ["/api/dashboard/project-usage/", "/api/dashboard/project-usage/gstr2tally/used/"]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 401)
        self.login("Admin")
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_api_summary_and_list(self):
        self.login("Superadmin")
        data = self.client.get("/api/dashboard/project-usage/").json()
        self.assertEqual([p["code"] for p in data["projects"]], ["gstr2tally", "prn-auto-print"])
        gstr = data["projects"][0]
        self.assertTrue(gstr["available"])
        self.assertEqual(gstr["used"] + gstr["not_used"], gstr["total"])
        listing = self.client.get("/api/dashboard/project-usage/prn-auto-print/not-used/?page_size=10&search=customer").json()
        self.assertEqual((listing["page"], listing["page_size"]), (1, 10))
        self.assertEqual(listing["total"], len(listing["results"]))
        self.assertEqual(set(listing["results"][0]), {"sno", "customer", "device", "last_used", "status"})

    def test_api_validation(self):
        self.login("Superadmin")
        base = "/api/dashboard/project-usage/"
        self.assertEqual(self.client.get(base + "nope/used/").status_code, 404)
        self.assertEqual(self.client.get(base + "gstr2tally/everything/").status_code, 404)
        self.assertEqual(self.client.get(base + "gstr2tally/used/?page_size=7").status_code, 400)
        self.assertEqual(self.client.get(base + "gstr2tally/used/?page=x").status_code, 400)

    def test_unavailable_project_is_reported_not_fatal(self):
        self.login("Superadmin")
        with override_settings(PRN_DB_NAME="NoSuchDatabase"):
            data = self.client.get("/api/dashboard/project-usage/").json()
            prn = data["projects"][1]
            self.assertFalse(prn["available"])
            self.assertIn("unavailable", prn["message"])
            self.assertNotIn("used", prn)
            self.assertTrue(data["projects"][0]["available"])  # the other project still loads
            r = self.client.get("/api/dashboard/project-usage/prn-auto-print/used/")
            self.assertEqual(r.status_code, 503)
            self.assertIn("unavailable", r.json()["message"])

    # ---- Usage Report (Reports menu)
    def test_usage_report_rows_match_counts_and_filters(self):
        for code in usage.PROJECTS:
            counts = usage.summary(code, NOW)
            self.assertEqual(len(usage.report_rows(code)), counts["total"])
            self.assertEqual(len(usage.report_rows(code, "used")), counts["used"])
            self.assertEqual(len(usage.report_rows(code, "not-used")), counts["not_used"])
        rows = usage.report_rows("gstr2tally", "used", "acme")
        self.assertEqual([(r["customer"], r["product"], r["status"]) for r in rows], [("Acme Traders", "GSTR2Tally", "Used")])

    def test_usage_report_api_filters_and_permissions(self):
        url = "/api/reports/usage/"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.login("Admin")
        self.assertEqual(self.client.get(url).status_code, 403)  # Admin follows the Reports permission
        self.login("Superadmin")
        everything = self.client.get(url).json()
        self.assertEqual(len(everything["results"]), 10)
        self.assertEqual(everything["unavailable"], [])
        prn_unused = self.client.get(url + "?project=prn-auto-print&status=not-used").json()["results"]
        self.assertEqual(len(prn_unused), 3)
        self.assertEqual({(r["product"], r["status"]) for r in prn_unused}, {("Barcode Auto Print", "Not Used")})
        self.assertEqual(self.client.get(url + "?project=nope").status_code, 400)
        self.assertEqual(self.client.get(url + "?status=weird").status_code, 400)

    def test_usage_report_one_unavailable_project_is_reported(self):
        self.login("Superadmin")
        with override_settings(PRN_DB_NAME="NoSuchDatabase"):
            data = self.client.get("/api/reports/usage/").json()
            self.assertEqual(data["unavailable"], ["Barcode Auto Print"])
            self.assertEqual(len(data["results"]), 4)
            self.assertEqual(self.client.get("/api/reports/usage/?project=prn-auto-print").status_code, 503)

    def test_usage_report_export_uses_the_same_filters(self):
        self.login("Superadmin")
        body = {"fmt": "print", "project": "gstr2tally", "status": "used"}
        r = self.client.post("/api/reports/usage/export/", body, format="json", **self._csrf())
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual(data["columns"], ["S.No", "Customer / Company Name", "Product", "Device / Serial Number", "Last Used Date", "Usage Status"])
        self.assertEqual(len(data["rows"]), 2)
        self.assertIn("Project: GSTR2Tally", data["subtitle"])
        pdf = self.client.post("/api/reports/usage/export/", {**body, "fmt": "pdf"}, format="json", **self._csrf())
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        xlsx = self.client.post("/api/reports/usage/export/", {**body, "fmt": "xlsx"}, format="json", **self._csrf())
        self.assertEqual(xlsx.status_code, 200)


class NexusRecordedUsageTests(TestCase):
    """PRN usage that Nexus records itself (device usage reports) is counted once per customer, next to the PRN project's records."""

    def setUp(self):
        from apps.registrations.models import DeviceRegistration, ProjectCustomer

        self.Device, self.Customer = DeviceRegistration, ProjectCustomer
        db = connection.settings_dict["NAME"]
        override = override_settings(GSTR_DB_NAME=db, PRN_DB_NAME=db)
        override.enable()
        self.addCleanup(override.disable)
        for ddl in PRN_DDL:
            sql(ddl)

    def customer(self, serial, **over):
        return make_customer("prn-auto-print", serial, f"{serial}@example.com", activation_key_hash=(serial * 8)[:64], **over)

    def device(self, customer, used=None, **over):
        return self.Device.objects.create(
            customer=customer, project="prn-auto-print", device_public_key="k", fingerprint_hashes="{}", device_name="PC", last_used_at=used, **over
        )

    def test_used_and_not_used_come_from_nexus_device_usage(self):
        self.device(self.customer("711111111"), used=datetime(2026, 10, 14, 5, 0, tzinfo=usage.IST))
        self.device(self.customer("722222222"))  # enrolled, never used
        self.customer("733333333")  # approved, never activated
        self.assertEqual(usage.summary("prn-auto-print", NOW)["total"], 3)
        counts = usage.summary("prn-auto-print", NOW)
        self.assertEqual((counts["used"], counts["not_used"]), (1, 2))
        rows = usage.report_rows("prn-auto-print", "used")
        self.assertEqual([(r["customer"], r["status"]) for r in rows], [("Ravi Traders", "Used")])
        self.assertIn("711111111", rows[0]["device"])

    def test_cancelled_inactive_and_revoked_are_not_counted_as_active(self):
        cancelled = self.customer("744444444", status="TRIAL_CANCELLED")
        self.device(cancelled, used=datetime(2026, 10, 14, 5, 0, tzinfo=usage.IST))
        self.device(self.customer("755555555", status="INACTIVE"), used=datetime(2026, 10, 14, 5, 0, tzinfo=usage.IST))
        moved = self.customer("766666666")
        self.device(moved, used=datetime(2026, 10, 1, 5, 0, tzinfo=usage.IST), revoked_at=datetime(2026, 10, 2, 5, 0, tzinfo=usage.IST))
        counts = usage.summary("prn-auto-print", NOW)
        self.assertEqual((counts["total"], counts["used"], counts["not_used"]), (1, 0, 1))  # only the transferred-away customer, unused

    def test_a_customer_in_both_places_is_one_unit_with_the_latest_use(self):
        insert("Customers", id=1, name="Linked Customer")
        insert("Licenses", id=1, customer_id=1, serial_number="777777777", status="ACTIVE", activated_device_name="OLD", activated_at="2026-08-01T05:00:00+00:00", last_validated_at=None)
        self.device(self.customer("777777777"), used=datetime(2026, 10, 14, 5, 0, tzinfo=usage.IST))
        insert("Customers", id=2, name="Cancelled Linked")
        insert("Licenses", id=2, customer_id=2, serial_number="788888888", status="ACTIVE", activated_device_name="", activated_at="2026-08-01T05:00:00+00:00", last_validated_at=None)
        self.customer("788888888", status="INACTIVE")
        counts = usage.summary("prn-auto-print", NOW)
        self.assertEqual((counts["total"], counts["used"]), (1, 1))  # linked once; the inactive Nexus licence hides its PRN twin
        self.assertEqual(usage.report_rows("prn-auto-print", "used")[0]["last_used"][:10], "2026-10-14")
