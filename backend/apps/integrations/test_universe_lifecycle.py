import json
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.services import ensure_account

from . import pull, services as s, universe
from .models import (IntegrationActivationKey, IntegrationApiConfig, IntegrationCustomer, IntegrationLicense, IntegrationNotification, IntegrationProject,
                     IntegrationRegistration, IntegrationSession, IntegrationTallyInfo, IntegrationUsageEvent)

ADM = "/api/integrations/admin/"
V1 = "/api/integrations/v1/"
ROWS = [{"id": "C1", "name": "Alpha Traders", "email": "alpha@example.com", "phone": "9876543210"},
        {"id": "C2", "name": "Beta Stores", "email": "beta@example.com", "phone": "9876543211"}]
MAPPING = {"external_customer_id": "id", "customer_name": "name", "email": "email", "contact_number": "phone"}
GOOD_TALLY = {"serial": "756123456", "edition": "Gold", "tss_active": True, "license_admin": "Ravi", "tss_expiry": "2099-03-31", "virtual_user_limit": 5, "gateway_server": "srv01"}
DNS = mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Lifecycle(TestCase):
    code = "PMP"
    tally_required = False

    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        DNS.start()
        self.addCleanup(DNS.stop)
        self.staff = APIClient()
        self.sa, _ = ensure_account("usuper", Role.SUPERADMIN, "pw")
        self.staff.force_authenticate(self.sa)
        r = self.staff.post(ADM + "projects/", {"code": self.code, "name": "PMP Demo", "frontend_url": "https://pmp.example.com", "backend_url": "https://pmp.example.com/api",
                                                 "tally_serial_required": self.tally_required}, format="json")
        assert r.status_code == 201, r.data
        self.cred = r.data["credentials"]
        self.project = IntegrationProject.objects.get(code=self.code)
        self.api = APIClient()
        self.cfg = {"customer_method": "GET", "customer_path": "/customers", "auth_type": "NONE", "field_mapping": MAPPING,
                    "pagination": {"type": "none", "results_path": ""}, "sync_mode": "POLL", "sync_interval_minutes": 15, "developer_email": "dev@example.com"}

    def v1(self, path, data=None):
        return self.api.post(V1 + path, {"project_code": self.code, **(data or {})}, format="json", HTTP_X_CLIENT_ID=self.cred["client_id"], HTTP_X_CLIENT_SECRET=self.cred["api_secret"])

    def put_cfg(self, **over):
        return self.staff.put(f"{ADM}projects/{self.code}/api-config/", {**self.cfg, **over}, format="json")

    def sync(self, rows=ROWS):
        with mock.patch("apps.integrations.pull._get", return_value=rows):
            return self.staff.post(f"{ADM}projects/{self.code}/sync/")

    def approve(self, reg_id, **body):
        return self.staff.post(f"{ADM}registrations/{reg_id}/approve/", {"license_type": "TRIAL", **body}, format="json")

    def make_customer(self, row=0, **body):
        self.put_cfg()
        self.sync()
        reg = IntegrationRegistration.objects.get(external_customer_id=ROWS[row]["id"])
        out = self.approve(reg.pk, **body).data["result"]
        return out, reg

    def ready(self, **body):
        """An approved customer who already changed the temporary password."""
        out, reg = self.make_customer(**body)
        pw2 = "NewPass#9"
        self.assertEqual(self.v1("auth/change-password", {"user_id": out["user_id"], "current_password": out["temporary_password"], "new_password": pw2}).status_code, 200)
        return out["user_id"], pw2, out


