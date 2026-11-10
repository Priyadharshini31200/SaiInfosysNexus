"""End-to-end Barcode Auto Print flow: sign-up -> Super Admin notified -> open -> approve -> credentials emailed -> sign in -> license."""
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AdminPermission

from .models import LicenseAuditLog, ProjectClient, ProjectCustomer, RegistrationRequest
from .portal_views import hash_key
from .test_portal import REST

PRN, GSTR = "prn-auto-print", "gstr2tally"
KEY = "nxk_prn_flow"
R = f"/api/projects/{PRN}/registration-requests/"
BROKEN = "apps.registrations.test_portal.BrokenBackend"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST,
    BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True,
)
class PrnFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_initial_users", verbosity=0)
        get_user_model().objects.filter(username="Superadmin").update(email="boss@nexus.test")
        ProjectClient.objects.create(project=PRN, key_hash=hash_key(KEY), reset_url="https://prn.example/reset")
        self.app = APIClient()  # the Barcode Auto Print backend
        self.staff = APIClient(enforce_csrf_checks=True)

    def portal(self, path, data=None, token=None, method="post"):
        h = {"HTTP_X_PROJECT_KEY": KEY, **({"HTTP_X_CUSTOMER_TOKEN": token} if token else {})}
        return getattr(self.app, method)(f"/api/portal/{PRN}/{path}", data or {}, format="json", **h)

    @property
    def csrf(self):
        self.staff.get("/api/auth/csrf/")
        return {"HTTP_X_CSRFTOKEN": self.staff.cookies["nexus_csrftoken"].value}

    def login(self, name):
        self.staff.post("/api/auth/login/", {"username": name, "password": name}, format="json", **self.csrf)

    def approve(self, rid, data):
        return self.staff.post(f"{R}{rid}/approve/", data, format="json", **self.csrf)

    def signup(self, email="Ravi@Example.com"):
        return self.portal("register-request/", {"name": "Ravi", "contact_number": "9876543210", "email": email})

    def test_full_flow(self):
        # 1. Sign-up stores a pending request, grants nothing, and notifies the Super Admin.
        r = self.signup()
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        rid = r.json()["id"]
        self.assertEqual(ProjectCustomer.objects.count(), 0)
        self.assertEqual([m.to for m in mail.outbox], [["boss@nexus.test"]])
        self.assertIn("Ravi", mail.outbox[0].body)
        mail.outbox.clear()

        # 2. Super Admin lists and opens it.
        self.login("Superadmin")
        self.assertEqual([x["id"] for x in self.staff.get(R).json()["results"]], [rid])
        detail = self.staff.get(f"{R}{rid}/").json()
        self.assertEqual((detail["email"], detail["status"]), ("ravi@example.com", "pending"))

        # 3. Approve: account + license + generated serial (= User ID) + temporary password, credentials emailed.
        r = self.approve(rid, {"license_type": "TRIAL", "trial_days": 5})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertTrue(body["email_sent"])
        self.assertNotIn("temporary_password", body)
        self.assertRegex(body["serial_number"], r"^7[0-9]{8}$")
        self.assertEqual(body["user_id"], body["serial_number"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ravi@example.com"])
        self.assertIn(f"User ID: {body['user_id']}", mail.outbox[0].body)
        temp = mail.outbox[0].body.split("Password: ")[1].split()[0]
        req = RegistrationRequest.objects.get(pk=rid)
        self.assertEqual((req.status, req.customer_id), ("PROCESSED", body["id"]))
        self.assertEqual(self.staff.get(R).json()["results"], [])
        self.assertTrue(LicenseAuditLog.objects.filter(project=PRN, action="REQUEST_PROCESSED").exists())

        # 4. First sign-in with the emailed credentials goes straight to the licensed dashboard; changing the password is voluntary.
        s = self.portal("login/", {"username": body["user_id"], "password": temp}).json()
        self.assertEqual(self.portal("license/status/", token=s["token"], method="get").status_code, 200)
        self.portal("change-password/", {"current_password": temp, "new_password": "My#NewPass9"}, token=s["token"])
        s = self.portal("login/", {"username": body["user_id"], "password": "My#NewPass9"}).json()
        self.assertFalse(s["must_change_password"])
        st = self.portal("license/status/", token=s["token"], method="get")
        self.assertEqual(st.status_code, 200)
        self.assertEqual((st.json()["authorized"], st.json()["state"], st.json()["license_type"]), (True, "active", "TRIAL"))

        # 5. A request cannot be approved twice.
        self.assertEqual(self.approve(rid, {"license_type": "LICENSED"}).status_code, 409)
        self.assertEqual(ProjectCustomer.objects.count(), 1)

    def test_email_failure_returns_the_temporary_password_once(self):
        rid = self.signup().json()["id"]
        self.login("Superadmin")
        with override_settings(EMAIL_BACKEND=BROKEN):
            r = self.approve(rid, {"license_type": "LICENSED"})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertFalse(body["email_sent"])
        self.assertEqual(self.portal("login/", {"username": body["user_id"], "password": body["temporary_password"]}).status_code, 200)
        self.assertEqual(body["license_type"], "LICENSED")
        self.assertIsNone(body["expires_at"])

    def test_supplied_serial_and_validation(self):
        rid = self.signup().json()["id"]
        self.login("Superadmin")
        self.assertEqual(self.approve(rid, {"license_type": "TRIAL", "serial_number": "123"}).status_code, 400)
        self.assertEqual(self.approve(rid, {"license_type": "WEEKLY"}).status_code, 400)  # license_type is optional (default Trial) but must be a real package
        ok = self.approve(rid, {"license_type": "TRIAL", "serial_number": "712345678", "temporary_password": "Chosen#Pw1"})
        self.assertEqual((ok.status_code, ok.json()["user_id"]), (201, "712345678"))
        self.assertEqual(self.portal("login/", {"username": "712345678", "password": "Chosen#Pw1"}).status_code, 200)

    def test_permissions_and_project_isolation(self):
        rid = self.signup().json()["id"]
        self.assertEqual(self.staff.post(f"{R}{rid}/approve/", {"license_type": "TRIAL"}, format="json").status_code, 401)
        AdminPermission.objects.update_or_create(action="entry", defaults={"name": "Entry Access", "enabled": False})
        self.login("Admin")
        self.assertEqual(self.approve(rid, {"license_type": "TRIAL"}).status_code, 403)
        self.assertEqual(self.staff.get(f"{R}{rid}/").status_code, 403)
        AdminPermission.objects.filter(action="entry").update(enabled=True)
        other = f"/api/projects/{GSTR}/registration-requests/{rid}/"
        self.assertEqual(self.staff.get(other).status_code, 404)
        self.assertEqual(self.staff.post(other + "approve/", {"license_type": "TRIAL"}, format="json", **self.csrf).status_code, 404)
        self.assertEqual(self.approve(rid, {"license_type": "TRIAL"}).status_code, 201)

    def test_notification_failure_does_not_fail_signup(self):
        with override_settings(EMAIL_BACKEND=BROKEN):
            self.assertEqual(self.signup().status_code, 201)
        self.assertEqual(RegistrationRequest.objects.count(), 1)

    def test_extra_notify_addresses(self):
        with override_settings(NEW_REQUEST_NOTIFY_EMAILS=["ops@nexus.test"]):
            self.signup()
        self.assertCountEqual([m.to[0] for m in mail.outbox], ["boss@nexus.test", "ops@nexus.test"])
