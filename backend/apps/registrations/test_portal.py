from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import portal, services
from .models import ProjectClient, ProjectCustomer, RegistrationRequest
from .portal_views import hash_key

GSTR, PRN = "gstr2tally", "prn-auto-print"
KEY_G, KEY_P = "nxk_gstr_test", "nxk_prn_test"

REST = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"login": "1000/min", "portal": "1000/min"}}


def make_customer(project, serial, email, password="Temp#Pass1", **over):
    now = timezone.now()
    data = dict(
        project=project, name="Ravi", company_name="Ravi Traders", license_type="TRIAL", user_id=serial,
        password_hash=services.hash_password(password), contact_number="9876543210", email=email, address="Chennai",
        gstin="33AFHPM6103Q1Z8", serial_number=serial, trial_days=3, activation_key_hash=serial * 5, activation_key_suffix="ABCD",
        activated_at=now, expires_at=now + timedelta(days=3), must_change_password=True,
    )
    data.update(over)
    return ProjectCustomer.objects.create(**data)


class BrokenBackend:
    """An SMTP server that is down."""

    def __init__(self, *args, **kwargs):
        pass

    def send_messages(self, messages):
        raise OSError("smtp down")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST)
class PortalTests(TestCase):
    def setUp(self):
        cache.clear()  # the throttle counters live in the cache; do not inherit another test's requests
        ProjectClient.objects.create(project=GSTR, key_hash=hash_key(KEY_G), reset_url="https://gstr.example/reset")
        ProjectClient.objects.create(project=PRN, key_hash=hash_key(KEY_P), reset_url="https://prn.example/reset")
        self.c = APIClient()

    def call(self, project, path, data=None, key=None, token=None, method="post"):
        headers = {"HTTP_X_PROJECT_KEY": key if key is not None else (KEY_G if project == GSTR else KEY_P)}
        if token:
            headers["HTTP_X_CUSTOMER_TOKEN"] = token
        return getattr(self.c, method)(f"/api/portal/{project}/{path}", data or {}, format="json", **headers)

    def login(self, serial="700000001", password="Temp#Pass1", project=GSTR):
        return self.call(project, "login/", {"username": serial, "password": password})

    def test_project_key_required_and_bound_to_its_project(self):
        body = {"username": "x", "password": "y"}
        self.assertEqual(self.call(GSTR, "login/", body, key="").status_code, 401)
        self.assertEqual(self.call(GSTR, "login/", body, key="wrong").status_code, 401)
        self.assertEqual(self.call(GSTR, "login/", body, key=KEY_P).status_code, 403)
        ProjectClient.objects.filter(project=GSTR).update(active=False)
        self.assertEqual(self.call(GSTR, "login/", body).status_code, 401)

    def test_signup_creates_pending_request_without_license_and_blocks_duplicates(self):
        body = {"name": "Asha", "contact_number": "9876501234", "email": "Asha@Example.com"}
        self.assertEqual(self.call(GSTR, "register-request/", body).status_code, 201)
        req = RegistrationRequest.objects.get()
        self.assertEqual((req.project, req.email, req.status), (GSTR, "asha@example.com", "PENDING"))
        self.assertEqual(ProjectCustomer.objects.count(), 0)  # nothing is activated
        self.assertEqual(self.call(GSTR, "register-request/", body).status_code, 409)
        self.assertEqual(self.call(PRN, "register-request/", body).status_code, 201)  # other project: separate
        self.assertEqual(self.call(GSTR, "register-request/", {**body, "email": "b@x.com", "contact_number": "123"}).status_code, 400)
        make_customer(GSTR, "700000001", "has@example.com")
        self.assertEqual(self.call(GSTR, "register-request/", {**body, "email": "HAS@example.com"}).status_code, 409)

    @override_settings(ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE=True)
    def test_login_forced_password_change_then_license_status(self):
        make_customer(GSTR, "700000001", "a@example.com")
        self.assertEqual(self.login(password="nope").status_code, 401)
        r = self.login(password="temp#PASS1")  # passwords are case-insensitive
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["must_change_password"])
        self.assertTrue(body["license"]["authorized"])
        self.assertNotIn("hash", str(body).lower())
        token = body["token"]
        blocked = self.call(GSTR, "license/status/", token=token, method="get")
        self.assertEqual((blocked.status_code, blocked.json()["code"]), (403, "password_change_required"))
        self.assertEqual(self.call(GSTR, "change-password/", {"current_password": "bad", "new_password": "x"}, token=token).status_code, 400)
        self.assertEqual(self.call(GSTR, "change-password/", {"current_password": "Temp#Pass1", "new_password": "Newer#Pass77"}, token=token).status_code, 200)
        self.assertEqual(self.call(GSTR, "license/status/", token=token, method="get").status_code, 401)  # old token died with the password
        token = self.login(password="newer#pass77").json()["token"]
        status = self.call(GSTR, "license/status/", token=token, method="get").json()
        self.assertEqual((status["authorized"], status["state"]), (True, "active"))
        self.assertIsNotNone(status["lease_until"])

    def test_license_states_block_access(self):
        c = make_customer(GSTR, "700000001", "a@example.com", must_change_password=False)
        token = self.login().json()["token"]
        self.assertTrue(self.call(GSTR, "license/status/", token=token, method="get").json()["authorized"])
        cases = (
            ("expired", {"expires_at": timezone.now() - timedelta(hours=1)}),
            ("cancelled", {"status": "TRIAL_CANCELLED"}),
            ("inactive", {"status": "INACTIVE"}),
        )
        for state, patch in cases:
            ProjectCustomer.objects.filter(pk=c.pk).update(status="ACTIVE", expires_at=timezone.now() + timedelta(days=2))
            ProjectCustomer.objects.filter(pk=c.pk).update(**patch)
            st = self.call(GSTR, "license/status/", token=token, method="get").json()
            self.assertEqual((st["authorized"], st["state"], st["lease_until"]), (False, state, None))

    def test_offline_lease_is_capped_by_expiry_and_lease_days(self):
        soon = timezone.now() + timedelta(days=2)
        c = make_customer(GSTR, "700000001", "a@example.com", expires_at=soon)
        self.assertEqual(portal.license_status(c)["lease_until"], soon)
        ProjectCustomer.objects.filter(pk=c.pk).update(license_type="LICENSED", expires_at=None)
        c.refresh_from_db()
        lease = portal.license_status(c)["lease_until"]
        self.assertAlmostEqual((lease - timezone.now()).total_seconds(), 7 * 86400, delta=60)

    def test_credentials_and_tokens_do_not_cross_projects(self):
        make_customer(GSTR, "700000001", "a@example.com")
        self.assertEqual(self.login(project=PRN).status_code, 401)
        token = self.login().json()["token"]
        self.assertEqual(self.call(PRN, "account/", token=token, method="get").status_code, 401)

    def test_username_recovery_and_password_reset_by_email(self):
        make_customer(GSTR, "700000001", "a@example.com", must_change_password=False)
        for email in ("a@example.com", "unknown@example.com"):
            self.assertEqual(self.call(GSTR, "recover-username/", {"email": email}).status_code, 200)  # same answer either way
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("700000001", mail.outbox[0].body)
        self.assertEqual(self.call(GSTR, "forgot-password/", {"identifier": "700000001"}).status_code, 200)
        link = mail.outbox[1].body.split("https://gstr.example/reset?token=")[1].split()[0]
        self.assertEqual(self.call(PRN, "reset-password/", {"token": link, "new_password": "Fresh#Pass88"}).status_code, 401)
        self.assertEqual(self.call(GSTR, "reset-password/", {"token": link, "new_password": "Fresh#Pass88"}).status_code, 200)
        self.assertEqual(self.call(GSTR, "reset-password/", {"token": link, "new_password": "Again#Pass99"}).status_code, 401)  # one use
        self.assertEqual(self.login(password="FRESH#PASS88").status_code, 200)
        self.assertEqual(self.call(GSTR, "reset-password/", {"token": "garbage", "new_password": "x"}).status_code, 401)

    def test_credentials_email_failure_is_reported_not_raised(self):
        c = make_customer(GSTR, "700000001", "a@example.com")
        self.assertTrue(portal.send_credentials(c, "Temp#Pass1", "ABCD-EFGH-JKLM-NPQR"))
        self.assertIn("ABCD-EFGH-JKLM-NPQR", mail.outbox[-1].body)
        self.assertIn("700000001", mail.outbox[-1].body)
        with override_settings(EMAIL_BACKEND="apps.registrations.test_portal.BrokenBackend"):
            self.assertFalse(portal.send_credentials(c, "Temp#Pass1", "ABCD-EFGH-JKLM-NPQR"))
            self.assertEqual(ProjectCustomer.objects.get(pk=c.pk).credentials_email_status, "failed")
