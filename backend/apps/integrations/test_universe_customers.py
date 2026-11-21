from datetime import timedelta
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.models import StaffReportProject, UserModulePermission
from apps.accounts.services import ensure_account
from apps.registrations.test_portal import make_customer

from . import universe
from .models import (IntegrationActivationKey, IntegrationCustomer, IntegrationLicense, IntegrationNotification, IntegrationProject, IntegrationRegistration,
                     IntegrationSession, IntegrationUsageEvent)
from .test_universe_lifecycle import ADM, GOOD_TALLY, Lifecycle, MAPPING

RICH = [{"id": "C1", "name": "Alpha Traders", "company": "Alpha Pvt Ltd", "addr": "12 MG Road, Chennai", "email": "alpha@example.com", "phone": "9876543210"},
        {"id": "C2", "name": "Beta Stores", "company": "Beta & Co", "addr": "", "email": "beta@example.com", "phone": "9876543211"}]
RICH_MAP = {**MAPPING, "company_name": "company", "address": "addr"}


class CustomerCentric(Lifecycle):
    def setUp(self):
        super().setUp()
        self.cfg = {**self.cfg, "field_mapping": RICH_MAP}
        self.put_cfg()
        self.sync(RICH)
        self.base = f"{ADM}projects/{self.code}/universe-customers/"

    def reg(self, ext="C1"):
        return IntegrationRegistration.objects.get(project=self.project, external_customer_id=ext)

    def approve(self, kind_id, **body):
        return self.staff.post(f"{self.base}registration/{kind_id}/approve/", {"license_type": "TRIAL", **body}, format="json")

    def admin_client(self, grants, name="adm"):
        user, _ = ensure_account(name, Role.ADMIN, "pw")
        for module, actions in grants.items():
            UserModulePermission.objects.update_or_create(user=user, module=module, defaults={f"can_{a}": True for a in actions})
        c = APIClient()
        c.force_authenticate(user)
        return c


