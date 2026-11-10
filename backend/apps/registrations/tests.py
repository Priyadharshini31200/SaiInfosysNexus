import itertools
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AdminPermission

from . import services
from .models import LicenseAuditLog, ProjectCustomer, RegistrationRequest

P = "/api/projects/gstr2tally/"


_EMAILS = itertools.count(1)


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class RegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_initial_users", verbosity=0)
        self.client = APIClient(enforce_csrf_checks=True)

    def _csrf(self):
        self.client.get("/api/auth/csrf/")
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["nexus_csrftoken"].value}

    def login(self, name):
        self.client.post("/api/auth/login/", {"username": name, "password": name}, format="json", **self._csrf())

    def post(self, url, data=None):
        return self.client.post(url, data or {}, format="json", **self._csrf())

    def form(self, **over):
        key = self.post(P + "activation-key/").json()["activation_key"]
        data = {
            "name": "Ravi", "company_name": "Ravi Traders", "license_type": "TRIAL", "activation_key": key,
            "password": "Secret#123", "contact_number": "9876543210", "email": f"ravi{next(_EMAILS)}@example.com",
            "address": "12 Main Road, Chennai", "gstin": "33AFHPM6103Q1Z8", "serial_number": "735149529",
        }
        data.update(over)
        return data

    def register(self, **over):
        return self.post(P + "customers/", self.form(**over))

    def toggle(self, name, enabled):
        pk = AdminPermission.objects.get(name=name).pk
        return self.client.patch(f"/api/config/admin-permissions/{pk}/", {"enabled": enabled}, format="json", **self._csrf())

    # ---- registration
    def test_register_trial_defaults_to_3_days_and_hashes_password(self):
        self.login("Superadmin")
        r = self.register()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["license_type"], body["status_label"], body["trial_days"]), ("TRIAL", "Trial Active", 3))
        self.assertNotIn("password", str(body).lower().replace("password_hash", "").replace("password_status_label", "").replace("password_status", "").replace("password_changed_at", ""))
        row = ProjectCustomer.objects.get()
        self.assertNotEqual(row.password_hash, "Secret#123")
        self.assertTrue(row.password_hash.startswith(("pbkdf2_", "argon2", "bcrypt", "scrypt")))
        self.assertAlmostEqual((row.expires_at - row.activated_at).total_seconds(), 3 * 86400, delta=5)
        self.assertEqual(body["activation_key_hint"][-4:], row.activation_key_suffix)

    def test_licensed_has_no_expiry_and_custom_trial_days(self):
        self.login("Superadmin")
        lic = self.register(license_type="LICENSED", serial_number="700000001").json()
        self.assertEqual((lic["status_label"], lic["expires_at"], lic["trial_days"]), ("Lifetime Active", None, None))
        trial = self.register(serial_number="700000002", trial_days=10).json()
        self.assertEqual(trial["trial_days"], 10)

    def test_keys_are_unique_single_use_and_not_stored_in_clear(self):
        self.login("Superadmin")
        keys = {self.post(P + "activation-key/").json()["activation_key"] for _ in range(5)}
        self.assertEqual(len(keys), 5)
        data = self.form()
        self.assertEqual(self.post(P + "customers/", data).status_code, 201)
        again = self.post(P + "customers/", {**data, "serial_number": ""})
        self.assertEqual(again.status_code, 400)
        self.assertIn("activation_key", again.json()["errors"])
        self.assertFalse(ProjectCustomer.objects.filter(activation_key_hash=data["activation_key"]).exists())
        # a key generated for one project is not valid for another
        other = self.post("/api/projects/prn-auto-print/customers/", {**self.form(), "activation_key": self.post(P + "activation-key/").json()["activation_key"]})
        self.assertEqual(other.status_code, 400)

    def test_user_id_is_serial_and_password_is_case_insensitive(self):
        self.login("Superadmin")
        r = self.register(user_id="ignored", password="Admin123")
        self.assertEqual(r.status_code, 201, r.content)
        row = ProjectCustomer.objects.get()
        self.assertEqual(row.user_id, row.serial_number)
        self.assertTrue(services.verify_password(row, "admin123"))
        self.assertTrue(services.verify_password(row, "ADMIN123"))
        self.assertFalse(services.verify_password(row, "admin124"))
        self.assertNotIn("admin123", row.password_hash.lower())
        self.assertEqual(self.register(serial_number="700000009", password="a").status_code, 201)  # no complexity rules

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_processing_a_request_registers_emails_credentials_and_marks_it_processed(self):
        self.login("Superadmin")
        req = RegistrationRequest.objects.create(project="gstr2tally", name="Asha", contact_number="9876501234", email="asha@example.com")
        pending = self.client.get(P + "registration-requests/").json()["results"]
        self.assertEqual([r["email"] for r in pending], ["asha@example.com"])
        r = self.register(email="asha@example.com", request_id=req.id, password="Temp#Pass1")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["email_sent"])
        row = ProjectCustomer.objects.get()
        self.assertTrue(row.must_change_password)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(row.user_id, mail.outbox[0].body)
        self.assertIn("Temp#Pass1", mail.outbox[0].body)  # emailed once, never stored
        self.assertNotIn("Temp#Pass1", row.password_hash)
        req.refresh_from_db()
        self.assertEqual((req.status, req.customer_id), ("PROCESSED", row.id))
        self.assertEqual(self.client.get(P + "registration-requests/").json()["results"], [])
        self.assertIn("REQUEST_PROCESSED", list(LicenseAuditLog.objects.values_list("action", flat=True)))
        # a processed request cannot be used again, and a taken email cannot be registered twice
        self.assertIn("request_id", self.register(request_id=req.id).json()["errors"])
        self.assertIn("email", self.register(email="ASHA@example.com").json()["errors"])

    def test_registration_survives_a_mail_failure(self):
        self.login("Superadmin")
        with override_settings(EMAIL_BACKEND="apps.registrations.test_portal.BrokenBackend"):
            r = self.register()
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.json()["email_sent"])
        self.assertEqual(ProjectCustomer.objects.count(), 1)

    def test_registration_requests_need_entry_permission(self):
        self.login("Admin")
        self.assertEqual(self.client.get(P + "registration-requests/").status_code, 200)  # Entry is on by default
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Superadmin")
        self.toggle("Entry Access", False)
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Admin")
        self.assertEqual(self.client.get(P + "registration-requests/").status_code, 403)
        self.client.post("/api/auth/logout/", **self._csrf())
        self.assertEqual(self.client.get(P + "registration-requests/").status_code, 401)

    # ---- Admin <-> Superadmin: one shared record set, tracked changes, polling fingerprint
    def switch_to(self, name):
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login(name)

    def test_admin_changes_are_seen_by_superadmin_with_who_and_when(self):
        self.login("Superadmin")
        self.post("/api/config/admin-permissions/", {"name": "View Access"})
        self.toggle("Edit Access", True)
        self.toggle("Delete Access", True)
        self.switch_to("Superadmin")
        before = self.client.get(P + "sync/").json()["version"]
        self.switch_to("Admin")
        cid = self.register(email="by.admin@example.com").json()["id"]
        url = f"{P}customers/{cid}/"
        self.assertEqual(self.client.patch(url, {"name": "Renamed"}, format="json", **self._csrf()).status_code, 200)
        self.assertEqual(self.post(url + "extend-trial/", {"days": 2}).status_code, 200)
        self.assertEqual(self.post(url + "cancel-trial/").status_code, 200)
        # the Admin's own polling fingerprint moved, and Superadmin sees the same record, no copy
        self.switch_to("Superadmin")
        self.assertNotEqual(self.client.get(P + "sync/").json()["version"], before)
        rows = self.client.get(P + "customers/").json()["results"]
        self.assertEqual([(r["name"], r["created_by"], r["updated_by"], r["status_label"]) for r in rows], [("Renamed", "Admin", "Admin", "Trial Cancelled")])
        self.assertEqual(ProjectCustomer.objects.count(), 1)
        # a Superadmin change is attributed to Superadmin and visible to Admin
        self.assertEqual(self.client.patch(url, {"name": "By Super"}, format="json", **self._csrf()).status_code, 200)
        self.switch_to("Admin")
        mine = self.client.get(P + "customers/").json()["results"][0]
        self.assertEqual((mine["name"], mine["created_by"], mine["updated_by"]), ("By Super", "Admin", "Superadmin"))
        # activity trail: Superadmin only, newest first, each with actor, action and time
        self.assertEqual(self.client.get(P + "activity/").status_code, 403)
        self.switch_to("Superadmin")
        log = self.client.get(P + "activity/").json()["results"]
        self.assertEqual([(e["actor"], e["action"]) for e in log if e["customer_id"] == cid][:2], [("Superadmin", "EDITED"), ("Admin", "TRIAL_CANCELLED")])
        self.assertTrue(all(e["created_at"] for e in log))
        self.assertIn("ADMIN", {e["actor_role"] for e in log})
        only = self.client.get(P + f"activity/?customer={cid}").json()["results"]
        self.assertTrue(all(e["customer_id"] == cid for e in only))
        self.assertEqual(self.client.get(P + "activity/?customer=x").status_code, 400)

    def test_sync_fingerprint_is_per_project_and_permission_checked(self):
        self.login("Superadmin")
        gstr = self.client.get(P + "sync/").json()["version"]
        prn = self.client.get("/api/projects/prn-auto-print/sync/").json()["version"]
        self.register()
        self.assertNotEqual(self.client.get(P + "sync/").json()["version"], gstr)
        self.assertEqual(self.client.get("/api/projects/prn-auto-print/sync/").json()["version"], prn)  # other project untouched
        self.assertEqual(self.client.get("/api/dashboard/sync/").status_code, 200)
        self.switch_to("Admin")
        self.assertEqual(self.client.get("/api/dashboard/sync/").status_code, 403)  # dashboard is Super Admin only
        self.client.post("/api/auth/logout/", **self._csrf())
        self.assertEqual(self.client.get(P + "sync/").status_code, 401)

    def test_generated_serial_number(self):
        self.login("Superadmin")
        r = self.post(P + "serial-number/")
        self.assertEqual(r.status_code, 201)
        serial = r.json()["serial_number"]
        self.assertRegex(serial, r"^7[0-9]{8}$")
        self.assertEqual(self.register(serial_number=serial).status_code, 201)
        self.assertNotEqual(self.post(P + "serial-number/").json()["serial_number"], serial)

    def test_validation(self):
        self.login("Superadmin")
        for field, bad in (("contact_number", "abc"), ("gstin", "123"), ("serial_number", ""), ("serial_number", "635149529"), ("serial_number", "73514952"), ("serial_number", "7351495291"), ("serial_number", "73514952a"), ("password", ""), ("name", ""), ("trial_days", 0)):
            r = self.register(**{field: bad})
            self.assertEqual(r.status_code, 400, field)
            self.assertIn(field, r.json()["errors"])
        self.assertEqual(self.register().status_code, 201)
        first = ProjectCustomer.objects.get(serial_number="735149529")
        before = (first.pk, first.password_hash, first.activation_key_hash, first.expires_at)
        # a number that is already taken is never refused and never reused: a new unique one is issued, the first customer is untouched
        dup = self.register()
        self.assertEqual(dup.status_code, 201, dup.content)
        self.assertNotEqual(dup.json()["serial_number"], "735149529")
        self.assertEqual(dup.json()["serial_reassigned_from"], "735149529")
        self.assertEqual(dup.json()["user_id"], dup.json()["serial_number"])
        first.refresh_from_db()
        self.assertEqual((first.pk, first.password_hash, first.activation_key_hash, first.expires_at), before)
        # serial numbers are unique across every product, so the other project gets a different number too
        key = self.post("/api/projects/prn-auto-print/activation-key/").json()["activation_key"]
        ok = self.post("/api/projects/prn-auto-print/customers/", {**self.form(), "activation_key": key})
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertNotIn(ok.json()["serial_number"], ("735149529", dup.json()["serial_number"]))
        self.assertEqual(self.client.get(P + "customers/").json()["results"].__len__(), 2)

    # ---- license management
    def test_extend_cancel_inactivate_flow_and_audit(self):
        self.login("Superadmin")
        cid = self.register().json()["id"]
        url = f"{P}customers/{cid}/"
        before = ProjectCustomer.objects.get(pk=cid).expires_at
        r = self.post(url + "extend-trial/", {"days": 4})
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual((ProjectCustomer.objects.get(pk=cid).expires_at - before).total_seconds(), 4 * 86400, delta=5)
        self.assertEqual(self.post(url + "extend-trial/", {"days": 0}).status_code, 400)
        self.assertEqual(self.post(url + "cancel-trial/").json()["status_label"], "Trial Cancelled")
        self.assertEqual(self.post(url + "cancel-trial/").status_code, 409)
        self.assertEqual(self.post(url + "extend-trial/", {"days": 1}).status_code, 409)
        self.assertEqual(self.post(url + "inactivate/").json()["status_label"], "Inactive")
        self.assertEqual(self.post(url + "inactivate/").status_code, 409)
        actions = list(LicenseAuditLog.objects.order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["KEY_GENERATED", "REGISTERED", "TRIAL_EXTENDED", "TRIAL_CANCELLED", "LICENSE_INACTIVATED"])

    def test_expired_trial_can_be_extended_from_now(self):
        self.login("Superadmin")
        cid = self.register().json()["id"]
        ProjectCustomer.objects.filter(pk=cid).update(expires_at=timezone.now() - timedelta(days=2))
        self.assertEqual(self.client.get(f"{P}customers/{cid}/").json()["status_label"], "Trial Expired")
        self.assertEqual(self.post(f"{P}customers/{cid}/cancel-trial/").status_code, 409)  # not active any more
        r = self.post(f"{P}customers/{cid}/extend-trial/", {"days": 5}).json()
        self.assertEqual(r["status_label"], "Trial Active")
        left = ProjectCustomer.objects.get(pk=cid).expires_at - timezone.now()
        self.assertAlmostEqual(left.total_seconds(), 5 * 86400, delta=10)

    def test_licensed_customer_cannot_use_trial_actions(self):
        self.login("Superadmin")
        cid = self.register(license_type="LICENSED").json()["id"]
        self.assertEqual(self.post(f"{P}customers/{cid}/extend-trial/", {"days": 3}).status_code, 409)
        self.assertEqual(self.post(f"{P}customers/{cid}/cancel-trial/").status_code, 409)
        self.assertEqual(self.post(f"{P}customers/{cid}/inactivate/").status_code, 200)

    def test_edit_changes_profile_only(self):
        self.login("Superadmin")
        cid = self.register().json()["id"]
        r = self.client.patch(f"{P}customers/{cid}/", {"name": "Ravi K", "license_type": "LICENSED", "user_id": "hacker"}, format="json", **self._csrf())
        self.assertEqual(r.status_code, 200)
        row = ProjectCustomer.objects.get(pk=cid)
        self.assertEqual((row.name, row.license_type, row.user_id), ("Ravi K", "TRIAL", "735149529"))

    def test_customer_of_other_project_is_not_reachable(self):
        self.login("Superadmin")
        cid = self.register().json()["id"]
        self.assertEqual(self.client.get(f"/api/projects/prn-auto-print/customers/{cid}/").status_code, 404)
        self.assertEqual(self.client.get("/api/projects/nope/customers/").status_code, 404)

    # ---- access control
    def test_anonymous_gets_401(self):
        self.assertEqual(self.client.get(P + "customers/").status_code, 401)

    def test_admin_only_gets_enabled_actions(self):
        self.login("Superadmin")
        cid = self.register().json()["id"]
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Admin")
        url = f"{P}customers/{cid}/"
        # defaults: Entry only
        self.assertEqual(self.client.get(P + "customers/").status_code, 403)
        self.assertEqual(self.post(P + "activation-key/").status_code, 201)
        self.assertEqual(self.client.patch(url, {"name": "X"}, format="json", **self._csrf()).status_code, 403)
        self.assertEqual(self.post(url + "extend-trial/", {"days": 2}).status_code, 403)
        self.assertEqual(self.post(url + "cancel-trial/").status_code, 403)
        self.assertEqual(self.post(url + "inactivate/").status_code, 403)
        # Superadmin switches Edit, Delete (and a View heading) ON
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Superadmin")
        self.post("/api/config/admin-permissions/", {"name": "View Access"})
        self.toggle("Edit Access", True)
        self.toggle("Delete Access", True)
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Admin")
        self.assertEqual(self.client.get(P + "customers/").status_code, 200)
        self.assertEqual(self.client.patch(url, {"name": "X"}, format="json", **self._csrf()).status_code, 200)
        self.assertEqual(self.post(url + "extend-trial/", {"days": 2}).status_code, 200)
        self.assertEqual(self.post(url + "cancel-trial/").status_code, 200)
        # Entry OFF blocks registration
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Superadmin")
        self.toggle("Entry Access", False)
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Admin")
        self.assertEqual(self.post(P + "activation-key/").status_code, 403)
        self.assertEqual(self.post(P + "customers/", {"name": "X"}).status_code, 403)

    def test_permission_changes_are_audited(self):
        self.login("Superadmin")
        self.post("/api/config/admin-permissions/", {"name": "Approval Access"})
        self.toggle("Edit Access", True)
        self.toggle("Edit Access", False)
        actions = list(LicenseAuditLog.objects.order_by("id").values_list("action", "detail"))
        self.assertEqual(actions, [("PERMISSION_ADDED", "Approval Access (ON)"), ("PERMISSION_ON", "Edit Access"), ("PERMISSION_OFF", "Edit Access")])

    def test_authorization_follows_license_state(self):
        from . import services

        self.login("Superadmin")
        cid = self.register().json()["id"]
        row = ProjectCustomer.objects.get(pk=cid)
        self.assertTrue(services.is_authorized(row))
        row.expires_at = timezone.now() - timedelta(minutes=1)
        self.assertFalse(services.is_authorized(row))  # expired trial
        row.expires_at = timezone.now() + timedelta(days=1)
        row.status = ProjectCustomer.TRIAL_CANCELLED
        self.assertFalse(services.is_authorized(row))  # cancelled
        row.status = ProjectCustomer.INACTIVE
        self.assertFalse(services.is_authorized(row))  # inactive
        row.status, row.license_type, row.expires_at = ProjectCustomer.ACTIVE, ProjectCustomer.LICENSED, None
        self.assertTrue(services.is_authorized(row))  # full license never expires by itself

    def test_edit_only_admin_can_load_the_record(self):
        self.login("Superadmin")
        cid = self.register().json()["id"]
        self.toggle("Edit Access", True)
        self.client.post("/api/auth/logout/", **self._csrf())
        self.login("Admin")
        self.assertEqual(self.client.get(P + "customers/").status_code, 403)  # no View
        self.assertEqual(self.client.get(f"{P}customers/{cid}/").status_code, 200)  # Edit may load it

    def test_lists_never_mix_projects(self):
        self.login("Superadmin")
        self.register()
        key = self.post("/api/projects/prn-auto-print/activation-key/").json()["activation_key"]
        self.post("/api/projects/prn-auto-print/customers/", {**self.form(), "activation_key": key, "serial_number": "700000003"})
        gstr = self.client.get(P + "customers/").json()["results"]
        prn = self.client.get("/api/projects/prn-auto-print/customers/").json()["results"]
        self.assertEqual([c["user_id"] for c in gstr], ["735149529"])
        self.assertEqual([c["user_id"] for c in prn], ["700000003"])
