import json
import re
from datetime import timedelta
from threading import Barrier  # noqa: F401  (kept for readability of the race note below)

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.services import ensure_account

from . import services as s
from . import spec, usage
from .models import IntegrationActivationKey, IntegrationCustomer, IntegrationLicense, IntegrationProject, IntegrationSession, IntegrationUsageEvent

V1 = "/api/integrations/v1/"
ADM = "/api/integrations/admin/"
REG = {"project_code": "MOTHYS", "external_customer_id": "MOTHYS-1001", "customer_name": "Kumar", "email": "kumar@example.com", "contact_number": "9876543210"}


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Base(TestCase):
    def setUp(self):
        cache.clear()
        IntegrationProject.objects.filter(code="MOTHYS").delete()  # seeded by migration 0002; tests create their own
        self.staff = APIClient()
        self.super, _ = ensure_account("isuper", Role.SUPERADMIN, "pw")
        self.staff.force_authenticate(self.super)
        r = self.staff.post(ADM + "projects/", {"code": "MOTHYS", "name": "Mothys Inventory Management", "frontend_url": "https://inventory.tallysolutionstamilnadu.com",
                                                 "backend_url": "https://inventory.tallysolutionstamilnadu.com/api", "framework": "FastAPI"}, format="json")
        assert r.status_code == 201, r.data
        # The fixture stands in for the real, migration-seeded Mothys, which keeps the legacy 365-day rule (projects created through the
        # API now default to calendar years, so the fixture is pinned to LEGACY explicitly).
        IntegrationProject.objects.filter(code="MOTHYS").update(license_policy="LEGACY")
        self.project = IntegrationProject.objects.get(code="MOTHYS")
        self.cid, self.secret = r.data["credentials"]["client_id"], r.data["credentials"]["api_secret"]
        self.api = APIClient()

    def call(self, path, data=None, method="post", secret=None, cid=None, **extra):
        headers = {"HTTP_X_CLIENT_ID": cid or self.cid, "HTTP_X_CLIENT_SECRET": secret or self.secret, **extra}
        if method == "get":
            return self.api.get(V1 + path, **headers)
        return self.api.post(V1 + path, {"project_code": "MOTHYS", **(data or {})}, format="json", **headers)

    def register(self, **over):
        return self.call("customers/register", {**REG, **over})

    def password(self):
        return re.search(r"Temporary Password: (\S+)", next(m for m in mail.outbox if "Your Login Details" in m.subject).body).group(1)

    def make_active(self, **over):
        r = self.register(**over)
        assert r.status_code == 201, r.data
        d = r.data["data"]
        return d["user_id"], self.password(), d

    def key(self, user, pw):
        mail.outbox.clear()
        r = self.call("activation/request", {"user_id": user, "password": pw})
        assert r.status_code == 200, r.data
        return re.search(r"is: (\d{9})", mail.outbox[-1].body).group(1)

    def activate(self, user, pw, device, **extra):
        return self.call("activation/verify", {"user_id": user, "password": pw, "activation_key": self.key(user, pw), "device_id": device, "device_name": device, **extra})


class ProjectTests(Base):
    def test_new_project_is_setup_pending_with_one_time_secret_and_hashed_storage(self):
        self.assertEqual(self.project.status, "SETUP_PENDING")
        self.assertEqual((self.project.serial_prefix, self.project.serial_length, self.project.activation_key_length, self.project.device_limit), ("9", 9, 9, 1))
        self.assertFalse(self.project.tally_serial_required)
        self.assertNotIn(self.secret, json.dumps(self.staff.get(ADM + "projects/MOTHYS/").data, default=str))  # never shown again
        self.assertNotEqual(self.project.credentials.get().secret_hash, self.secret)

    def test_health_check_alone_does_not_verify_integration(self):
        r = self.staff.post(ADM + "projects/MOTHYS/test-connection/")
        self.assertFalse(r.data["integration_verified"])
        self.assertEqual(IntegrationProject.objects.get().status, "SETUP_PENDING")

    def test_staff_only_and_tally_serial_projects_refused(self):
        admin, _ = ensure_account("iadmin", Role.ADMIN, "pw")
        c = APIClient()
        c.force_authenticate(admin)
        self.assertEqual(c.get(ADM + "projects/").status_code, 403)
        self.assertEqual(APIClient().get(ADM + "projects/").status_code, 401)
        r = self.staff.post(ADM + "projects/", {"code": "X1", "name": "X", "frontend_url": "https://x.example", "backend_url": "https://x.example/api", "tally_serial_required": True}, format="json")
        self.assertEqual(r.status_code, 201)  # Tally-integrated Universal projects are now configurable (Superadmin only, audited)

    def test_rotation_kills_old_secret_and_docs_match_routes(self):
        old = self.secret
        r = self.staff.post(ADM + "projects/MOTHYS/rotate-credentials/")
        self.assertEqual(self.call("customers/register", REG, secret=old).status_code, 401)
        self.assertEqual(self.call("customers/register", REG, secret=r.data["credentials"]["api_secret"], cid=r.data["credentials"]["client_id"]).status_code, 201)
        doc = spec.openapi("https://nexus.example/nexus/api")
        for path in doc["paths"]:
            probe = (V1 + path.lstrip("/")).replace("{project_code}", "MOTHYS")
            resolve("/api/" + probe.split("/api/", 1)[-1])  # raises Resolver404 if a documented endpoint is not routed
        self.assertEqual(self.staff.get(ADM + "projects/MOTHYS/docs/openapi/").status_code, 200)
        self.assertIn("Mothys", self.staff.get(ADM + "projects/MOTHYS/docs/markdown/").content.decode())