class CustomersListAndPopup(CustomerCentric):
    def test_list_shows_fetched_customers_with_mapped_fields_before_approval(self):
        r = self.staff.get(self.base).data
        self.assertEqual(r["total"], 2)
        a = next(x for x in r["results"] if x["name"] == "Alpha Traders")
        self.assertEqual((a["company_name"], a["email"], a["contact_number"], a["status"], a["kind"], a["project"]), ("Alpha Pvt Ltd", "alpha@example.com", "9876543210", "Pending Review", "registration", "PMP Demo"))
        d = self.staff.get(f"{self.base}registration/{self.reg().pk}/").data["record"]["registration"]
        self.assertEqual((d["address"], d["external_customer_id"], d["source"], d["approval_status"]), ("12 MG Road, Chennai", "C1", "API SYNC", "Pending Review"))
        self.assertEqual(self.staff.get(self.base + "?search=beta").data["total"], 1)
        self.assertEqual(self.staff.get(self.base + "?search=Alpha+Pvt").data["total"], 1)  # company is searchable
        self.assertEqual(self.staff.get(self.base + "?status=Pending+Review").data["total"], 2)
        self.assertEqual(self.staff.get(self.base + "?status=Active").data["total"], 0)

    def test_popup_preview_then_save_and_allow_then_view_mode_never_exposes_secrets(self):
        reg = self.reg()
        pre = self.staff.get(f"{self.base}registration/{reg.pk}/?license_type=ONE_YEAR&years=2").data["record"]
        self.assertEqual(pre["preview"]["license_type"], "ONE_YEAR")
        self.assertIn("9-digit serial starting with 9", pre["account"]["user_id_rule"])
        self.assertIsNone(pre["tally"])  # Non-Tally: the Tally section is absent
        before = IntegrationCustomer.objects.count()
        for _ in range(3):  # opening the popup repeatedly never creates or rotates anything
            self.staff.get(f"{self.base}registration/{reg.pk}/")
        self.assertEqual(IntegrationCustomer.objects.count(), before)
        out = self.approve(reg.pk, license_type="ONE_YEAR", years=2).data
        res, rec = out["result"], out["record"]
        self.assertRegex(res["user_id"], r"^9\d{8}$")
        self.assertEqual(len(res["temporary_password"]), 9)
        self.assertEqual((rec["kind"], rec["registration"]["company_name"], rec["account"]["password_status"].split(" ")[0], rec["account"]["activation_key_status"].split(" ")[0]), ("customer", "Alpha Pvt Ltd", "Setup", "Generated"))
        self.assertEqual((rec["license"]["type_label"], rec["license"]["years"]), ("Yearly", 2))
        again = self.approve(reg.pk).data["result"]
        self.assertEqual((again["already_approved"], again["temporary_password"]), (True, None))
        self.assertEqual(IntegrationCustomer.objects.count(), before + 1)
        cust = IntegrationCustomer.objects.get(user_id=res["user_id"])
        view = self.staff.get(f"{self.base}customer/{cust.pk}/")
        blob = view.content.decode()
        self.assertNotIn(res["temporary_password"], blob)  # never shown again
        self.assertNotIn(cust.password_hash, blob)
        self.assertEqual(IntegrationActivationKey.objects.get(license=cust.license).key_hash in blob, False)
        listed = next(x for x in self.staff.get(self.base).data["results"] if x["kind"] == "customer")
        self.assertEqual((listed["user_id"], listed["license_type"], listed["status"], listed["company_name"]), (res["user_id"], "Yearly", "Pending Activation", "Alpha Pvt Ltd"))

    def test_view_mode_sections_usage_devices_history(self):
        out = self.approve(self.reg().pk, trial_days=5).data
        cust = IntegrationCustomer.objects.get(user_id=out["result"]["user_id"])
        pw = "Own#Pass1"
        self.v1("auth/change-password", {"user_id": cust.user_id, "current_password": out["result"]["temporary_password"], "new_password": pw})
        tok = self.v1("devices/authorize", {"user_id": cust.user_id, "password": pw, "device_id": "A", "device_name": "Office PC"}).data["data"]["session_token"]
        t0 = timezone.now() - timedelta(minutes=20)
        ev = [{"event_type": "APP_OPEN", "session_token": tok, "occurred_at": t0.isoformat(), "event_id": "u1"}, {"event_type": "LOGIN", "session_token": tok, "occurred_at": t0.isoformat(), "event_id": "u2"}]
        ev += [{"event_type": "HEARTBEAT", "session_token": tok, "occurred_at": (t0 + timedelta(minutes=m)).isoformat(), "event_id": f"u-h{m}"} for m in (2, 4, 6)]
        self.v1("usage/events", {"events": ev})
        d = self.staff.get(f"{self.base}customer/{cust.pk}/").data["record"]
        self.assertEqual((d["account"]["password_status"], d["devices"]["current"], d["devices"]["active_session"]), ("Active", "Office PC", True))
        self.assertEqual((d["usage"]["total_logins"], d["usage"]["duration_available"], d["usage"]["category"]), (1, True, "new"))
        self.assertGreaterEqual(d["usage"]["daily_minutes"], 6)
        self.assertEqual((d["license"]["type_label"], d["license"]["trial_days"]), ("Trial", 5))
        self.assertEqual(d["license"]["remaining_days"], 5)  # IST calendar-day difference: a 5-day trial that just started ends 5 days from today
        self.assertTrue(any(h["action"] == "registration.save_and_allow" for h in d["history"]["audit"]))
        self.assertTrue(d["history"]["terms"])

    def test_edit_extend_renew_change_type_suspend_and_reset_password(self):
        out = self.approve(self.reg().pk, trial_days=3).data
        cust = IntegrationCustomer.objects.get(user_id=out["result"]["user_id"])
        url = f"{self.base}customer/{cust.pk}/"
        ed = self.staff.patch(url, {"customer_name": "Alpha Traders Ltd", "company_name": "Alpha Group", "address": "New Address", "contact_number": "9000000009"}, format="json")
        self.assertEqual((ed.status_code, ed.data["record"]["registration"]["company_name"], ed.data["record"]["registration"]["address"]), (200, "Alpha Group", "New Address"))
        self.assertEqual(self.staff.patch(url, {"email": "beta@example.com"}, format="json").status_code, 409)  # another customer's address
        old_exp = IntegrationLicense.objects.get(pk=cust.license.pk).expires_at
        ex = self.staff.post(url + "extend-trial/", {"days": 4}, format="json")
        self.assertEqual(ex.status_code, 200)
        self.assertEqual(IntegrationLicense.objects.get(pk=cust.license.pk).expires_at, old_exp + timedelta(days=4))
        self.assertEqual(self.staff.post(url + "renew/", {}, format="json").status_code, 409)  # a trial is not renewable
        sl = self.staff.post(url + "set-license/", {"license_type": "ONE_YEAR", "years": 3}, format="json")
        self.assertEqual((sl.status_code, sl.data["record"]["license"]["years"]), (200, 3))
        lic = IntegrationLicense.objects.get(pk=cust.license.pk)
        from . import services as s
        self.assertEqual(lic.expires_at, s.add_calendar_years(lic.starts_at, 3))
        self.assertEqual(self.staff.post(url + "renew/", {}, format="json").status_code, 200)
        self.assertEqual(self.staff.post(url + "inactivate/", {}, format="json").data["record"]["account"]["account_status"], "Suspended")
        self.assertEqual(self.staff.post(url + "reactivate/", {}, format="json").data["record"]["account"]["account_status"], "Active")
        pw = out["result"]["temporary_password"]
        reset = self.staff.post(url + "reset-password/", {}, format="json").data
        new_pw = reset["result"]["temporary_password"]
        c2 = IntegrationCustomer.objects.get(pk=cust.pk)
        self.assertTrue(check_password(new_pw, c2.password_hash) and not check_password(pw, c2.password_hash) and c2.must_change_password)
        self.assertNotIn(new_pw, self.staff.get(url).content.decode())
        types = [t["action"] for t in self.staff.get(url).data["record"]["history"]["terms"]]
        self.assertIn("RENEWED", types)

    def test_edit_registration_before_approval_and_hold_reject(self):
        reg = self.reg("C2")
        url = f"{self.base}registration/{reg.pk}/"
        self.assertEqual(self.staff.patch(url, {"company_name": "Beta Holdings", "customer_name": "Beta S"}, format="json").data["record"]["registration"]["company_name"], "Beta Holdings")
        self.assertEqual(self.staff.post(url + "hold/", {}, format="json").data["record"]["registration"]["approval_status"], "On Hold")
        self.assertEqual(self.staff.post(url + "reject/", {}, format="json").data["record"]["registration"]["approval_status"], "Rejected")
        self.assertEqual(self.staff.post(url + "approve/", {"license_type": "TRIAL"}, format="json").status_code, 409)
        self.assertEqual(self.staff.post(url + "extend-trial/", {"days": 2}, format="json").status_code, 400)  # wrong action for a registration