class NonTallyLifecycle(Lifecycle):
    def test_config_validation_and_activation_gate(self):
        self.put_cfg()
        self.assertEqual(self.staff.post(f"{ADM}projects/{self.code}/activate/").status_code, 409)  # nothing validated yet
        with mock.patch("apps.integrations.pull._get", return_value=ROWS):
            cust = self.staff.post(f"{ADM}projects/{self.code}/validate/customer/").data
            mapping = self.staff.post(f"{ADM}projects/{self.code}/validate/mapping/").data
            preview = self.staff.post(f"{ADM}projects/{self.code}/validate/preview/").data
        self.assertTrue(cust["ok"] and mapping["ok"] and preview["ok"])
        self.assertEqual(mapping["mapped"]["email"], "alpha@example.com")
        self.assertEqual(self.staff.post(f"{ADM}projects/{self.code}/activate/").data["status"], "ACTIVE")

    def test_bad_mapping_and_unreachable_api_give_actionable_messages_and_block_activation(self):
        self.put_cfg(field_mapping={**MAPPING, "email": "nope.path"})
        with mock.patch("apps.integrations.pull._get", return_value=ROWS):
            r = self.staff.post(f"{ADM}projects/{self.code}/validate/mapping/").data
        self.assertFalse(r["ok"])
        self.assertIn("email", r["message"])
        boom = s.IntegrationError("EXTERNAL_API_UNAVAILABLE", "The project API could not be read (Timeout).", 502)
        with mock.patch("apps.integrations.pull._get", side_effect=boom):
            r = self.staff.post(f"{ADM}projects/{self.code}/validate/customer/").data
        self.assertEqual((r["ok"], r["code"]), (False, "EXTERNAL_API_UNAVAILABLE"))
        self.assertEqual(self.staff.post(f"{ADM}projects/{self.code}/activate/").status_code, 409)

    def test_sync_queues_registrations_notifies_and_issues_nothing(self):
        self.put_cfg()
        r = self.sync()
        self.assertEqual((r.status_code, r.data["result"]["created"]), (200, 2))
        self.assertEqual(IntegrationRegistration.objects.filter(status="PENDING_REVIEW").count(), 2)
        self.assertFalse(IntegrationCustomer.objects.exists())
        self.assertFalse(IntegrationLicense.objects.exists())
        self.assertEqual(mail.outbox, [])  # no credentials emailed
        n = self.staff.get(ADM + "notifications/").data
        self.assertEqual(n["unread_count"], 2)
        self.assertEqual(n["results"][0]["title"], "New Customer Registration – PMP Demo")
        again = self.sync()
        self.assertEqual((again.data["result"]["created"], again.data["result"]["existing"]), (0, 2))
        self.assertEqual(IntegrationNotification.objects.count(), 2)  # no repeated notifications
        self.staff.post(f"{ADM}notifications/{n['results'][0]['id']}/read/")
        self.assertEqual(self.staff.get(ADM + "notifications/").data["unread_count"], 1)

    def test_push_registration_is_queued_too(self):
        r = self.v1("customers/register", {"external_customer_id": "P1", "customer_name": "Pushed", "email": "p@example.com", "contact_number": "9876543210"})
        self.assertEqual((r.status_code, r.data["data"]["status"], r.data["data"]["license_issued"]), (202, "PENDING_REVIEW", False))
        self.assertFalse(IntegrationCustomer.objects.exists())

    def test_save_and_allow_generates_credentials_and_is_idempotent(self):
        out, reg = self.make_customer(trial_days=5)
        self.assertRegex(out["user_id"], r"^9\d{8}$")  # unique 9-digit numeric serial starting with 9
        pw = out["temporary_password"]
        self.assertEqual(len(pw), 9)
        self.assertTrue(re.search(r"[A-Z]", pw) and re.search(r"[a-z]", pw) and re.search(r"\d", pw))
        c = IntegrationCustomer.objects.get(user_id=out["user_id"])
        self.assertNotEqual(c.password_hash, pw)
        self.assertTrue(check_password(pw, c.password_hash))
        self.assertTrue(c.must_change_password)
        key = IntegrationActivationKey.objects.get(license=c.license)
        self.assertTrue(key.internal)
        self.assertEqual(len(key.key_hash), 64)  # only a hash is stored
        self.assertEqual(len(mail.outbox), 1)  # the account-ready email (User ID + one-time setup link) ...
        self.assertIn(pw, mail.outbox[0].body)  # the one-time temporary password is sent once in the account email (must be changed at first login)
        self.assertAlmostEqual((c.license.expires_at - c.license.starts_at).days, 5, delta=1)
        reg.refresh_from_db()
        self.assertEqual((reg.status, reg.decided_by), ("APPROVED", self.sa.username))
        again = self.approve(reg.pk).data["result"]
        self.assertEqual((again["already_approved"], again["temporary_password"]), (True, None))
        self.assertEqual(IntegrationCustomer.objects.count(), 1)

    def test_license_types_and_validation(self):
        self.put_cfg()
        self.sync()
        regs = list(IntegrationRegistration.objects.order_by("id"))
        self.assertEqual(self.approve(regs[0].pk, license_type="TRIAL", trial_days=0).status_code, 422)
        self.assertEqual(self.approve(regs[0].pk, license_type="ONE_YEAR", years="x").status_code, 422)
        self.assertEqual(self.approve(regs[0].pk, license_type="BOGUS").status_code, 422)
        self.assertFalse(IntegrationCustomer.objects.exists())  # failed approvals change nothing
        y = self.approve(regs[0].pk, license_type="ONE_YEAR", years=2).data["result"]
        lic = IntegrationLicense.objects.get(pk=y["license"]["license_id"])
        self.assertEqual(lic.expires_at, s.add_calendar_years(lic.starts_at, 2))  # calendar years, not 730 days
        life = self.approve(regs[1].pk, license_type="LIFETIME").data["result"]
        self.assertIsNone(IntegrationLicense.objects.get(pk=life["license"]["license_id"]).expires_at)
        prev = self.staff.get(f"{ADM}registrations/{regs[0].pk}/?license_type=ONE_YEAR&years=3").data["registration"]["preview"]
        self.assertEqual(prev["license_type"], "ONE_YEAR")

    def test_hold_reject_and_no_access_before_approval(self):
        self.put_cfg()
        self.sync()
        a, b = IntegrationRegistration.objects.order_by("id")
        self.assertEqual(self.staff.post(f"{ADM}registrations/{a.pk}/hold/", {"note": "check"}, format="json").data["registration"]["status"], "HELD")
        self.assertEqual(self.staff.post(f"{ADM}registrations/{b.pk}/reject/", {"note": "spam"}, format="json").data["registration"]["status"], "REJECTED")
        self.assertEqual(self.approve(b.pk).status_code, 409)  # rejected cannot be approved
        self.assertEqual(self.approve(a.pk).status_code, 200)  # held can
        self.assertEqual(self.v1("auth/verify", {"user_id": "900000000", "password": "x"}).status_code, 401)
        ids = [r["id"] for r in self.staff.get(f"{ADM}registrations/?status=open").data["results"]]
        self.assertEqual(ids, [])

    def test_login_verification_password_change_and_device_authorization(self):
        out, _ = self.make_customer()
        uid, pw = out["user_id"], out["temporary_password"]
        v = self.v1("auth/verify", {"user_id": uid, "password": pw}).data["data"]
        self.assertEqual((v["verified"], v["authorized"], v["must_change_password"]), (True, True, True))
        denied = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "A"})
        self.assertEqual((denied.status_code, denied.data["error"]["code"]), (403, "PASSWORD_CHANGE_REQUIRED"))
        self.assertEqual(self.v1("auth/change-password", {"user_id": uid, "current_password": pw, "new_password": "Better#Pass1"}).status_code, 200)
        ok = self.v1("devices/authorize", {"user_id": uid, "password": "Better#Pass1", "device_id": "A", "device_name": "PC A"}).data["data"]
        self.assertEqual((ok["status"], ok["authorized"], ok["offline_lease_seconds"]), ("AUTHORIZED", True, universe.OFFLINE_LEASE_SECONDS))
        self.assertTrue(self.v1("sessions/introspect", {"session_token": ok["session_token"]}).data["data"]["active"])
        self.assertEqual(self.v1("auth/verify", {"user_id": uid, "password": "wrong"}).status_code, 401)
        self.assertEqual(self.v1("activation/request", {"user_id": uid, "password": "Better#Pass1"}).data["error"]["code"], "USE_DEVICE_AUTHORIZE")

    def test_expired_license_is_denied_and_renewal_restores_access(self):
        uid, pw, out = self.ready(trial_days=2)
        tok = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "A"}).data["data"]["session_token"]
        IntegrationLicense.objects.filter(pk=out["license"]["license_id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
        r = self.v1("auth/verify", {"user_id": uid, "password": pw})
        self.assertEqual((r.status_code, r.data["error"]["code"]), (403, "LICENSE_EXPIRED"))
        self.assertEqual(self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "B"}).data["error"]["code"], "LICENSE_EXPIRED")
        intro = self.v1("sessions/introspect", {"session_token": tok}).data["data"]
        self.assertEqual((intro["active"], intro["reason"]), (False, "license_expired"))
        cust = IntegrationCustomer.objects.get(user_id=uid)
        renew = self.staff.post(f"{ADM}projects/{self.code}/product-customers/{cust.pk}/set-license/", {"license_type": "ONE_YEAR", "years": 2}, format="json")
        self.assertEqual(renew.status_code, 200)
        self.assertEqual(self.v1("auth/verify", {"user_id": uid, "password": pw}).status_code, 200)
        self.assertTrue(self.v1("sessions/introspect", {"session_token": tok}).data["data"]["active"])  # authorised again after the renewal transaction
        self.assertEqual(self.staff.get(f"{ADM}projects/{self.code}/renewals/").data["results"][0]["license_type"], "ONE_YEAR")

    def test_activity_events_appear_in_the_usage_report(self):
        uid, pw, out = self.ready()
        tok = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "A"}).data["data"]["session_token"]
        t0 = timezone.now() - timedelta(minutes=30)
        events = [{"event_type": "APP_OPEN", "session_token": tok, "occurred_at": t0.isoformat(), "event_id": "e1"}, {"event_type": "LOGIN", "session_token": tok, "occurred_at": t0.isoformat(), "event_id": "e2"}]
        events += [{"event_type": "HEARTBEAT", "session_token": tok, "occurred_at": (t0 + timedelta(minutes=m)).isoformat(), "event_id": f"h{m}"} for m in (2, 4, 6)]
        events += [{"event_type": "IDLE_START", "session_token": tok, "occurred_at": (t0 + timedelta(minutes=7)).isoformat(), "event_id": "i1"},
                   {"event_type": "IDLE_END", "session_token": tok, "occurred_at": (t0 + timedelta(minutes=12)).isoformat(), "event_id": "i2"}]
        self.assertEqual(self.v1("usage/events", {"events": events}).data["data"]["recorded"], len(events))
        self.assertEqual(self.v1("usage/events", {"events": events}).data["data"]["recorded"], 0)  # event ids deduplicate retries
        rep = self.staff.get(f"{ADM}projects/{self.code}/usage-report/").data
        c = rep["customers"][0]
        self.assertEqual((c["opens"], c["logins"], c["duration_available"], c["idle_minutes"]), (1, 1, True, 5.0))
        self.assertGreater(c["active_minutes"], 5)

    def test_staff_scope_registrations_are_superadmin_only(self):
        self.put_cfg()
        self.sync()
        admin, _ = ensure_account("someadmin", Role.ADMIN, "pw")
        c = APIClient()
        c.force_authenticate(admin)
        self.assertEqual(c.get(ADM + "registrations/").status_code, 403)
        n = c.get(ADM + "notifications/")  # notifications are project-scoped for Admins: no grant => nothing visible
        self.assertEqual((n.status_code, n.data["results"], n.data["unread_count"]), (200, [], 0))
        reg = IntegrationRegistration.objects.first()
        self.assertEqual(c.post(f"{ADM}registrations/{reg.pk}/approve/", {"license_type": "TRIAL"}, format="json").status_code, 403)


