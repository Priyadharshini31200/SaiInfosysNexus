from datetime import datetime, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.models import UserModulePermission
from apps.accounts.services import ensure_account

from . import pull, scheduler, services as s, tally, usage_reports
from .models import (IntegrationProject, IntegrationApiConfig, IntegrationCustomer, IntegrationLicense, IntegrationRenewal, IntegrationSession, IntegrationSyncCheckpoint,
                     IntegrationTallyInfo, IntegrationTallyPending, IntegrationUsageEvent)
from .tests import ADM, REG, Base

GOOD = {"serial": "756123456", "edition": "Gold", "tss_active": True, "license_admin": "Ravi", "tss_expiry": "2099-03-31", "virtual_user_limit": 5, "gateway_server": "srv01"}


class TallyVerificationTests(Base):
    def setUp(self):
        super().setUp()
        self.project.tally_serial_required = True
        self.project.save()

    def reg(self, tally_data=None, **over):
        body = {**REG, **over}
        if tally_data is not None:
            body["tally"] = tally_data
        return self.call("customers/register", body)

    def test_missing_tally_data_is_pending_with_no_licence_or_serial(self):
        r = self.reg()
        self.assertEqual(r.status_code, 202)
        d = r.data["data"]
        self.assertEqual((d["status"], d["title"], d["license_issued"]), ("PENDING_TALLY_VERIFICATION", "Tally License Details Required", False))
        self.assertFalse(IntegrationCustomer.objects.filter(project=self.project).exists())
        self.assertFalse(IntegrationLicense.objects.exists())
        self.assertEqual(IntegrationTallyPending.objects.get().external_customer_id, "MOTHYS-1001")
        self.assertTrue(self.project.tally_serial_required)  # never silently converted to Non-Tally

    def test_each_mandatory_field_is_enforced(self):
        for drop in ("serial", "edition", "tss_active", "license_admin"):
            bad = {k: v for k, v in GOOD.items() if k != drop}
            self.assertEqual(self.reg(bad).status_code, 202, drop)
        self.assertFalse(IntegrationCustomer.objects.exists())
        self.assertEqual(self.reg({**GOOD, "serial": "12AB"}).status_code, 202)
        self.assertEqual(self.reg({**GOOD, "tss_expiry": "2001-01-01"}).status_code, 202)  # TSS expired
        self.assertEqual(self.reg({**GOOD, "customer_email": "someone@else.com"}).status_code, 202)  # belongs to another customer

    def test_valid_tally_uses_tally_serial_as_user_id_and_stores_details(self):
        r = self.reg(GOOD)
        self.assertEqual(r.status_code, 201)
        d = r.data["data"]
        self.assertEqual((d["user_id"], d["serial_number"]), ("756123456", "756123456"))
        c = IntegrationCustomer.objects.get()
        info = IntegrationTallyInfo.objects.get(customer=c)
        self.assertEqual((info.edition, info.license_admin, info.virtual_user_limit, info.gateway_server), ("Gold", "Ravi", 5, "srv01"))
        self.assertFalse(IntegrationTallyPending.objects.exists())

    def test_retry_verifies_a_pending_customer_and_duplicate_serial_is_refused(self):
        self.assertEqual(self.reg().status_code, 202)
        pending = IntegrationTallyPending.objects.get()
        r = self.staff.post(f"{ADM}projects/MOTHYS/tally-pending/{pending.pk}/retry/")
        self.assertEqual(r.status_code, 409)  # no Tally API configured: stays pending, nothing issued
        self.assertTrue(IntegrationTallyPending.objects.exists())
        with mock.patch("apps.integrations.pull.fetch_tally", return_value=GOOD):
            ok = self.staff.post(f"{ADM}projects/MOTHYS/tally-pending/{pending.pk}/retry/")
        self.assertEqual((ok.status_code, ok.data["verified"]), (200, True))
        self.assertEqual(IntegrationCustomer.objects.get().user_id, "756123456")
        self.assertFalse(IntegrationTallyPending.objects.exists())
        dup = self.reg(GOOD, external_customer_id="MOTHYS-2002", email="two@example.com")
        self.assertEqual(dup.status_code, 202)  # serial already registered -> not issued

    def test_non_tally_project_is_unchanged(self):
        self.project.tally_serial_required = False
        self.project.save()
        r = self.reg()
        self.assertEqual(r.status_code, 201)
        self.assertNotEqual(r.data["data"]["user_id"], "756123456")
        self.assertFalse(IntegrationTallyInfo.objects.exists())

    def test_requirement_change_is_superadmin_only_and_audited(self):
        from .models import IntegrationAudit
        r = self.staff.patch(f"{ADM}projects/MOTHYS/", {"tally_serial_required": "false"}, format="json")  # a form posts text
        self.assertEqual(r.status_code, 200)
        self.project.refresh_from_db()
        self.assertFalse(self.project.tally_serial_required)
        self.assertTrue(IntegrationAudit.objects.filter(action="project.tally_required_changed").exists())
        admin, _ = ensure_account("iadmin", Role.ADMIN, "pw")
        c = APIClient(); c.force_authenticate(admin)
        self.assertEqual(c.patch(f"{ADM}projects/MOTHYS/", {"tally_serial_required": True}, format="json").status_code, 403)

    def test_pull_sync_saves_pending_and_verified_tally_customers(self):
        resp = [{"id": "A1", "name": "Alpha", "email": "a@example.com", "phone": "9876543210", "ts": "756111111", "ed": "Silver", "tss": True, "adm": "Raj"},
                {"id": "A2", "name": "Beta", "email": "b@example.com", "phone": "9876543211"}]
        IntegrationApiConfig.objects.create(
            project=self.project, customer_path="/c", field_mapping=__import__("json").dumps({
                "external_customer_id": "id", "customer_name": "name", "email": "email", "contact_number": "phone",
                "tally_serial": "ts", "tally_edition": "ed", "tally_tss_active": "tss", "tally_license_admin": "adm"}),
            pagination='{"type": "none", "results_path": ""}')
        with mock.patch("apps.integrations.pull._get", return_value=resp), mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            stats = pull.pull_sync(self.project)
        self.assertEqual((stats["created"], stats["pending_tally"]), (1, 1))
        self.assertEqual(IntegrationCustomer.objects.get().user_id, "756111111")
        self.assertEqual(IntegrationTallyPending.objects.get().external_customer_id, "A2")