class PermissionsAndIsolation(CustomerCentric):
    def setUp(self):
        super().setUp()
        self.other = IntegrationProject.objects.create(code="OTHER2", name="Other", frontend_url="https://o.example.com", backend_url="https://o.example.com/api", license_policy="CALENDAR")
        self.other_reg = IntegrationRegistration.objects.create(project=self.other, external_customer_id="X1", name="Hidden", email="hidden@example.com", contact_number="9876543299")

    def test_admin_sees_and_does_only_what_was_granted(self):
        view_only = self.admin_client({self.code: ["view"], f"{self.code}:dashboard": ["view"]}, "viewer")
        self.assertEqual(view_only.get(self.base).status_code, 200)
        self.assertEqual(view_only.get(f"{self.base}registration/{self.reg().pk}/").status_code, 200)
        self.assertEqual(view_only.post(f"{self.base}registration/{self.reg().pk}/approve/", {"license_type": "TRIAL"}, format="json").status_code, 403)
        self.assertEqual(view_only.patch(f"{self.base}registration/{self.reg().pk}/", {"company_name": "x"}, format="json").status_code, 403)
        self.assertEqual(view_only.get(f"{ADM}projects/{self.code}/universe-dashboard/").status_code, 200)
        approver = self.admin_client({self.code: ["view", "approve"]}, "approver")
        self.assertEqual(approver.post(f"{self.base}registration/{self.reg().pk}/approve/", {"license_type": "TRIAL"}, format="json").status_code, 200)
        # no cross-project reach, by URL or by id
        self.assertEqual(view_only.get(f"{ADM}projects/OTHER2/universe-customers/").status_code, 403)
        self.assertEqual(view_only.get(f"{ADM}projects/{self.code}/universe-customers/registration/{self.other_reg.pk}/").status_code, 404)
        self.assertEqual(self.staff.get(f"{ADM}projects/{self.code}/universe-customers/registration/{self.other_reg.pk}/").status_code, 404)

    def test_sidebar_registry_is_permission_aware_and_excludes_legacy_products(self):
        names = [p["code"] for p in self.staff.get(ADM + "universe/projects/").data["projects"]]
        self.assertIn(self.code, names)
        self.assertNotIn("MOTHYS", names)  # legacy products never appear in the UNIVERSE registry
        none = self.admin_client({}, "nogrants")
        self.assertEqual(none.get(ADM + "universe/projects/").data["projects"], [])
        some = self.admin_client({f"{self.code}:dashboard": ["view"]}, "somegrants")
        p = some.get(ADM + "universe/projects/").data["projects"]
        self.assertEqual([x["code"] for x in p], [self.code])
        self.assertEqual((p[0]["can"]["dashboard"], p[0]["can"]["customers"], p[0]["can"]["approve"], p[0]["can"]["integration"]), (True, False, False, False))

    def test_restricted_user_and_developer_have_no_universe_access(self):
        for role in (Role.USER, Role.DEVELOPER):
            u, _ = ensure_account(f"r-{role}", role, "pw")
            c = APIClient()
            c.force_authenticate(u)
            for url in (ADM + "universe/projects/", ADM + "universe/cards/", self.base, f"{ADM}projects/{self.code}/universe-dashboard/"):
                self.assertEqual(c.get(url).status_code, 403, f"{role} {url}")