class TallyLifecycle(Lifecycle):
    code = "PMPT"
    tally_required = True

    def test_without_mandatory_tally_data_nothing_is_issued_and_approval_is_refused(self):
        r = self.v1("customers/register", {"external_customer_id": "T1", "customer_name": "Tara", "email": "t@example.com", "contact_number": "9876543210"})
        d = r.data["data"]
        self.assertEqual((r.status_code, d["status"], d["title"], d["license_issued"]), (202, "PENDING_TALLY_VERIFICATION", "Tally License Details Required", False))
        reg = IntegrationRegistration.objects.get()
        self.assertEqual(reg.status, "PENDING_TALLY_VERIFICATION")
        a = self.approve(reg.pk)
        self.assertEqual((a.status_code, a.data["code"]), (409, "TALLY_VERIFICATION_REQUIRED"))  # no Save & Allow until verification passes
        self.assertFalse(IntegrationCustomer.objects.exists())
        self.project.refresh_from_db()
        self.assertTrue(self.project.tally_serial_required)  # never silently Non-Tally
        for drop in ("serial", "edition", "tss_active", "license_admin"):
            bad = {k: v for k, v in GOOD_TALLY.items() if k != drop}
            self.assertEqual(self.v1("tally/submit", {"external_customer_id": "T1", "tally": bad}).data["data"]["status"], "PENDING_TALLY_VERIFICATION", drop)
        self.assertEqual(self.v1("tally/submit", {"external_customer_id": "T1", "tally": {**GOOD_TALLY, "customer_email": "other@example.com"}}).data["data"]["status"], "PENDING_TALLY_VERIFICATION")

    def test_connector_supplies_valid_tally_data_then_approval_uses_tally_serial_as_user_id(self):
        self.v1("customers/register", {"external_customer_id": "T1", "customer_name": "Tara", "email": "t@example.com", "contact_number": "9876543210"})
        r = self.v1("tally/submit", {"external_customer_id": "T1", "tally": GOOD_TALLY})
        self.assertEqual(r.data["data"]["status"], "PENDING_REVIEW")
        reg = IntegrationRegistration.objects.get()
        row = self.staff.get(f"{ADM}registrations/{reg.pk}/").data["registration"]
        self.assertEqual((row["tally_result"], row["user_id"]), ("Verified", "Assigned on approval"))
        out = self.approve(reg.pk, license_type="ONE_YEAR", years=1).data["result"]
        self.assertRegex(out["user_id"], r"^9\d{8}$")  # 9-digit Nexus User ID ...
        c = IntegrationCustomer.objects.get()
        self.assertEqual(c.serial_number, "756123456")  # ... and the verified Tally serial is preserved separately
        self.assertNotEqual(c.user_id, c.serial_number)
        info = IntegrationTallyInfo.objects.get(customer=c)
        self.assertEqual((info.edition, info.license_admin, info.virtual_user_limit), ("Gold", "Ravi", 5))
        pw2 = "TallyPass#1"
        self.v1("auth/change-password", {"user_id": c.user_id, "current_password": out["temporary_password"], "new_password": pw2})
        self.assertEqual(self.v1("devices/authorize", {"user_id": c.user_id, "password": pw2, "device_id": "A"}).data["data"]["status"], "AUTHORIZED")

    def test_retry_verification_through_the_configured_tally_api(self):
        self.put_cfg(tally_path="/tally")
        with mock.patch("apps.integrations.pull._get", return_value=ROWS), mock.patch("apps.integrations.pull.fetch_tally", return_value={}):
            self.staff.post(f"{ADM}projects/{self.code}/sync/")
        reg = IntegrationRegistration.objects.get(external_customer_id="C1")
        self.assertEqual(reg.status, "PENDING_TALLY_VERIFICATION")
        with mock.patch("apps.integrations.pull.fetch_tally", return_value=None):
            self.assertEqual(self.staff.post(f"{ADM}registrations/{reg.pk}/retry-tally/").status_code, 409)
        with mock.patch("apps.integrations.pull.fetch_tally", return_value=GOOD_TALLY):
            r = self.staff.post(f"{ADM}registrations/{reg.pk}/retry-tally/")
        self.assertEqual(r.data["registration"]["status"], "PENDING_REVIEW")

    def test_tally_api_validation_and_activation_requires_it(self):
        self.put_cfg(tally_path="/tally")
        with mock.patch("apps.integrations.pull._get", return_value=ROWS):
            self.staff.post(f"{ADM}projects/{self.code}/validate/customer/")
            self.staff.post(f"{ADM}projects/{self.code}/validate/mapping/")
        self.assertEqual(self.staff.post(f"{ADM}projects/{self.code}/activate/").status_code, 409)  # Tally API not validated yet
        with mock.patch("apps.integrations.pull.fetch_tally", return_value=GOOD_TALLY), mock.patch("apps.integrations.pull._get", return_value=ROWS):
            t = self.staff.post(f"{ADM}projects/{self.code}/validate/tally/").data
        self.assertTrue(t["ok"])
        self.assertNotIn("756123456", json.dumps(t))  # the serial is masked in previews
        self.assertEqual(self.staff.post(f"{ADM}projects/{self.code}/activate/").status_code, 200)