class CalendarLicenceTests(Base):
    def test_leap_day_rule(self):
        d = timezone.make_aware(datetime(2024, 2, 29, 10, 0))
        self.assertEqual(s.add_calendar_years(d, 1).date().isoformat(), "2025-02-28")
        self.assertEqual(s.add_calendar_years(d, 4).date().isoformat(), "2028-02-29")
        self.assertEqual(s.add_calendar_years(timezone.make_aware(datetime(2023, 3, 1)), 2).date().isoformat(), "2025-03-01")

    def test_new_projects_use_calendar_years_and_trial_3(self):
        r = self.staff.post(ADM + "projects/", {"code": "NEWP", "name": "New", "frontend_url": "https://n.example.com", "backend_url": "https://n.example.com/api", "validity_years": 2}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        from .models import IntegrationProject
        p = IntegrationProject.objects.get(code="NEWP")
        self.assertEqual((p.license_policy, p.trial_days, p.validity_years), ("CALENDAR", 3, 2))

    def test_yearly_renewal_calendar_vs_legacy_and_history(self):
        user_id, pw, d = self.make_active()
        lic = IntegrationLicense.objects.get(pk=d["license_id"])
        for policy, years in (("LEGACY", 1), ("CALENDAR", 2)):
            self.project.license_policy, self.project.validity_years = policy, years
            self.project.save()
            lic.refresh_from_db()
            s.set_license_type(lic, "ONE_YEAR", actor="t")
            lic.refresh_from_db()
            start = lic.starts_at
            if policy == "LEGACY":
                self.assertEqual(lic.expires_at - start, timedelta(days=365))
            else:
                self.assertEqual(lic.expires_at, s.add_calendar_years(start, 2))
            before = lic.expires_at
            s.renew_license(lic, actor="t")
            lic.refresh_from_db()
            self.assertEqual(lic.expires_at, s._years_expiry(lic, before))
        actions = list(IntegrationRenewal.objects.filter(license=lic).values_list("action", flat=True))
        self.assertEqual(actions.count("RENEWED"), 2)
        self.assertGreaterEqual(actions.count("GRANTED"), 2)

    def _expired(self):
        user_id, pw, d = self.make_active()
        lic = IntegrationLicense.objects.get(pk=d["license_id"])
        s.set_license_type(lic, "TRIAL", trial_days=2)
        IntegrationLicense.objects.filter(pk=lic.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        return user_id, pw, lic

    def test_lifetime_has_no_expiry(self):
        user_id, pw, d = self.make_active()
        lic = IntegrationLicense.objects.get(pk=d["license_id"])
        s.set_license_type(lic, "LIFETIME")
        lic.refresh_from_db()
        self.assertIsNone(lic.expires_at)
        s.set_license_type(lic, "TRIAL", trial_days=2)
        lic.refresh_from_db()
        self.assertAlmostEqual((lic.expires_at - lic.starts_at).days, 2, delta=1)

    def test_expired_licence_is_denied_everywhere_for_a_new_universal_project(self):
        user_id, pw, lic = self._expired()  # the customer is created while the fixture is still the legacy (immediate-issue) project
        IntegrationProject.objects.filter(pk=self.project.pk).update(license_policy="CALENDAR")  # ...then the project is switched to the UNIVERSE rules
        self.project.refresh_from_db()
        r = self.call("auth/verify", {"user_id": user_id, "password": pw, "device_id": "PC1"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data["error"]["code"], "LICENSE_EXPIRED")
        self.assertNotIn("session_token", str(r.data))
        # activation / device authorisation: denied, no session, no token
        act = self.call("activation/request", {"user_id": user_id, "password": pw})
        self.assertEqual((act.status_code, act.data["error"]["code"]), (403, "USE_DEVICE_AUTHORIZE"))  # UNIVERSE: no customer-facing activation key at all
        self.assertFalse(IntegrationSession.objects.filter(license=lic).exists())
        # an existing session stops being authorised the moment the licence expires
        sess = IntegrationSession.objects.create(license=lic, token_hash=s._sha("nxt_x"), device_id="PC1")
        intro = self.call("sessions/introspect", {"session_token": "nxt_x"})
        self.assertFalse(intro.data["data"]["active"])
        self.assertEqual(intro.data["data"]["reason"], "license_expired")
        val = self.call("licenses/validate", {"user_id": user_id}).data["data"]
        self.assertEqual((val["valid"], val["state"]), (False, "EXPIRED"))
        # authorised again only after an authorised renewal
        s.set_license_type(lic, "ONE_YEAR", actor="admin")
        ok = self.call("auth/verify", {"user_id": user_id, "password": pw})
        self.assertEqual((ok.status_code, ok.data["data"]["authorized"], ok.data["data"]["denial_reason"]), (200, True, None))
        self.assertEqual(sess.status, "ACTIVE")

    def test_legacy_project_keeps_its_contract_and_adds_an_explicit_authorized_flag(self):
        self.project.license_policy = "LEGACY"
        self.project.save()
        user_id, pw, lic = self._expired()
        r = self.call("auth/verify", {"user_id": user_id, "password": pw})
        self.assertEqual(r.status_code, 200)  # unchanged legacy contract (Mothys)
        d = r.data["data"]
        self.assertEqual((d["verified"], d["authorized"], d["denial_reason"], d["license"]["state"]), (True, False, "EXPIRED", "EXPIRED"))
        self.assertEqual(self.call("activation/request", {"user_id": user_id, "password": pw}).status_code, 403)


class RealMothysCompatibilityTests(TestCase):
    """Uses the actual MOTHYS record created by migration 0002 (not a fixture standing in for it)."""

    def test_seeded_mothys_keeps_legacy_rules(self):
        from .models import IntegrationProject
        p = IntegrationProject.objects.get(code="MOTHYS")
        self.assertEqual((p.license_policy, p.trial_days, p.validity_years, p.tally_serial_required), ("LEGACY", 3, 1, False))

    def test_seeded_mothys_still_grants_and_renews_with_the_365_day_rule(self):
        from .models import IntegrationProject
        p = IntegrationProject.objects.get(code="MOTHYS")
        c = IntegrationCustomer.objects.create(project=p, external_customer_id="E1", name="Legacy Co", email="l@example.com", contact_number="9876543210",
                                               serial_number="900000001", user_id="900000001", password_hash="x")
        lic = IntegrationLicense.objects.create(customer=c, license_type="TRIAL", status="PENDING_APPROVAL")
        s.grant(lic, "ONE_YEAR")
        self.assertEqual(lic.expires_at - lic.starts_at, timedelta(days=365))  # NOT calendar years
        before = lic.expires_at
        s.renew_license(lic)
        lic.refresh_from_db()
        self.assertEqual(lic.expires_at - before, timedelta(days=365))


class SchedulerTests(Base):
    CFG = dict(customer_path="/c", field_mapping='{"external_customer_id":"id","customer_name":"name","email":"email","contact_number":"phone"}',
               pagination='{"type":"none","results_path":""}', sync_interval_minutes=30)
    ROW = {"id": "S1", "name": "Sched One", "email": "s1@example.com", "phone": "9876543210"}

    def setUp(self):
        super().setUp()
        IntegrationApiConfig.objects.create(project=self.project, **self.CFG)
        patcher = mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_due_project_syncs_automatically_then_waits_for_its_interval(self):
        with mock.patch("apps.integrations.pull._get", return_value=[self.ROW]) as get:
            first = scheduler.run_due()
            again = scheduler.run_due()
        self.assertEqual([(c, st) for c, st, _ in first], [("MOTHYS", "OK")])
        self.assertEqual(again, [])  # next run is 30 minutes away
        self.assertEqual(get.call_count, 1)
        cp = IntegrationSyncCheckpoint.objects.get()
        self.assertEqual((cp.status, cp.consecutive_failures), ("OK", 0))
        self.assertIsNotNone(cp.last_success_at)
        self.assertAlmostEqual((cp.next_run_at - cp.last_success_at).total_seconds(), 1800, delta=5)
        later = scheduler.run_due(now=timezone.now() + timedelta(minutes=31))
        self.assertEqual(len(later), 1)
        self.assertEqual(IntegrationCustomer.objects.filter(project=self.project).count(), 1)  # no duplicates on repeat

    def test_failure_is_logged_and_retried_with_backoff(self):
        boom = pull.IntegrationError("EXTERNAL_API_UNAVAILABLE", "down", 502)
        with mock.patch("apps.integrations.pull._get", side_effect=boom):
            out = scheduler.run_due()
        self.assertEqual(out[0][1], "FAILED")
        cp = IntegrationSyncCheckpoint.objects.get()
        self.assertEqual((cp.status, cp.consecutive_failures), ("FAILED", 1))
        self.assertAlmostEqual((cp.next_run_at - timezone.now()).total_seconds(), 300, delta=10)  # first retry in 5 minutes
        self.assertEqual(scheduler.run_due(), [])  # not retried before it is due
        with mock.patch("apps.integrations.pull._get", return_value=[self.ROW]):
            ok = scheduler.run_due(now=timezone.now() + timedelta(minutes=6))
        self.assertEqual(ok[0][1], "OK")
        self.assertEqual(IntegrationSyncCheckpoint.objects.get().consecutive_failures, 0)

    def test_second_worker_cannot_sync_the_same_project_at_once(self):
        cp = IntegrationSyncCheckpoint.objects.create(project=self.project, status="RUNNING", last_started_at=timezone.now())
        with self.assertRaises(pull.IntegrationError) as ctx:
            pull.pull_sync(self.project)
        self.assertEqual(ctx.exception.code, "SYNC_IN_PROGRESS")
        IntegrationSyncCheckpoint.objects.filter(pk=cp.pk).update(last_started_at=timezone.now() - timedelta(minutes=20))  # crashed run
        with mock.patch("apps.integrations.pull._get", return_value=[]):
            self.assertEqual(pull.pull_sync(self.project)["failed"], 0)

    def test_scheduler_is_off_in_tests_and_one_off_commands(self):
        self.assertFalse(scheduler.should_start())


class UsageReportTests(Base):
    def setUp(self):
        super().setUp()
        self.user_id, self.pw, d = self.make_active()
        self.lic = IntegrationLicense.objects.get(pk=d["license_id"])
        self.customer_id = d["customer_id"]
        self.t0 = timezone.now().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
        self.n = 0

    def ev(self, minutes, kind, device="PC1"):
        self.n += 1
        IntegrationUsageEvent.objects.create(project=self.project, license=self.lic, device_id=device, event_type=kind, event_id=f"e{self.n}", occurred_at=self.t0 + timedelta(minutes=minutes))

    def report(self, **q):
        return usage_reports.usage_report(self.project, q)

    def test_durations_come_from_heartbeats_and_idle_markers_only(self):
        self.ev(0, "APP_OPEN"); self.ev(0, "LOGIN")
        for m in (2, 4, 6):
            self.ev(m, "HEARTBEAT")            # 6 active minutes
        self.ev(7, "IDLE_START")
        self.ev(17, "IDLE_END")                # 10 idle minutes
        self.ev(19, "HEARTBEAT")               # 2 active
        self.ev(40, "HEARTBEAT")               # 21 minute gap > 5: unknown, NOT counted
        self.ev(41, "LOGOUT")
        c = self.report()["customers"][0]
        self.assertEqual((c["opens"], c["logins"], c["duration_available"]), (1, 1, True))
        self.assertEqual(c["idle_minutes"], 10.0)
        self.assertEqual(c["active_minutes"], 10.0)  # 0-6 (6) + 6-7 before idle (1) + 17-19 (2) + 40-41 (1); the 21-minute gap is not claimed

    def test_no_heartbeat_means_no_duration_claim(self):
        self.ev(0, "APP_OPEN"); self.ev(1, "LOGIN"); self.ev(30, "LOGIN")
        r = self.report()
        c = r["customers"][0]
        self.assertEqual((c["opens"], c["logins"], c["active_minutes"], c["idle_minutes"], c["duration_available"]), (1, 2, None, None, False))
        self.assertIsNone(r["daily"][0]["active_minutes"])

    def test_filters_and_staff_permissions(self):
        self.ev(0, "APP_OPEN"); self.ev(1, "HEARTBEAT")
        far = (self.t0 - timedelta(days=30)).date().isoformat()
        self.assertEqual(self.report(to=far)["customers"], [])
        self.assertEqual(len(self.report(customer=str(self.customer_id))["customers"]), 1)
        self.assertEqual(self.report(customer="999999")["customers"], [])
        url = f"{ADM}projects/MOTHYS/usage-report/"
        self.assertEqual(self.staff.get(url).status_code, 200)
        admin, _ = ensure_account("rep-admin", Role.ADMIN, "pw")
        c = APIClient(); c.force_authenticate(admin)
        self.assertEqual(c.get(url).status_code, 403)
        UserModulePermission.objects.create(user=admin, module="MOTHYS:usage", can_reports=True)
        self.assertEqual(c.get(url).status_code, 200)
        self.assertEqual(c.get(f"{ADM}projects/MOTHYS/device-transfers/").status_code, 200)
        self.assertEqual(c.get(f"{ADM}projects/MOTHYS/renewals/").status_code, 403)  # not part of the usage grant

    def test_device_transfer_history_lists_from_and_to_devices(self):
        old = IntegrationSession.objects.create(license=self.lic, token_hash="a" * 64, device_id="PC-OLD", device_name="Office PC", status="REVOKED",
                                                revoked_reason="device_transfer", revoked_at=self.t0 + timedelta(minutes=10))
        new = IntegrationSession.objects.create(license=self.lic, token_hash="b" * 64, device_id="PC-NEW", device_name="Laptop")
        IntegrationUsageEvent.objects.create(project=self.project, license=self.lic, session=new, device_id="PC-NEW", event_type="DEVICE_TRANSFER",
                                             event_id="t1", occurred_at=self.t0 + timedelta(minutes=10))
        rows = usage_reports.device_transfer_history(self.project, {})["results"]
        self.assertEqual((rows[0]["from_device"], rows[0]["to_device"]), ("Office PC", "Laptop"))
        self.assertEqual(old.status, "REVOKED")