class ProjectDashboard(CustomerCentric):
    def dash(self):
        return self.staff.get(f"{ADM}projects/{self.code}/universe-dashboard/").data

    def test_classification_is_unavailable_without_telemetry_and_real_with_it(self):
        out = self.approve(self.reg().pk, trial_days=30).data["result"]
        d = self.dash()
        cards = {c["key"]: c for c in d["cards"]}
        self.assertFalse(d["telemetry"]["available"])
        self.assertIn("No activity events", d["telemetry"]["reason"])
        for k in ("speed", "normal", "one_time"):
            self.assertEqual((cards[k]["count"], cards[k]["available"]), (None, False))  # Data Unavailable - never a misleading 0
        self.assertEqual((cards["new"]["count"], cards["new"]["available"]), (1, True))
        self.assertEqual(d["stats"], {"total_customers": 2, "last_7_days": 2, "pending_activation": 1, "recent_device_transfers": 0})
        # real telemetry makes the other classes real
        cust = IntegrationCustomer.objects.get(user_id=out["user_id"])
        IntegrationCustomer.objects.filter(pk=cust.pk).update(created_at=timezone.now() - timedelta(days=60))
        IntegrationLicense.objects.filter(pk=cust.license.pk).update(created_at=timezone.now() - timedelta(days=60))
        IntegrationUsageEvent.objects.create(project=self.project, license=cust.license, event_type="LOGIN", event_id="d1", device_id="A", occurred_at=timezone.now() - timedelta(days=2))
        d = self.dash()
        cards = {c["key"]: c for c in d["cards"]}
        self.assertTrue(d["telemetry"]["available"])
        self.assertEqual((cards["one_time"]["count"], cards["speed"]["count"], cards["normal"]["count"], cards["new"]["count"]), (1, 0, 0, 0))
        for day in range(3, 20):  # 17 more distinct active days => Speed (>= 15 days in the last 30)
            IntegrationUsageEvent.objects.create(project=self.project, license=cust.license, event_type="LOGIN", event_id=f"d{day}", device_id="A", occurred_at=timezone.now() - timedelta(days=day))
        cards = {c["key"]: c for c in self.dash()["cards"]}
        self.assertEqual((cards["speed"]["count"], cards["one_time"]["count"]), (1, 0))
        self.assertIn("definitions", self.dash())

    def test_recent_device_transfers_count(self):
        out = self.approve(self.reg().pk).data["result"]
        cust = IntegrationCustomer.objects.get(user_id=out["user_id"])
        IntegrationUsageEvent.objects.create(project=self.project, license=cust.license, event_type="DEVICE_TRANSFER", event_id="t1", device_id="B", occurred_at=timezone.now() - timedelta(days=2))
        IntegrationUsageEvent.objects.create(project=self.project, license=cust.license, event_type="DEVICE_TRANSFER", event_id="t2", device_id="C", occurred_at=timezone.now() - timedelta(days=20))
        self.assertEqual(self.dash()["stats"]["recent_device_transfers"], 1)

    def test_not_a_universe_project(self):
        self.assertEqual(self.staff.get(f"{ADM}projects/MOTHYS/universe-dashboard/").status_code, 404)