class DeviceTransfer(Lifecycle):
    def test_cancel_keeps_system_a_and_ok_revokes_it_atomically(self):
        uid, pw, out = self.ready()
        a = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "SYS-A", "device_name": "System A"}).data["data"]
        blocked = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "SYS-B", "device_name": "System B"})
        err = blocked.data["error"]
        self.assertEqual((blocked.status_code, err["code"], err["title"]), (409, "TRANSFER_CONFIRMATION_REQUIRED", "Account Already Active on Another System"))
        self.assertEqual(err["buttons"], ["OK – Continue", "Cancel"])
        self.assertIn("log out the previous system", err["message"])
        cancel = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "SYS-B", "confirm_transfer": False}).data["data"]
        self.assertEqual((cancel["status"], cancel["transferred"], cancel["previous_device_still_authorized"]), ("CANCELLED", False, True))
        self.assertTrue(self.v1("sessions/introspect", {"session_token": a["session_token"]}).data["data"]["active"])  # System A untouched
        self.assertEqual(IntegrationSession.objects.filter(status="ACTIVE").count(), 1)
        ok = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "SYS-B", "device_name": "System B", "confirm_transfer": True}).data["data"]
        self.assertEqual((ok["status"], ok["transferred"]), ("TRANSFERRED", True))
        old = self.v1("sessions/introspect", {"session_token": a["session_token"]}).data["data"]
        self.assertEqual((old["active"], old["reason"]), (False, "device_transfer"))  # System A rejected server-side from now on
        self.assertTrue(self.v1("sessions/introspect", {"session_token": ok["session_token"]}).data["data"]["active"])
        self.assertEqual(IntegrationSession.objects.filter(status="ACTIVE").count(), 1)
        self.assertTrue(IntegrationUsageEvent.objects.filter(event_type="DEVICE_TRANSFER").exists())
        moves = self.staff.get(f"{ADM}projects/{self.code}/device-transfers/").data["results"]
        self.assertEqual((moves[0]["from_device"], moves[0]["to_device"]), ("System A", "System B"))
        from .models import IntegrationAudit
        self.assertTrue(IntegrationAudit.objects.filter(action="transfer.confirmed").exists())
        self.assertTrue(IntegrationAudit.objects.filter(action="transfer.cancelled").exists())

    def test_same_device_relogin_does_not_ask_for_confirmation(self):
        uid, pw, out = self.ready()
        self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "SYS-A"})
        again = self.v1("devices/authorize", {"user_id": uid, "password": pw, "device_id": "SYS-A"})
        self.assertEqual((again.status_code, again.data["data"]["status"]), (200, "AUTHORIZED"))
        self.assertEqual(IntegrationSession.objects.filter(status="ACTIVE").count(), 1)