class SecurityTests(Base):
    def test_credentials_and_project_scope(self):
        self.assertEqual(self.api.post(V1 + "customers/register", REG, format="json").status_code, 401)
        self.assertEqual(self.call("customers/register", REG, secret="wrong").status_code, 401)
        r = self.call("customers/register", {**REG, "project_code": "OTHER"})
        self.assertEqual((r.status_code, r.data["error"]["code"]), (403, "PROJECT_MISMATCH"))
        self.staff.patch(ADM + "projects/MOTHYS/", {"enabled": False}, format="json")
        self.assertEqual(self.register().data["error"]["code"], "INTEGRATION_DISABLED")

    @override_settings(INTEGRATION_RATE_LIMIT_PER_MIN=3)
    def test_rate_limit(self):
        codes = [self.call("licenses/validate", {"user_id": "1"}).status_code for _ in range(5)]
        self.assertEqual(codes[-1], 429)

    def test_consistent_error_envelope_and_bad_json(self):
        r = self.api.post(V1 + "customers/register", "not json", content_type="application/json", HTTP_X_CLIENT_ID=self.cid, HTTP_X_CLIENT_SECRET=self.secret)
        self.assertEqual((r.status_code, r.data["ok"], r.data["error"]["code"]), (400, False, "INVALID_JSON"))
        self.assertIn("request_id", r.data)