class OverallDashboardCards(CustomerCentric):
    def test_only_activated_enabled_universe_projects_get_cards(self):
        cards = self.staff.get(ADM + "universe/cards/").data
        self.assertEqual(cards["cards"], [])  # integration not activated yet
        with mock.patch("apps.integrations.pull._get", return_value=RICH):
            for kind in ("customer", "mapping"):
                self.staff.post(f"{ADM}projects/{self.code}/validate/{kind}/")
        self.assertEqual(self.staff.post(f"{ADM}projects/{self.code}/activate/").status_code, 200)
        data = self.staff.get(ADM + "universe/cards/").data
        c = data["cards"][0]
        self.assertEqual((c["code"], c["label"], c["kind"], c["used"], c["not_used"], c["expired"]), (self.code, "PMP Demo", "universe", 0, 0, 0))
        self.assertEqual(sorted(r["state"] for r in data["rows"]), ["pending", "pending"])  # open registrations feed Pending Activations
        out = self.approve(self.reg().pk, trial_days=5).data["result"]
        data = self.staff.get(ADM + "universe/cards/").data
        self.assertEqual(data["cards"][0]["not_used"], 1)  # approved, never used
        self.assertIn("note", data["cards"][0])  # no telemetry yet: explicit note, not a silent zero
        self.assertEqual(sorted(r["state"] for r in data["rows"]), ["active", "pending"])
        trial = next(r for r in data["rows"] if r["state"] == "active")
        self.assertTrue(trial["trial"])
        IntegrationProject.objects.filter(pk=self.project.pk).update(enabled=False)
        self.assertEqual(self.staff.get(ADM + "universe/cards/").data["cards"], [])  # disabled: card disappears