class LegacyUnchanged(TestCase):
    """The real, migration-seeded Mothys is never routed through UNIVERSE."""

    def test_mothys_is_not_universe(self):
        p = IntegrationProject.objects.get(code="MOTHYS")
        self.assertFalse(universe.is_universe(p))
        with self.assertRaises(Exception):
            universe.authorize_device(p, {"user_id": "x", "password": "y", "device_id": "z"})  # credentials fail first; no legacy session is touched


class DeveloperWorkspace(Lifecycle):
    """A developer configures only the projects assigned to them; nothing else."""

    def setUp(self):
        super().setUp()
        IntegrationProject.objects.create(code="OTHERPRJ", name="Other", frontend_url="https://o.example.com", backend_url="https://o.example.com/api")
        r = self.staff.post("/api/config/staff/", {"full_name": "Dev One", "username": "dev1", "email": "dev1@example.com", "role": "DEVELOPER", "developer_projects": [self.code]}, format="json")
        assert r.status_code == 201, r.data
        from django.contrib.auth import get_user_model
        self.dev = APIClient()
        self.dev.force_authenticate(get_user_model().objects.get(username="dev1"))

    def test_developer_can_configure_and_test_only_the_assigned_project(self):
        base = f"/api/integrations/dev/projects/"
        self.assertEqual([p["code"] for p in self.dev.get(base).data["projects"]], [self.code])
        self.assertEqual(self.dev.put(f"{base}{self.code}/api-config/", {**self.cfg}, format="json").status_code, 200)
        with mock.patch("apps.integrations.pull._get", return_value=ROWS):
            ok = self.dev.post(f"{base}{self.code}/validate/mapping/").data
        self.assertTrue(ok["ok"])
        self.assertEqual(self.dev.get(f"{base}{self.code}/docs/markdown/").status_code, 200)
        self.assertEqual(self.dev.get(f"{base}{self.code}/logs/").status_code, 200)
        # not the neighbour project
        for url in (f"{base}OTHERPRJ/", f"{base}OTHERPRJ/api-config/", f"{base}OTHERPRJ/docs/markdown/"):
            self.assertEqual(self.dev.get(url).status_code, 403, url)
        self.assertEqual(self.dev.put(f"{base}OTHERPRJ/api-config/", {**self.cfg}, format="json").status_code, 403)

    def test_developer_is_denied_everything_else(self):
        self.put_cfg()
        self.sync()
        reg = IntegrationRegistration.objects.first()
        for method, url in (("get", ADM + "registrations/"), ("post", f"{ADM}registrations/{reg.pk}/approve/"), ("get", ADM + "projects/"), ("post", f"{ADM}projects/{self.code}/activate/"),
                            ("post", f"{ADM}projects/{self.code}/sync/"), ("post", f"{ADM}projects/{self.code}/send-docs/"), ("post", f"{ADM}projects/{self.code}/rotate-credentials/"),
                            ("get", f"{ADM}projects/{self.code}/customers/"), ("get", "/api/config/staff/"), ("get", "/api/dashboard/project-usage/"), ("get", ADM + "notifications/")):
            self.assertEqual(getattr(self.dev, method)(url).status_code, 403, url)
        me = self.dev.get("/api/auth/me/")
        self.assertEqual((me.status_code, me.data["user"]["role"]), (200, "DEVELOPER"))
        self.assertEqual(self.dev.post("/api/auth/logout/").status_code, 200)

    def test_only_superadmin_can_assign_developers_and_codes_are_validated(self):
        bad = self.staff.post("/api/config/staff/", {"full_name": "X", "username": "devbad", "email": "x@example.com", "role": "DEVELOPER", "developer_projects": ["NOPE"]}, format="json")
        self.assertEqual(bad.status_code, 400)
        none = self.staff.post("/api/config/staff/", {"full_name": "X", "username": "devnone", "email": "x@example.com", "role": "DEVELOPER"}, format="json")
        self.assertEqual(none.status_code, 400)
        self.assertEqual(self.dev.post("/api/config/staff/", {}, format="json").status_code, 403)


class WebhookConnectionTest(Lifecycle):
    def test_connection_test_creates_nothing_and_is_not_a_live_registration(self):
        from .models import IntegrationAudit
        before = IntegrationRegistration.objects.count()
        r = self.v1("webhook/test")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["data"]["customer_created"], False)
        self.assertEqual(IntegrationRegistration.objects.count(), before)
        self.assertTrue(IntegrationAudit.objects.filter(project=self.project, action="webhook.test").exists())
        self.assertFalse(IntegrationAudit.objects.filter(project=self.project, action="registration.received").exists())
        bad = self.api.post(V1 + "webhook/test", {"project_code": self.code}, format="json", HTTP_X_CLIENT_ID=self.cred["client_id"], HTTP_X_CLIENT_SECRET="wrong")
        self.assertEqual(bad.status_code, 401)  # authentication is not bypassed
        wrong_project = self.api.post(V1 + "webhook/test", {"project_code": "OTHER"}, format="json", HTTP_X_CLIENT_ID=self.cred["client_id"], HTTP_X_CLIENT_SECRET=self.cred["api_secret"])
        self.assertEqual(wrong_project.status_code, 403)