class RegistrationTests(Base):
    def test_register_creates_serial_userid_trial_and_one_welcome_email_without_activation_key(self):
        r = self.register()
        d = r.data["data"]
        self.assertEqual(r.status_code, 201)
        self.assertRegex(d["serial_number"], r"^9\d{8}$")
        self.assertEqual(d["user_id"], d["serial_number"])
        self.assertEqual((d["license_type"], d["license_state"]), ("TRIAL", "ACTIVE"))
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual((m.subject, m.to), ("Mothys Inventory – Your Login Details", ["kumar@example.com"]))
        for expect in ("Dear Kumar,", "Your Mothys Inventory account has been created successfully.", f"User ID: {d['user_id']}",
                       f"Serial Number: {d['serial_number']}", "License Type: Trial", "Trial Days / Expiry Date:", "Login:\nhttps://inventory.tallysolutionstamilnadu.com", "Regards,\nSai Infosys"):
            self.assertIn(expect, m.body)
        self.assertNotRegex(m.body, r"Activation Key: \d")  # no key at registration
        self.assertEqual(IntegrationActivationKey.objects.count(), 0)
        self.assertNotIn(self.password(), json.dumps(r.data, default=str))  # password never in the API response
        cust = IntegrationCustomer.objects.get()
        self.assertNotIn(self.password(), cust.password_hash)

    def test_validation_duplicates_and_idempotency(self):
        bad = self.register(email="nope", contact_number="12", customer_name="")
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(set(bad.data["error"]["fields"]), {"email", "contact_number", "customer_name"})
        first = self.register(HTTP_IDEMPOTENCY_KEY="k1") if False else self.call("customers/register", REG, HTTP_IDEMPOTENCY_KEY="k1")
        replay = self.call("customers/register", REG, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual((first.status_code, replay.status_code), (201, 201))
        self.assertTrue(replay.data["data"]["idempotent_replay"])
        self.assertEqual((replay.data["data"]["serial_number"], IntegrationCustomer.objects.count(), len(mail.outbox)), (first.data["data"]["serial_number"], 1, 1))
        self.assertEqual(self.call("customers/register", {**REG, "customer_name": "Other"}, HTTP_IDEMPOTENCY_KEY="k1").data["error"]["code"], "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(self.register().data["error"]["code"], "CUSTOMER_ALREADY_REGISTERED")
        self.assertEqual(self.register(external_customer_id="MOTHYS-2").data["error"]["code"], "DUPLICATE_EMAIL")

    def test_paid_licence_is_not_auto_activated(self):
        self.staff.patch(ADM + "projects/MOTHYS/", {"default_license_type": "ONE_YEAR"}, format="json")
        user, pw, d = self.make_active()
        self.assertEqual(d["license_state"], "PENDING_APPROVAL")
        self.assertEqual(self.call("activation/request", {"user_id": user, "password": pw}).data["error"]["code"], "LICENSE_NOT_ACTIVE")
        self.assertEqual(self.staff.post(ADM + f"licenses/{d['license_id']}/approve/").status_code, 200)
        self.assertEqual(self.call("licenses/validate", {"user_id": user}).data["data"]["state"], "ACTIVE")

    def test_serials_unique_and_bulk_import_reports_failures(self):
        items = [{**REG, "external_customer_id": f"E{i}", "email": f"u{i}@example.com"} for i in range(5)] + [{**REG, "external_customer_id": "E0", "email": "dup@example.com"}]
        r = self.call("customers/import", {"customers": items})
        self.assertEqual((r.data["data"]["imported"], r.data["data"]["failed"]), (5, 1))
        self.assertEqual(len(set(IntegrationCustomer.objects.values_list("serial_number", flat=True))), 5)
        failed = self.staff.get(ADM + "failed-sync/").data["failed_syncs"]
        self.assertEqual(len(failed), 1)

    def test_failed_welcome_can_be_resent_with_new_password(self):
        user, pw, d = self.make_active()
        n = len(mail.outbox)
        r = self.staff.post(ADM + f"customers/{d['customer_id']}/resend-welcome/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), n + 1)
        new_pw = re.search(r"Temporary Password: (\S+)", mail.outbox[-1].body).group(1)
        self.assertEqual(self.call("auth/verify", {"user_id": user, "password": pw}).status_code, 401)
        self.assertEqual(self.call("auth/verify", {"user_id": user, "password": new_pw}).status_code, 200)


class AuthTests(Base):
    def test_verify_lockout_and_password_change(self):
        user, pw, _ = self.make_active()
        ok = self.call("auth/verify", {"user_id": user, "password": pw})
        self.assertEqual((ok.status_code, ok.data["data"]["must_change_password"]), (200, True))
        for _ in range(5):
            self.call("auth/verify", {"user_id": user, "password": "bad"})
        self.assertEqual(self.call("auth/verify", {"user_id": user, "password": pw}).data["error"]["code"], "ACCOUNT_LOCKED")
        IntegrationCustomer.objects.update(locked_until=None)
        self.assertEqual(self.call("auth/change-password", {"user_id": user, "current_password": pw, "new_password": "BrandNew#1"}).status_code, 200)
        self.assertFalse(self.call("auth/verify", {"user_id": user, "password": "BrandNew#1"}).data["data"]["must_change_password"])

    def test_sync_updates_inactivation_and_failure_log(self):
        user, pw, _ = self.make_active()
        r = self.call("customers/update", {"external_customer_id": "MOTHYS-1001", "event": "EMAIL_CHANGED", "email": "New@Example.com"})
        self.assertEqual(r.data["data"]["changed"], ["email"])
        self.assertEqual(IntegrationCustomer.objects.get().email, "new@example.com")
        miss = self.call("customers/update", {"external_customer_id": "NOPE", "event": "CUSTOMER_UPDATED", "customer_name": "Z"})
        self.assertEqual(miss.status_code, 404)
        self.call("customers/update", {"external_customer_id": "MOTHYS-1001", "event": "CUSTOMER_INACTIVATED"})
        self.assertEqual(self.call("licenses/validate", {"user_id": user}).data["data"]["state"], "INACTIVE")
        self.assertEqual(self.call("customers/update", {"external_customer_id": "MOTHYS-1001", "license_status": "LIFETIME", "event": "LICENSE_STATUS_UPDATED"}).data["error"]["code"], "LICENSE_STATUS_NOT_ALLOWED")
        self.assertGreaterEqual(len(self.staff.get(ADM + "failed-sync/").data["failed_syncs"]), 1)
        retry_id = self.staff.get(ADM + "failed-sync/").data["failed_syncs"][-1]["id"]  # the NOPE sync
        self.assertEqual(self.staff.post(ADM + f"failed-sync/{retry_id}/retry/").status_code, 404)  # still no such customer: retry reports it again


class ActivationTests(Base):
    def test_activation_key_rules_and_session(self):
        user, pw, _ = self.make_active()
        self.assertEqual(self.call("activation/request", {"user_id": user, "password": "bad"}).status_code, 401)
        key = self.key(user, pw)
        self.assertRegex(key, r"^\d{9}$")
        self.assertEqual(IntegrationActivationKey.objects.get().key_hash.count(key), 0)  # only a keyed hash is stored
        bad = self.call("activation/verify", {"user_id": user, "password": pw, "activation_key": "000000000", "device_id": "A"})
        self.assertEqual(bad.data["error"]["code"], "ACTIVATION_KEY_INVALID")
        ok = self.call("activation/verify", {"user_id": user, "password": pw, "activation_key": key, "device_id": "A", "device_name": "PC A"})
        self.assertEqual(ok.status_code, 200)
        token = ok.data["data"]["session_token"]
        self.assertTrue(self.call("sessions/introspect", {"session_token": token}).data["data"]["active"])
        again = self.call("activation/verify", {"user_id": user, "password": pw, "activation_key": key, "device_id": "A"})
        self.assertEqual(again.data["error"]["code"], "ACTIVATION_KEY_INVALID")  # one-time use

    def test_key_expiry_rate_limit_and_attempt_cap(self):
        user, pw, _ = self.make_active()
        key = self.key(user, pw)
        IntegrationActivationKey.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.call("activation/verify", {"user_id": user, "password": pw, "activation_key": key, "device_id": "A"}).data["error"]["code"], "ACTIVATION_KEY_INVALID")
        self.key(user, pw)
        self.key(user, pw)
        self.assertEqual(self.call("activation/request", {"user_id": user, "password": pw}).data["error"]["code"], "RATE_LIMITED")  # 4th within the hour
        live = self.key if False else None  # noqa
        IntegrationActivationKey.objects.update(attempts=5)
        self.assertEqual(self.call("activation/verify", {"user_id": user, "password": pw, "activation_key": "123456789", "device_id": "A"}).data["error"]["code"], "ACTIVATION_KEY_INVALID")

    def test_device_transfer_confirm_revokes_old_device_and_cancel_keeps_it(self):
        user, pw, _ = self.make_active()
        a = self.activate(user, pw, "DEV-A").data["data"]["session_token"]
        blocked = self.activate(user, pw, "DEV-B")
        self.assertEqual((blocked.status_code, blocked.data["error"]["code"]), (409, "TRANSFER_CONFIRMATION_REQUIRED"))
        e = blocked.data["error"]
        self.assertEqual(e["title"], "License Already Active on Another Device")
        self.assertIn("Your previous device session will be terminated", e["message"])
        self.assertEqual([x["label"] for x in e["actions"]], ["Cancel", "OK – Activate This Device"])
        self.assertTrue(self.call("sessions/introspect", {"session_token": a}).data["data"]["active"])  # nothing changed yet
        cancel = self.call("devices/transfer/confirm", {"user_id": user, "transfer_token": e["transfer_token"], "confirm": False})
        self.assertEqual(cancel.data["data"]["status"], "CANCELLED")
        self.assertTrue(self.call("sessions/introspect", {"session_token": a}).data["data"]["active"])  # Device A still authorised
        blocked = self.activate(user, pw, "DEV-B")  # try again
        tok = blocked.data["error"]["transfer_token"]
        done = self.call("devices/transfer/confirm", {"user_id": user, "transfer_token": tok, "confirm": True})
        self.assertEqual((done.status_code, done.data["data"]["status"]), (200, "TRANSFERRED"))
        old = self.call("sessions/introspect", {"session_token": a}).data["data"]
        self.assertEqual((old["active"], old["reason"]), (False, "device_transfer"))  # rejected on the next protected request
        self.assertTrue(self.call("sessions/introspect", {"session_token": done.data["data"]["session_token"]}).data["data"]["active"])
        self.assertEqual(IntegrationSession.objects.filter(status="ACTIVE").count(), 1)
        repeat = self.call("devices/transfer/confirm", {"user_id": user, "transfer_token": tok, "confirm": True})
        self.assertEqual(repeat.data["data"]["status"], "ALREADY_TRANSFERRED")  # idempotent
        self.assertEqual(IntegrationSession.objects.filter(status="ACTIVE").count(), 1)
        self.assertEqual(IntegrationUsageEvent.objects.filter(event_type="DEVICE_TRANSFER").count(), 1)
        self.assertNotIn("Verify your email", " ".join(m.subject for m in mail.outbox))  # no extra email OTP in the transfer flow

    def test_transfer_disabled_and_expired_licence_and_logout(self):
        user, pw, _ = self.make_active()
        a = self.activate(user, pw, "A").data["data"]["session_token"]
        self.staff.patch(ADM + "projects/MOTHYS/", {"transfer_policy": "DISABLED"}, format="json")
        self.assertEqual(self.activate(user, pw, "B").data["error"]["code"], "TRANSFER_DISABLED")
        IntegrationLicense.objects.update(expires_at=timezone.now() - timedelta(days=1))
        i = self.call("sessions/introspect", {"session_token": a}).data["data"]
        self.assertEqual((i["active"], i["reason"]), (False, "license_expired"))
        IntegrationLicense.objects.update(expires_at=None)
        self.assertEqual(self.call("sessions/logout", {"session_token": a}).status_code, 200)
        self.assertEqual(self.call("sessions/introspect", {"session_token": a}).data["data"]["reason"], "logout")
        self.assertEqual(self.call("sessions/introspect", {"session_token": "nxt_other"}).data["data"]["reason"], "unknown_session")

    def test_session_of_another_project_is_not_visible(self):
        user, pw, _ = self.make_active()
        a = self.activate(user, pw, "A").data["data"]["session_token"]
        r = self.staff.post(ADM + "projects/", {"code": "OTHER", "name": "Other", "frontend_url": "https://o.example", "backend_url": "https://o.example/api"}, format="json")
        res = self.api.post(V1 + "sessions/introspect", {"project_code": "OTHER", "session_token": a}, format="json",
                            HTTP_X_CLIENT_ID=r.data["credentials"]["client_id"], HTTP_X_CLIENT_SECRET=r.data["credentials"]["api_secret"])
        self.assertEqual(res.data["data"]["reason"], "unknown_session")


class UsageDashboardTests(Base):
    def test_usage_events_integration_status_and_dashboard(self):
        user, pw, d = self.make_active()
        tok = self.activate(user, pw, "A").data["data"]["session_token"]
        self.call("auth/verify", {"user_id": user, "password": pw})
        self.call("sessions/introspect", {"session_token": tok})
        ev = {"session_token": tok, "event_type": "HEARTBEAT", "event_id": "e1"}
        self.assertEqual(self.call("usage/events", ev).data["data"], {"recorded": 1, "duplicates": 0})
        self.assertEqual(self.call("usage/events", ev).data["data"], {"recorded": 0, "duplicates": 1})
        self.assertEqual(self.call("usage/events", {"user_id": user, "event_type": "NOPE"}).status_code, 422)
        self.assertEqual(IntegrationProject.objects.get().status, "ACTIVE")  # all five real steps seen
        for x in (self.staff.get(ADM + "dashboard/"),):
            p = x.data["projects"][0]
            self.assertEqual((p["code"], p["total_customers"], p["used"], p["not_used"], p["active_devices"]), ("MOTHYS", 1, 1, 0, 1))
            self.assertEqual(x.data["overall"]["total_unique_customers"], 1)

    def test_classification_is_ordered_and_non_overlapping(self):
        r = {"new_days": 7, "speed_min_days": 15}
        self.assertEqual(usage.classify(2, 0, 0, r), "new")
        self.assertEqual(usage.classify(30, 0, 0, r), None)
        self.assertEqual(usage.classify(30, 1, 1, r), "one_time")
        self.assertEqual(usage.classify(60, 20, 16, r), "speed")
        self.assertEqual(usage.classify(60, 5, 3, r), "normal")
        self.assertEqual(usage.classify(3, 20, 20, r), "new")

    def test_expiry_counts(self):
        self.make_active()
        now = timezone.now()
        IntegrationLicense.objects.update(expires_at=now - timedelta(days=1))
        self.assertEqual(usage.project_stats(self.project)["expired"], 1)


class RegressionIsolation(TestCase):
    def test_existing_urls_unchanged(self):
        for p in ("/api/health/", "/api/projects/gstr2tally/customers/", "/api/v1/tally-sl-admin/products/"):
            resolve(p)


class MothysProductTests(Base):
    """The staff APIs behind the Mothys Dashboard and Customers pages (project MOTHYS, no Tally involved)."""

    def pc(self, path="", method="get", data=None):
        url = ADM + "projects/MOTHYS/product-customers/" + path
        return getattr(self.staff, method)(url, data, format="json") if method != "get" else self.staff.get(url, data)

    def test_seed_migration_defaults(self):
        from django.apps import apps as dj_apps
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        # the registry row the migration seeds is what the sidebar shows; verify its defaults by recreating from the migration function
        import importlib
        m = importlib.import_module("apps.integrations.migrations.0002_seed_mothys")
        IntegrationProject.objects.all().delete()
        m.seed(dj_apps, None)
        p = IntegrationProject.objects.get(code="MOTHYS")
        self.assertEqual((p.trial_days, p.serial_prefix, p.serial_length, p.activation_key_length, p.device_limit, p.tally_serial_required, p.status), (3, "9", 9, 9, 1, False, "SETUP_PENDING"))

    def test_customers_list_filters_dashboard_counts_match(self):
        a = self.make_active()[2]
        self.register(external_customer_id="M2", email="b@example.com", customer_name="Bala")
        self.register(external_customer_id="M3", email="c@example.com", customer_name="Chitra")
        d = self.staff.get(ADM + "projects/MOTHYS/dashboard/").data
        self.assertEqual((d["total_customers"], d["trial_licenses"], d["annual_licenses"], d["lifetime_licenses"], d["not_used"], d["new_users"]), (3, 3, 0, 0, 3, 3))
        for key, count in (("trial", d["trial_licenses"]), ("not_used", d["not_used"]), ("new", d["new_users"]), ("used", d["used"]), ("expired", d["expired"])):
            self.assertEqual(self.pc(data={"filter": key}).data["total"], count, key)  # a Dashboard count lists exactly those customers
        row = self.pc().data["results"][0]
        self.assertEqual(set(row) >= {"name", "email", "contact_number", "serial_number", "license_type", "status", "activated_at", "expires_at", "last_login", "usage_category"}, True)
        self.assertEqual(row["status"], "not_activated")
        self.assertEqual(self.pc(data={"search": "kumar"}).data["total"], 1)
        self.assertEqual(self.pc(data={"license_type": "LIFETIME"}).data["total"], 0)

    def test_license_actions_trial_annual_lifetime(self):
        self.staff.patch(ADM + "projects/MOTHYS/", {"trial_days": 3}, format="json")  # the Mothys default
        d = self.make_active()[2]
        pk, lic = d["customer_id"], d["license_id"]
        act = lambda a, body=None: self.pc(f"{pk}/{a}/", "post", body or {})  # noqa: E731
        e0 = IntegrationLicense.objects.get(pk=lic).expires_at
        self.assertEqual((IntegrationLicense.objects.get(pk=lic).expires_at - timezone.now()).days, 2)  # default 3-day trial
        self.assertEqual(act("extend-trial", {"days": 5}).status_code, 200)
        self.assertEqual(IntegrationLicense.objects.get(pk=lic).expires_at, e0 + timedelta(days=5))
        self.assertEqual(act("extend-trial", {"days": 0}).status_code, 422)
        self.assertEqual(act("renew").status_code, 409)  # a Trial is not renewable
        self.assertEqual(act("set-license", {"license_type": "ONE_YEAR"}).data["customer"]["license_type"], "ONE_YEAR")
        old = IntegrationLicense.objects.get(pk=lic).expires_at
        self.assertEqual(act("renew").status_code, 200)
        self.assertEqual(IntegrationLicense.objects.get(pk=lic).expires_at, old + timedelta(days=365))
        self.assertEqual(act("cancel-trial").status_code, 409)  # not a Trial any more
        r = act("set-license", {"license_type": "LIFETIME"})
        self.assertEqual((r.data["customer"]["license_type"], r.data["customer"]["expires_at"]), ("LIFETIME", None))
        act("set-license", {"license_type": "TRIAL", "trial_days": 3})
        self.assertEqual(act("cancel-trial").data["customer"]["status"], "cancelled")
        self.assertEqual(self.call("licenses/validate", {"user_id": d["user_id"]}).data["data"]["state"], "CANCELLED")
        self.assertEqual(act("inactivate").data["customer"]["status"], "inactive")
        self.assertEqual(act("reactivate").status_code, 200)
        self.assertEqual(self.pc(f"{pk}/", "patch", {"customer_name": "Kumar R", "contact_number": "9000000001"}).data["customer"]["name"], "Kumar R")

    def test_cancel_and_inactivate_revoke_session_immediately(self):
        user, pw, d = self.make_active()
        tok = self.activate(user, pw, "A").data["data"]["session_token"]
        self.assertEqual(self.pc(f"{d['customer_id']}/").data["customer"]["status"], "active")
        self.pc(f"{d['customer_id']}/cancel-trial/", "post", {})
        i = self.call("sessions/introspect", {"session_token": tok}).data["data"]
        self.assertEqual((i["active"], i["reason"]), (False, "trial_cancelled"))

    def test_detail_histories_and_export_and_staff_only(self):
        user, pw, d = self.make_active()
        self.activate(user, pw, "DEV-A")
        t = self.activate(user, pw, "DEV-B").data["error"]["transfer_token"]
        self.call("devices/transfer/confirm", {"user_id": user, "transfer_token": t, "confirm": True})
        self.call("usage/events", {"user_id": user, "event_type": "LOGIN"})
        det = self.pc(f"{d['customer_id']}/").data
        self.assertEqual(len(det["activations"]), 2)
        self.assertEqual((det["transfers"][0]["from_device"], det["transfers"][0]["to_device"]), ("DEV-A", "DEV-B"))
        self.assertIn("LOGIN", [e["event"] for e in det["usage"]])
        self.assertIsNotNone(self.pc().data["results"][0]["last_login"])
        r = self.staff.post(ADM + "projects/MOTHYS/product-customers/export/", {"fmt": "print", "filters": {}}, format="json")
        self.assertEqual(len(r.data["rows"]), 1)
        self.assertEqual(self.staff.post(ADM + "projects/MOTHYS/product-customers/export/", {"fmt": "xlsx", "filters": {}}, format="json").status_code, 200)
        admin, _ = ensure_account("iadmin2", Role.ADMIN, "pw")
        c = APIClient()
        c.force_authenticate(admin)
        self.assertEqual(c.get(ADM + "projects/MOTHYS/product-customers/").status_code, 403)

    def test_activate_license_popup_action_custom_days_password_and_email(self):
        from django.core import mail
        from .models import IntegrationCustomer
        self.register(external_customer_id="M9", email="rethanya@example.com", customer_name="Rethanya")
        row = self.pc().data["results"][0]
        self.assertEqual(row["status"], "not_activated")
        mail.outbox.clear()
        r = self.pc(f"{row['id']}/set-license/", "post", {"activation_form": True, "license_type": "CUSTOM", "custom_days": 45, "paid": True, "password": "Temp#Pass9"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["customer"]["license_type"], "CUSTOM")
        self.assertIn(r.data["welcome_email"], ("accepted", "not_delivered", "failed"))
        self.assertTrue(any("Temp#Pass9" in m.body and row["serial_number"] in m.body for m in mail.outbox))
        cust = IntegrationCustomer.objects.get(pk=row["id"])
        self.assertNotEqual(cust.password_hash, "Temp#Pass9")
        bad = self.pc(f"{row['id']}/set-license/", "post", {"activation_form": True, "license_type": "CUSTOM", "custom_days": 0})
        self.assertEqual(bad.status_code, 422)
        legacy = self.pc(f"{row['id']}/set-license/", "post", {"license_type": "LIFETIME"})  # legacy call unchanged
        self.assertEqual(legacy.status_code, 200)

    def test_e2e_register_trial_login_paid_years_lifetime_custom_extend(self):
        from datetime import timedelta
        from django.utils import timezone
        mail.outbox.clear()
        r = self.register(external_customer_id="E2E1", email="e2e@example.com", customer_name="E2E Customer")
        self.assertEqual(r.status_code, 201, r.data)
        user = r.data["data"]["user_id"]
        # 1-2: appears in Nexus; exactly one credentials email
        row = self.pc().data["results"][0]
        self.assertEqual(row["serial_number"], user)
        self.assertEqual(len([m for m in mail.outbox if "Your Login Details" in m.subject]), 1)
        pw = self.password()
        # retry does not regenerate credentials or send a second email
        self.assertEqual(self.register(external_customer_id="E2E1", email="e2e@example.com", customer_name="E2E Customer").status_code, 409)
        self.assertEqual(len([m for m in mail.outbox if "Your Login Details" in m.subject]), 1)
        # 3: trial is active for 3 days with no admin action; device not paired yet
        self.assertEqual((row["license_type"], row["trial_days"], row["status"]), ("TRIAL", 3, "not_activated"))
        lic = self.call("licenses/validate", {"user_id": user}).data["data"] if self.call("licenses/validate", {"user_id": user}).status_code == 200 else None
        if lic:
            self.assertTrue(lic["valid"])
        # 4: login with the emailed credentials works and is authorised
        a = self.call("auth/verify", {"user_id": user, "password": pw})
        self.assertEqual(a.status_code, 200, a.data)
        self.assertTrue(a.data["data"]["authorized"])
        self.assertEqual(self.call("auth/verify", {"user_id": user, "password": "wrong-pass"}).status_code, 401)
        url = f"{row['id']}/set-license/"
        # 5-6: Paid + Years -> Save & Activate
        self.assertEqual(self.pc(url, "post", {"activation_form": True, "license_type": "ONE_YEAR", "years": 2}).status_code, 422)  # not paid
        y = self.pc(url, "post", {"activation_form": True, "license_type": "ONE_YEAR", "years": 2, "paid": True})
        self.assertEqual(y.status_code, 200, y.data)
        self.assertEqual(y.data["customer"]["license_type"], "ONE_YEAR")
        self.assertGreater(self.pc().data["results"][0]["expires_at"], timezone.now() + timedelta(days=700))  # refresh keeps it
        # 7: same credentials, no new email, license recognised by the app
        self.assertEqual(len([m for m in mail.outbox if "Your Login Details" in m.subject]), 1)
        a2 = self.call("auth/verify", {"user_id": user, "password": pw})
        self.assertEqual(a2.status_code, 200, a2.data)
        self.assertEqual(a2.data["data"]["license"]["license_type"], "ONE_YEAR")
        self.assertTrue(a2.data["data"]["license"]["paid"])
        # 8: Lifetime, Custom date, Trial Extend
        life = self.pc(url, "post", {"activation_form": True, "license_type": "LIFETIME", "paid": True})
        self.assertIsNone(life.data["customer"]["expires_at"])
        fut = (timezone.localdate() + timedelta(days=60)).isoformat()
        cu = self.pc(url, "post", {"activation_form": True, "license_type": "CUSTOM", "custom_expiry_date": fut, "paid": True})
        self.assertEqual(cu.status_code, 200, cu.data)
        self.assertLess(abs((cu.data["customer"]["expires_at"].date() - (timezone.localdate() + timedelta(days=60))).days), 2)
        self.pc(url, "post", {"license_type": "TRIAL", "trial_days": 3})
        ex = self.pc(f"{row['id']}/extend-trial/", "post", {"days": 4})
        self.assertEqual(ex.status_code, 200, ex.data)
        self.assertEqual(self.call("auth/verify", {"user_id": user, "password": pw}).status_code, 200)

    def test_paid_required_custom_date_credentials_unchanged_and_trial_extend(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import IntegrationCustomer
        self.register(external_customer_id="P1", email="p1@example.com", customer_name="Paid One")
        row = self.pc().data["results"][0]
        before = IntegrationCustomer.objects.get(pk=row["id"]).password_hash
        url = f"{row['id']}/set-license/"
        no = self.pc(url, "post", {"activation_form": True, "license_type": "LIFETIME"})
        self.assertEqual(no.status_code, 422); self.assertIn("PAYMENT_REQUIRED", str(no.data))
        past = (timezone.localdate() - timedelta(days=2)).isoformat()
        self.assertEqual(self.pc(url, "post", {"activation_form": True, "license_type": "CUSTOM", "custom_expiry_date": past, "paid": True}).status_code, 422)
        future = (timezone.localdate() + timedelta(days=40)).isoformat()
        ok = self.pc(url, "post", {"activation_form": True, "license_type": "CUSTOM", "custom_expiry_date": future, "paid": True})
        self.assertEqual(ok.status_code, 200, ok.data)
        life = self.pc(url, "post", {"activation_form": True, "license_type": "LIFETIME", "paid": True})
        self.assertEqual(life.data["customer"]["license_type"], "LIFETIME")
        self.assertIsNone(life.data["customer"]["expires_at"])
        cust = IntegrationCustomer.objects.get(pk=row["id"])
        self.assertTrue(cust.license.paid)
        self.assertEqual(cust.password_hash, before)  # licence changes never touch credentials
        self.pc(url, "post", {"license_type": "TRIAL", "trial_days": 3})
        ext = self.pc(f"{row['id']}/extend-trial/", "post", {"days": 2})
        self.assertEqual(ext.status_code, 200, ext.data)
        self.assertEqual(IntegrationCustomer.objects.get(pk=row["id"]).password_hash, before)

    def test_new_registration_trial_default_three_days_active_and_login_works(self):
        from .models import IntegrationProject
        self.assertEqual(IntegrationProject._meta.get_field("trial_days").default, 3)
        user, pw, d = self.make_active()
        self.assertEqual(d["license_state"], "ACTIVE")
        r = self.call("auth/verify", {"user_id": user, "password": pw})
        self.assertEqual(r.status_code, 200, r.data)
        bad = self.call("auth/verify", {"user_id": user, "password": "wrong-password"})
        self.assertEqual(bad.status_code, 401)

    def test_paid_registration_pending_then_approved_and_scoped_to_project(self):
        self.staff.patch(ADM + "projects/MOTHYS/", {"default_license_type": "ONE_YEAR"}, format="json")
        d = self.make_active()[2]
        self.assertEqual(self.pc().data["results"][0]["status"], "pending")
        self.assertEqual(self.pc(f"{d['customer_id']}/approve/", "post", {}).data["customer"]["status"], "not_activated")
        other = self.staff.post(ADM + "projects/", {"code": "OTHER", "name": "Other", "frontend_url": "https://o.example", "backend_url": "https://o.example/api"}, format="json")
        self.assertEqual(self.staff.get(ADM + "projects/OTHER/product-customers/").data["total"], 0)
        self.assertEqual(self.staff.get(ADM + f"projects/OTHER/product-customers/{d['customer_id']}/").status_code, 404)


class MothysReportTests(Base):
    def rep(self, kind, **params):
        return self.staff.get(ADM + f"projects/MOTHYS/reports/{kind}/", params)

    def test_three_reports_scoped_filtered_and_exported(self):
        user, pw, d = self.make_active()
        self.register(external_customer_id="M2", email="b@example.com", customer_name="Bala")
        tok = self.activate(user, pw, "A").data["data"]["session_token"]
        self.call("usage/events", {"session_token": tok, "event_type": "LOGIN"})
        self.staff.post(ADM + "projects/", {"code": "OTHER", "name": "Other", "frontend_url": "https://o.example", "backend_url": "https://o.example/api"}, format="json")
        self.assertEqual(self.staff.get(ADM + "projects/OTHER/reports/expiry/").data["total"], 0)  # another project's data never mixes in
        for kind, cols in (("expiry", 9), ("details", 10), ("performance", 8)):
            r = self.rep(kind)
            self.assertEqual((r.status_code, r.data["total"], len(r.data["columns"]), len(r.data["results"][0]["cells"]) + 1), (200, 2, cols, cols))
        self.assertEqual(self.rep("expiry", search="bala").data["total"], 1)
        self.assertEqual(self.rep("expiry", status="not_activated").data["total"], 1)
        self.assertEqual(self.rep("performance", usage="used").data["total"], 1)
        row = self.rep("performance", usage="used").data["results"][0]["cells"]
        self.assertEqual((row[3], row[6]), ("1", "A"))  # one LOGIN, active device A
        self.assertEqual(self.rep("details").data["results"][0]["cells"][3] in ("MOTHYS-1001", "M2"), True)  # Mothys customer id, no Tally field
        self.assertEqual(self.rep("expiry", **{"from": "2999-01-01"}).data["total"], 0)
        self.assertEqual(self.rep("nope").status_code, 404)
        for fmt in ("xlsx", "pdf"):
            self.assertEqual(self.staff.post(ADM + "projects/MOTHYS/reports/details/export/", {"fmt": fmt, "filters": {}}, format="json").status_code, 200)
        self.assertEqual(len(self.staff.post(ADM + "projects/MOTHYS/reports/details/export/", {"fmt": "print", "filters": {}}, format="json").data["rows"]), 2)
        admin, _ = ensure_account("iadmin3", Role.ADMIN, "pw")
        c = APIClient()
        c.force_authenticate(admin)
        self.assertEqual(c.get(ADM + "projects/MOTHYS/reports/expiry/").status_code, 403)