class ConsolidatedExpiryAndNotifications(CustomerCentric):
    def make_expiring(self, days=3):
        out = self.approve(self.reg().pk, trial_days=5).data["result"]
        cust = IntegrationCustomer.objects.get(user_id=out["user_id"])
        IntegrationLicense.objects.filter(pk=cust.license.pk).update(expires_at=timezone.now() + timedelta(days=days))
        return cust

    def test_report_includes_universe_and_legacy_deduplicated_with_filters(self):
        cust = self.make_expiring(1)
        # a legacy Barcode customer expiring this month
        make_customer("prn-auto-print", "714000001", "prn1@example.com", name="Barcode Co", company_name="Barcode Pvt", license_type="ONE_YEAR", expires_at=timezone.now() + timedelta(hours=1))
        data = self.staff.get("/api/staff/expiry-report/?month=" + (timezone.now() + timedelta(days=1)).astimezone(universe.s.timezone.get_fixed_timezone(330)).strftime("%Y-%m")).data
        projects = {r["project"] for r in data["results"]}
        self.assertIn(self.code, projects)
        row = next(r for r in data["results"] if r["project"] == self.code)
        self.assertEqual((row["customer"], row["company"], row["license_type"], row["status"], row["user_id"]), ("Alpha Traders", "Alpha Pvt Ltd", "Trial", "Active", cust.user_id))
        self.assertIn(row["remaining_days"], (0, 1, 2))
        self.assertEqual(data["total"], len(data["results"]))
        only = self.staff.get(f"/api/staff/expiry-report/?project={self.code}").data
        self.assertEqual({r["project"] for r in only["results"]}, {self.code})
        self.assertEqual(self.staff.get("/api/staff/expiry-report/?search=alpha+pvt").data["total"], 1)
        self.assertEqual(self.staff.get("/api/staff/expiry-report/?license_type=Lifetime").data["total"], 0)
        self.assertEqual(self.staff.get("/api/staff/expiry-report/?status=Expired").data["total"], 0)
        self.assertEqual(self.staff.get("/api/staff/expiry-report/?month=2031-01").data["total"], 0)
        self.assertEqual(self.staff.get("/api/staff/expiry-report/?month=bad").status_code, 400)
        # renewal moves the licence out of this month's report
        self.staff.post(f"{self.base}customer/{cust.pk}/set-license/", {"license_type": "ONE_YEAR", "years": 2}, format="json")
        self.assertEqual(self.staff.get(f"/api/staff/expiry-report/?project={self.code}").data["total"], 0)

    def test_report_access_is_project_scoped_for_admin_and_user(self):
        self.make_expiring(1)
        user, _ = ensure_account("rep-user", Role.USER, "pw")
        StaffReportProject.objects.create(user=user, project_code="prn-auto-print")
        c = APIClient()
        c.force_authenticate(user)
        d = c.get("/api/staff/expiry-report/").data
        self.assertEqual([p["code"] for p in d["projects"]], ["prn-auto-print"])
        self.assertEqual(c.get(f"/api/staff/expiry-report/?project={self.code}").status_code, 400)
        adm = self.admin_client({f"{self.code}:expiry": ["reports"]}, "rep-admin")
        self.assertEqual([p["code"] for p in adm.get("/api/staff/expiry-report/").data["projects"]], [self.code])

    def test_expiry_notifications_are_deduplicated_scoped_and_open_the_customer(self):
        cust = self.make_expiring(3)
        n1 = self.staff.get(ADM + "notifications/").data
        for _ in range(3):  # a repeated job / page poll never repeats a notification
            self.staff.get(ADM + "notifications/")
        self.assertEqual(IntegrationNotification.objects.filter(kind="EXPIRY").count(), 1)
        item = next(n for n in n1["results"] if n["kind"] == "EXPIRY")
        self.assertEqual((item["customer_id"], item["project_code"], item["title"].startswith("License expiring")), (cust.pk, self.code, True))
        self.assertGreaterEqual(n1["unread_count"], 3)  # 2 registrations + 1 expiry: separate from the This Month Expiries count
        self.staff.post(f"{ADM}notifications/{item['id']}/read/")
        self.assertTrue(IntegrationNotification.objects.get(pk=item["id"]).read)
        # renewal then approaching expiry again => a new notification (different expiry date), never a duplicate of the old one
        IntegrationLicense.objects.filter(pk=cust.license.pk).update(expires_at=timezone.now() + timedelta(days=5))
        self.staff.get(ADM + "notifications/")
        self.assertEqual(IntegrationNotification.objects.filter(kind="EXPIRY").count(), 2)
        other = self.admin_client({"UNRELATED": ["view"]}, "scoped")
        self.assertEqual(other.get(ADM + "notifications/").data["results"], [])  # project-scoped
        granted = self.admin_client({self.code: ["view"]}, "scoped2")
        self.assertTrue(any(n["kind"] == "EXPIRY" for n in granted.get(ADM + "notifications/").data["results"]))


