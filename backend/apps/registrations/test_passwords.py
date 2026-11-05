"""Centralized customer passwords: hashing, status display, change/reset rules, lockout, and the authorized reset / legacy link."""
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AdminPermission

from . import services
from .models import LicenseAuditLog, ProjectClient, ProjectCustomer
from .portal_views import hash_key
from .test_portal import BrokenBackend, REST, make_customer

PRN, GSTR = "prn-auto-print", "gstr2tally"
KEY = "nxk_prn_pw"
SERIAL = "712345678"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST,
    BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True,
)
class CustomerPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_initial_users", verbosity=0)
        ProjectClient.objects.create(project=PRN, key_hash=hash_key(KEY), reset_url="https://prn.example/reset")
        self.app = APIClient()
        self.staff = APIClient(enforce_csrf_checks=True)
        self.customer = make_customer(PRN, SERIAL, "ravi@example.com", password="Temp#Pass1")

    def portal(self, path, data=None, token=None, method="post"):
        h = {"HTTP_X_PROJECT_KEY": KEY, **({"HTTP_X_CUSTOMER_TOKEN": token} if token else {})}
        return getattr(self.app, method)(f"/api/portal/{PRN}/{path}", data or {}, format="json", **h)

    def signin(self, password, serial=SERIAL):
        return self.portal("login/", {"username": serial, "password": password})

    @property
    def csrf(self):
        self.staff.get("/api/auth/csrf/")
        return {"HTTP_X_CSRFTOKEN": self.staff.cookies["nexus_csrftoken"].value}

    def staff_login(self, name):
        self.staff.post("/api/auth/login/", {"username": name, "password": name}, format="json", **self.csrf)

    def detail(self):
        return self.staff.get(f"/api/projects/{PRN}/customers/{self.customer.pk}/").json()

    def reset_url(self, project=PRN):
        return f"/api/projects/{project}/customers/{self.customer.pk}/reset-password/"

    # ---- status display ----------------------------------------------------------------
    def test_password_status_follows_the_customers_own_change_and_never_leaks_the_secret(self):
        self.staff_login("Superadmin")
        d = self.detail()
        self.assertEqual((d["password_status"], d["password_changed_at"]), ("temporary", None))
        token = self.signin("Temp#Pass1").json()["token"]
        self.assertEqual(
            self.portal("change-password/", {"current_password": "Temp#Pass1", "new_password": "Brand#New42x"}, token=token).status_code, 200
        )
        d = self.detail()
        self.assertEqual(d["password_status"], "customer_set")
        self.assertIsNotNone(d["password_changed_at"])
        blob = str(d) + str(self.staff.get(f"/api/projects/{PRN}/customers/").json())
        self.assertNotIn("Brand#New42x", blob)
        self.assertNotIn("pbkdf2", blob)
        self.assertNotIn("password_hash", blob)

    def test_legacy_row_without_a_recorded_date_is_labelled_honestly(self):
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(must_change_password=False)
        self.customer.refresh_from_db()
        self.assertEqual(services.password_status(self.customer)[0], "not_recorded")

    # ---- change / reset rules ----------------------------------------------------------
    def test_password_is_only_stored_hashed_and_new_logins_use_the_changed_one(self):
        token = self.signin("Temp#Pass1").json()["token"]
        self.portal("change-password/", {"current_password": "Temp#Pass1", "new_password": "Brand#New42x"}, token=token)
        self.customer.refresh_from_db()
        self.assertNotIn("Brand#New42x", self.customer.password_hash)
        self.assertTrue(self.customer.password_hash.startswith(("pbkdf2_", "argon2", "bcrypt", "scrypt")))
        self.assertEqual(self.signin("Temp#Pass1").status_code, 401)
        self.assertEqual(self.signin("Brand#New42x").status_code, 200)

    def test_weak_or_unchanged_passwords_are_rejected_and_nothing_changes(self):
        token = self.signin("Temp#Pass1").json()["token"]
        r = self.portal("change-password/", {"current_password": "Temp#Pass1", "new_password": ""}, token=token)
        self.assertEqual(r.status_code, 400)  # empty is the only rejected password; no length or complexity rules
        r = self.portal("change-password/", {"current_password": "Temp#Pass1", "new_password": "TEMP#PASS1"}, token=token)
        self.assertEqual(r.json()["code"], "same_password")  # same text, case-insensitively
        self.assertEqual(self.signin("Temp#Pass1").status_code, 200)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.password_changed_at)

    def test_emailed_reset_link_enforces_the_same_rules(self):
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(must_change_password=False)
        self.portal("forgot-password/", {"identifier": SERIAL})
        link = mail.outbox[-1].body.split("?token=")[1].split()[0]
        self.assertEqual(self.portal("reset-password/", {"token": link, "new_password": ""}).status_code, 400)
        self.assertEqual(self.portal("reset-password/", {"token": link, "new_password": "Fresh#Pass88"}).status_code, 200)  # link survived the bad try
        self.assertEqual(self.signin("Fresh#Pass88").status_code, 200)
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.password_changed_at)

    # ---- lockout -----------------------------------------------------------------------
    @override_settings(CUSTOMER_MAX_FAILED_LOGINS=3, CUSTOMER_LOCKOUT_MINUTES=15)
    def test_account_locks_after_repeated_failures_and_a_reset_clears_it(self):
        for _ in range(3):
            self.assertEqual(self.signin("wrong").status_code, 401)
        r = self.signin("Temp#Pass1")  # even the right password is refused while locked
        self.assertEqual((r.status_code, r.json()["code"]), (429, "account_locked"))
        self.assertTrue(LicenseAuditLog.objects.filter(action="CUSTOMER_LOCKED").exists())
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(locked_until=None)
        self.assertEqual(self.signin("Temp#Pass1").status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.failed_logins, 0)
        self.assertEqual(self.signin("wrong", serial="799999999").status_code, 401)  # unknown id answers the same

    # ---- Super Admin reset / legacy link -----------------------------------------------
    @override_settings(ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE=True)
    def test_superadmin_reset_emails_a_new_temporary_password_and_kills_old_sessions(self):
        old = self.signin("Temp#Pass1").json()["token"]
        self.staff_login("Superadmin")
        r = self.staff.post(self.reset_url(), {"legacy_reference": "PRNPRINT local user 17"}, format="json", **self.csrf)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["email_sent"])
        self.assertNotIn("temporary_password", body)
        self.assertEqual(body["password_status"], "temporary")
        temp = mail.outbox[-1].body.split("Temporary password: ")[1].split()[0]
        self.assertEqual(mail.outbox[-1].to, ["ravi@example.com"])
        self.assertEqual(self.portal("account/", token=old, method="get").status_code, 401)
        self.assertEqual(self.signin("Temp#Pass1").status_code, 401)
        s = self.signin(temp).json()
        self.assertTrue(s["must_change_password"])
        actions = set(LicenseAuditLog.objects.filter(customer=self.customer).values_list("action", flat=True))
        self.assertTrue({"PASSWORD_RESET_BY_ADMIN", "LEGACY_ACCOUNT_LINKED"} <= actions)
        # license data is untouched
        before = ProjectCustomer.objects.get(pk=self.customer.pk)
        self.assertEqual((before.expires_at, before.status, before.serial_number), (self.customer.expires_at, "ACTIVE", SERIAL))

    def test_reset_email_failure_returns_the_password_once(self):
        self.staff_login("Superadmin")
        with override_settings(EMAIL_BACKEND="apps.registrations.test_portal.BrokenBackend"):
            body = self.staff.post(self.reset_url(), {}, format="json", **self.csrf).json()
        self.assertFalse(body["email_sent"])
        self.assertEqual(self.signin(body["temporary_password"]).status_code, 200)

    def test_only_superadmin_may_reset_and_only_inside_the_project(self):
        self.assertEqual(self.staff.post(self.reset_url(), {}, format="json").status_code, 401)
        AdminPermission.objects.update_or_create(action="edit", defaults={"name": "Edit Access", "enabled": True})
        self.staff_login("Admin")
        self.assertEqual(self.staff.post(self.reset_url(), {}, format="json", **self.csrf).status_code, 403)
        self.staff_login("Superadmin")
        self.assertEqual(self.staff.post(self.reset_url(GSTR), {}, format="json", **self.csrf).status_code, 404)
        self.assertEqual(self.signin("Temp#Pass1").status_code, 200)  # nothing changed

    # ---- Superadmin credentials stay administrative ------------------------------------
    def test_staff_credentials_do_not_work_as_customer_credentials(self):
        for name in ("Superadmin", "Admin"):
            self.assertEqual(self.signin(name, serial=name).status_code, 401)
        self.assertFalse(ProjectCustomer.objects.filter(user_id__in=["Superadmin", "Admin"]).exists())
        # ...and a customer's password is no staff login
        r = self.staff.post("/api/auth/login/", {"username": SERIAL, "password": "Temp#Pass1"}, format="json", **self.csrf)
        self.assertNotEqual(r.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username=SERIAL).exists())

    def test_same_serial_in_another_project_is_a_separate_account(self):
        make_customer(GSTR, SERIAL, "other@example.com", password="Other#Pass1", activation_key_hash="g" * 64)
        self.assertEqual(self.signin("Other#Pass1").status_code, 401)  # PRN key cannot reach the GSTR account
        self.assertEqual(self.signin("Temp#Pass1").status_code, 200)