class TallyPopup(Lifecycle):
    code = "PMPT"
    tally_required = True

    def test_tally_section_blocks_approval_until_verified_and_user_id_is_the_serial(self):
        self.put_cfg(field_mapping={**MAPPING, "tally_serial": "serial"})
        base = f"{ADM}projects/{self.code}/universe-customers/"
        self.v1("customers/register", {"external_customer_id": "T1", "customer_name": "Tara", "email": "t@example.com", "contact_number": "9876543210", "company_name": "Tara Exports"})
        reg = IntegrationRegistration.objects.get()
        d = self.staff.get(f"{base}registration/{reg.pk}/").data["record"]
        self.assertFalse(d["tally"]["verified"])
        self.assertEqual(d["tally"]["title"], "Tally License Details Required")
        self.assertEqual(sorted(d["tally"]["missing"]), sorted(["Tally Serial Number", "Tally Edition", "Tally Software Services (TSS)", "License Administrator"]))
        self.assertEqual(self.staff.post(f"{base}registration/{reg.pk}/approve/", {"license_type": "TRIAL"}, format="json").status_code, 409)
        self.assertEqual(self.staff.get(base).data["results"][0]["status"], "Pending Tally Verification")
        self.v1("tally/submit", {"external_customer_id": "T1", "tally": GOOD_TALLY})
        d = self.staff.get(f"{base}registration/{reg.pk}/").data["record"]
        self.assertTrue(d["tally"]["verified"] and not d["tally"]["missing"])
        self.assertEqual(d["account"]["user_id"], "Assigned on approval")  # the Nexus User ID is separate from the Tally serial
        out = self.staff.post(f"{base}registration/{reg.pk}/approve/", {"license_type": "ONE_YEAR", "years": 1}, format="json").data
        rec = out["record"]
        self.assertRegex(rec["account"]["user_id"], r"^9\d{8}$")  # new rule: 9-digit Nexus User ID; the Tally serial is kept separately
        self.assertNotEqual(rec["account"]["user_id"], "756123456")
        self.assertEqual((rec["tally"]["edition"], rec["tally"]["license_admin"], rec["tally"]["verified"]), ("Gold", "Ravi", True))
        self.assertEqual(IntegrationCustomer.objects.get(pk=out["result"]["customer_id"]).serial_number, "756123456")
        self.assertIsNotNone(rec["tally"]["verified_at"])


class ProjectHub(CustomerCentric):
    """UNIVERSE -> Projects hub: server-side, permission-filtered, paginated."""

    def setUp(self):
        super().setUp()
        for i in range(1, 46):
            IntegrationProject.objects.create(code=f"H{i:03d}", name=f"Hub Project {i:03d}", frontend_url="https://h.example.com", backend_url="https://h.example.com/api",
                                              license_policy="CALENDAR", tally_serial_required=(i % 3 == 0), status="ACTIVE" if i % 5 == 0 else "SETUP_PENDING", enabled=(i % 9 != 0))
        self.hub = ADM + "universe/hub/"

    def test_pagination_search_type_status_run_on_the_server(self):
        d = self.staff.get(self.hub + "?page_size=20").data
        self.assertEqual((d["total"], len(d["results"]), d["can_add"]), (46, 20, True))  # 45 hub projects + PMP
        self.assertEqual(len(self.staff.get(self.hub + "?page=3&page_size=20").data["results"]), 6)
        self.assertEqual(self.staff.get(self.hub + "?search=H007").data["total"], 1)
        self.assertEqual(self.staff.get(self.hub + "?search=pmp").data["results"][0]["code"], self.code)
        t = self.staff.get(self.hub + "?type=tally").data
        self.assertTrue(t["total"] > 0 and all(r["type"] == "Tally" for r in t["results"]))
        self.assertEqual(self.staff.get(self.hub + "?status=suspended").data["total"], 5)  # i % 9 == 0
        self.assertTrue(all(r["status"] == "Suspended" for r in self.staff.get(self.hub + "?status=suspended").data["results"]))
        self.assertEqual(self.staff.get(self.hub + "?page=abc").status_code, 200)  # bad paging input never errors

    def test_row_shape_customer_count_and_last_sync(self):
        row = next(r for r in self.staff.get(self.hub + "?search=PMP").data["results"] if r["code"] == self.code)
        self.assertEqual((row["customer_count"], row["api_status"]["sync"], row["can"]), (2, "OK", {"open": True, "configure": True}))
        self.assertIsNotNone(row["last_sync_at"])
        self.assertNotIn("MOTHYS", [r["code"] for r in self.staff.get(self.hub + "?page_size=100").data["results"]])  # legacy products are never listed

    def test_admin_list_is_filtered_by_the_api_and_cannot_configure(self):
        a = self.admin_client({self.code: ["view"], f"{self.code}:dashboard": ["view"]}, "hubadmin")
        d = a.get(self.hub + "?page_size=100").data
        self.assertEqual(([r["code"] for r in d["results"]], d["total"], d["can_add"], d["results"][0]["can"]["configure"]), ([self.code], 1, False, False))
        self.assertEqual(a.get(self.hub + "?search=H001").data["total"], 0)  # other projects are not searchable either
        nogrant = self.admin_client({}, "hubnone")
        self.assertEqual(nogrant.get(self.hub).data["results"], [])

    def test_project_detail_not_found_forbidden_ok(self):
        self.assertEqual(self.staff.get(f"{ADM}universe/projects/{self.code}/").status_code, 200)
        self.assertEqual(self.staff.get(f"{ADM}universe/projects/NOSUCH/").status_code, 404)
        self.assertEqual(self.staff.get(f"{ADM}universe/projects/MOTHYS/").status_code, 404)  # legacy product is not a UNIVERSE project
        a = self.admin_client({self.code: ["view"]}, "detailadmin")
        self.assertEqual(a.get(f"{ADM}universe/projects/{self.code}/").status_code, 200)
        self.assertEqual(a.get(f"{ADM}universe/projects/H001/").status_code, 403)
        d = a.get(f"{ADM}universe/projects/{self.code}/").data
        self.assertEqual((d["can"]["customers"], d["can"]["configure"]), (True, False))

    def test_user_and_developer_roles_have_no_hub_access(self):
        for role in (Role.USER, Role.DEVELOPER):
            u, _ = ensure_account(f"hub-{role}", role, "pw")
            c = APIClient()
            c.force_authenticate(u)
            self.assertEqual(c.get(self.hub).status_code, 403)
            self.assertEqual(c.get(f"{ADM}universe/projects/{self.code}/").status_code, 403)


class SimpleTemporaryPassword(CustomerCentric):
    """NEW Universal customers: the admin's temporary password needs only 3+ characters (no complexity rule); it is hashed, emailed once and must be changed."""

    def test_policy_accepts_short_simple_passwords_and_rejects_empty(self):
        for pw in ("123", "123456", "abc", "abc123", "ABC@123"):
            self.assertEqual(universe.check_password(pw), pw)
        for bad in ("", "12", None):
            with self.assertRaises(universe.IntegrationError):
                universe.check_password(bad)

    def test_numeric_password_saved_as_hash_emailed_and_must_change(self):
        from django.core import mail
        mail.outbox.clear()
        self.assertEqual(self.approve(self.reg("C2").pk, password="").status_code, 422)  # explicit empty -> rejected, not silently replaced
        self.assertFalse(IntegrationCustomer.objects.filter(project=self.project).exists())
        self.assertEqual(self.approve(self.reg("C1").pk, password="123").status_code, 200)
        c = IntegrationCustomer.objects.get(project=self.project, external_customer_id="C1")
        self.assertTrue(check_password("123", c.password_hash) and c.password_hash != "123" and c.must_change_password)
        self.assertTrue(any("123" in m.body for m in mail.outbox))
