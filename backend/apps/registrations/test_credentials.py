"""Customer credentials: the email, its honest delivery status, activation with exactly the issued credentials, reissue."""
import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from .models import DeviceRegistration, LicenseAuditLog, ProjectCustomer, RegistrationRequest
from .test_device import E, FP, OTHER_FP, PRN, PUB, Device, verify_like_the_agent
from .test_packages import PackageBase

APPROVE = f"/api/projects/{PRN}/registration-requests/%d/approve/"
KEY_RE = re.compile(r"Activation Key: ([A-Z0-9-]+)")
PASSWORD_RE = re.compile(r"Password: (\S+)")
USER_RE = re.compile(r"User ID: (\S+)")
NEW_PASSWORD = "Brand#New9pass"


class CredentialsBase(PackageBase):
    def approve(self, kind="TRIAL", email="new.customer@example.com", project=PRN, **extra):
        req = RegistrationRequest.objects.create(project=project, name="Meena", contact_number="9876543210", email=email)
        r = self.s.post(f"/api/projects/{project}/registration-requests/{req.pk}/approve/", {"license_type": kind, **extra}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r

    def issued(self):
        """What the customer reads in the email: (user id, password, key)."""
        body = mail.outbox[-1].body
        return USER_RE.search(body).group(1), PASSWORD_RE.search(body).group(1), KEY_RE.search(body).group(1)

    def activate_with(self, serial, key, dev=None, **over):
        dev = dev or Device()
        body = {"serial_number": serial, "activation_key": key, "device_public_key": dev.public, "fingerprint": dev.fp,
                "device_name": "Counter PC", "app_version": "1.0.0", **over}
        r = self.c.post(E + "activate/", body, format="json")
        if r.status_code == 200:
            dev.device_id = r.json()["device_id"]
        return r, dev

    def sign_in(self, dev, serial, password):
        r = self.call(dev, "POST", "login/", {"username": serial, "password": password}, token=False)
        if r.status_code == 200:
            dev.token = r.json()["token"]
        return r


class CredentialsEmailTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()

    def test_approval_email_carries_everything_the_customer_needs_and_nothing_secret(self):
        r = self.approve("ONE_YEAR")
        body_json, msg = r.json(), mail.outbox[-1]
        self.assertEqual(msg.to, ["new.customer@example.com"])
        for needle in ("Meena", "Barcode Auto Print", "1 Year", f"User ID: {body_json['user_id']}", f"Serial Number: {body_json['serial_number']}",
                       "Password:", "Activation Key:", "Sign in with your User ID", "My Profile"):
            self.assertIn(needle, msg.body)
        self.assertEqual(body_json["user_id"], body_json["serial_number"])
        self.assertRegex(body_json["serial_number"], r"^7\d{8}$")
        customer = ProjectCustomer.objects.get(pk=body_json["id"])
        for secret in (customer.password_hash, customer.activation_key_hash):
            self.assertNotIn(secret, msg.body)
        # stored only hashed; the API answer does not repeat them when the email was accepted
        self.assertNotIn("temporary_password", body_json)
        self.assertNotIn("activation_key", body_json)
        self.assertNotIn(self.issued()[1], str(body_json))
        self.assertEqual((body_json["email_sent"], body_json["credentials_email_status"]), (True, "accepted"))
        self.assertIsNotNone(ProjectCustomer.objects.get(pk=customer.pk).credentials_emailed_at)

    def test_every_customer_gets_their_own_unique_credentials(self):
        a, b = self.approve(email="a@example.com").json(), self.approve(email="b@example.com").json()
        (ua, pa, ka), (ub, pb) = self.issued_for(a), self.issued_for(b)[:2]
        self.assertNotEqual((a["serial_number"], pa, ka), (b["serial_number"], pb, self.issued_for(b)[2]))
        self.assertNotEqual(pa, pb)

    def issued_for(self, body):
        msg = next(m for m in mail.outbox if f"User ID: {body['user_id']}\n" in m.body)
        return USER_RE.search(msg.body).group(1), PASSWORD_RE.search(msg.body).group(1), KEY_RE.search(msg.body).group(1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_a_development_console_backend_is_never_reported_as_delivered(self):
        r = self.approve().json()
        self.assertEqual((r["email_sent"], r["credentials_email_status"]), (False, "not_delivered"))
        self.assertIn("no mail server", r["credentials_email_status_label"])
        # nothing reached the customer, so the admin is handed both values once
        self.assertTrue(r["temporary_password"] and r["activation_key"])
        self.assertIsNone(ProjectCustomer.objects.get(pk=r["id"]).credentials_emailed_at)
        self.assertNotIn("temporary_password", self.s.get(f"/api/projects/{PRN}/customers/{r['id']}/").json())

    def test_a_mail_failure_is_recorded_and_reported_without_losing_the_customer(self):
        with override_settings(MAIL_RETRY_DELAY_SECONDS=0), patch("apps.registrations.mailer.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            r = self.approve().json()
        self.assertEqual((r["email_sent"], r["credentials_email_status"]), (False, "failed"))
        self.assertTrue(r["temporary_password"] and r["activation_key"])
        self.assertTrue(ProjectCustomer.objects.filter(pk=r["id"]).exists())
        self.assertEqual(self.s.get(f"/api/projects/{PRN}/customers/{r['id']}/").json()["credentials_email_status_label"], "Failed to send")

    def test_add_customer_form_emails_the_generated_key_and_password_too(self):
        key = self.s.post(f"/api/projects/{PRN}/activation-key/").json()["activation_key"]
        pw = self.s.post(f"/api/projects/{PRN}/temporary-password/").json()["temporary_password"]
        serial = self.s.post(f"/api/projects/{PRN}/serial-number/").json()["serial_number"]
        self.assertNotEqual(pw, self.s.post(f"/api/projects/{PRN}/temporary-password/").json()["temporary_password"])
        r = self.s.post(f"/api/projects/{PRN}/customers/", {
            "name": "Kumar", "company_name": "Kumar & Co", "license_type": "LICENSED", "contact_number": "9876543210", "email": "kumar@example.com",
            "address": "Chennai", "gstin": "33AFHPM6103Q1Z8", "serial_number": serial, "user_id": serial, "password": pw, "activation_key": key}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.issued(), (serial, pw, key))
        self.assertIn("Lifetime", mail.outbox[-1].body)

    def test_an_existing_customer_never_gets_new_credentials_by_being_viewed_edited_or_re_approved(self):
        r = self.approve().json()
        customer = ProjectCustomer.objects.get(pk=r["id"])
        before = (customer.password_hash, customer.activation_key_hash, customer.serial_number)
        sent = len(mail.outbox)
        self.s.get(f"/api/projects/{PRN}/customers/{customer.pk}/")
        self.s.patch(f"/api/projects/{PRN}/customers/{customer.pk}/", {"name": "Meena R", "company_name": "X", "contact_number": "9876543210",
                     "email": "new.customer@example.com", "address": "Chennai", "gstin": "33AFHPM6103Q1Z8", "serial_number": customer.serial_number}, format="json")
        req = RegistrationRequest.objects.get(customer=customer)
        self.assertEqual(self.s.post(APPROVE % req.pk, {"license_type": "TRIAL"}, format="json").status_code, 409)  # already processed
        customer.refresh_from_db()
        self.assertEqual((customer.password_hash, customer.activation_key_hash, customer.serial_number), before)
        self.assertEqual(len(mail.outbox), sent)


class IssuedCredentialsWorkEndToEndTests(CredentialsBase):
    """Approve in Nexus, read the email, then do exactly what the PRN app does with those exact values."""

    def setUp(self):
        super().setUp()
        self.staff()

    @override_settings(ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE=True)   # the optional strict mode
    def test_issued_credentials_activate_sign_in_change_password_and_report_usage(self):
        approved = self.approve("TRIAL").json()
        serial, temp, key = self.issued()
        r, dev = self.activate_with(serial, key)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(verify_like_the_agent(r.json()["entitlement"], PUB))
        payload = r.json()["entitlement"]["payload"]
        self.assertEqual((payload["serial"], payload["product"], payload["license_type"]), (serial, PRN, "TRIAL"))
        self.assertTrue(r.json()["account"]["must_change_password"])
        customer = ProjectCustomer.objects.get(pk=approved["id"])
        self.assertEqual(customer.expires_at, customer.first_activated_at + timedelta(days=7))
        # sign in with User ID + the emailed temporary password, then it must be replaced
        login = self.sign_in(dev, serial, temp)
        self.assertEqual(login.status_code, 200, login.content)
        self.assertTrue(login.json()["must_change_password"])
        self.assertEqual(self.call(dev, "POST", "usage/", {"event": "print"}).status_code, 403)
        done = self.call(dev, "POST", "change-password/", {"current_password": temp, "new_password": NEW_PASSWORD})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(self.sign_in(dev, serial, temp).status_code, 401)
        relogin = self.sign_in(dev, serial, NEW_PASSWORD)
        self.assertEqual((relogin.status_code, relogin.json()["must_change_password"]), (200, False))
        self.assertEqual(self.call(dev, "POST", "usage/", {"event": "print", "count": 2}).status_code, 200)
        self.assertEqual(self.call(dev, "GET", "license/status/").json()["authorized"], True)
        customer.refresh_from_db()
        self.assertEqual(customer.password_status if hasattr(customer, "password_status") else "customer", "customer")
        row = DeviceRegistration.objects.get(customer=customer)
        self.assertIsNotNone(row.last_used_at)
        # the Nexus customer list now shows the activation, the password change and a used device
        listed = next(c for c in self.s.get(f"/api/projects/{PRN}/customers/").json()["results"] if c["id"] == customer.pk)
        self.assertEqual(listed["password_status"], "customer_set")
        self.assertEqual(self.s.get(f"/api/projects/{PRN}/customers/{customer.pk}/device/").json()["device_status"], "active")
        # an activated computer does not need activating again: a refresh keeps working
        self.assertEqual(self.refresh(dev).status_code, 200)

    def test_wrong_or_foreign_credentials_are_refused_without_saying_which_part_is_wrong(self):
        self.approve("TRIAL")
        serial, _, key = self.issued()
        wrong = "ABCD-EFGH-JKLM-NPQR"
        outcomes = [self.activate_with(serial, wrong)[0], self.activate_with("799999999", key)[0], self.activate_with(serial, "")[0]]
        self.assertEqual([o.status_code for o in outcomes[:2]], [401, 401])
        self.assertEqual({o.json()["code"] for o in outcomes[:2]}, {"invalid_activation"})
        self.assertEqual(outcomes[0].json()["message"], outcomes[1].json()["message"])
        self.assertFalse(DeviceRegistration.objects.exists())
        self.assertEqual(self.activate_with(serial, key.lower().replace("-", ""))[0].status_code, 200)  # the exact key, loosely typed

    def test_a_key_works_only_for_its_own_product(self):
        self.approve("TRIAL")
        serial, _, key = self.issued()
        gstr = self.c.post("/api/device/gstr2tally/activate/", {"serial_number": serial, "activation_key": key}, format="json")
        self.assertEqual(gstr.status_code, 404)  # GSTR2Tally has no device licensing
        other = self.approve("TRIAL", email="g@example.com", project="gstr2tally").json()
        self.assertEqual(self.activate_with(other["serial_number"], self.issued()[2])[0].json()["code"], "invalid_activation")  # not a PRN customer
        self.assertFalse(DeviceRegistration.objects.exists())

    def test_expired_cancelled_and_inactive_licences_are_refused_with_their_reason(self):
        self.approve("TRIAL")
        serial, _, key = self.issued()
        customer = ProjectCustomer.objects.get(serial_number=serial)
        _, dev = self.activate_with(serial, key)
        ProjectCustomer.objects.filter(pk=customer.pk).update(first_activated_at=timezone.now() - timedelta(days=10), expires_at=timezone.now() - timedelta(days=3))
        for fields, code in (({}, "license_expired"), ({"status": "TRIAL_CANCELLED"}, "license_cancelled"), ({"status": "INACTIVE"}, "account_inactive")):
            ProjectCustomer.objects.filter(pk=customer.pk).update(**fields)
            r = self.activate_with(serial, key, Device(FP))[0]
            self.assertEqual((r.status_code, r.json()["code"]), (403, code))
            self.assertEqual(self.refresh(dev).json()["code"], code)

    def test_a_second_computer_is_refused_and_existing_customers_and_products_are_untouched(self):
        existing = self.customer
        gstr_before = ProjectCustomer.objects.filter(project="gstr2tally").count()
        before = (existing.password_hash, existing.activation_key_hash, existing.expires_at, existing.serial_number)
        self.approve("LICENSED")
        serial, _, key = self.issued()
        self.assertEqual(self.activate_with(serial, key)[0].status_code, 200)
        self.assertEqual(self.activate_with(serial, key, Device(OTHER_FP))[0].json()["code"], "device_limit")
        existing.refresh_from_db()
        self.assertEqual((existing.password_hash, existing.activation_key_hash, existing.expires_at, existing.serial_number), before)
        self.assertEqual(ProjectCustomer.objects.filter(project="gstr2tally").count(), gstr_before)


class ReissueKeyTests(CredentialsBase):
    def setUp(self):
        super().setUp()
        self.staff()
        self.approve("ONE_YEAR")
        self.serial, self.temp, self.old_key = self.issued()
        self.customer = ProjectCustomer.objects.get(serial_number=self.serial)
        self.url = f"/api/projects/{PRN}/customers/{self.customer.pk}/reissue-activation-key/"

    def test_reissue_replaces_only_the_key_and_emails_it_without_the_password(self):
        before = ProjectCustomer.objects.get(pk=self.customer.pk)
        r = self.s.post(self.url)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn("activation_key", r.json())
        body = mail.outbox[-1].body
        new_key = KEY_RE.search(body).group(1)
        self.assertNotEqual(new_key, self.old_key)
        self.assertNotIn("Temporary password", body)
        after = ProjectCustomer.objects.get(pk=self.customer.pk)
        self.assertEqual((after.password_hash, after.serial_number, after.expires_at, after.must_change_password),
                         (before.password_hash, before.serial_number, before.expires_at, before.must_change_password))
        self.assertNotEqual(after.activation_key_hash, before.activation_key_hash)
        self.assertEqual(self.activate_with(self.serial, self.old_key)[0].json()["code"], "invalid_activation")
        self.assertEqual(self.activate_with(self.serial, new_key)[0].status_code, 200)
        self.assertTrue(LicenseAuditLog.objects.filter(customer=after, action="ACTIVATION_KEY_REISSUED").exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_when_no_email_goes_out_the_new_key_is_shown_once(self):
        r = self.s.post(self.url).json()
        self.assertEqual(self.activate_with(self.serial, r["activation_key"])[0].status_code, 200)
        self.assertNotIn("activation_key", self.s.get(f"/api/projects/{PRN}/customers/{self.customer.pk}/").json())

    def test_only_the_super_admin_and_never_for_an_inactive_licence(self):
        self.staff("Admin")
        self.grant("edit", "delete", "entry", "view")
        self.assertEqual(self.s.post(self.url).status_code, 403)
        self.staff("Superadmin")
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(status="INACTIVE")
        self.assertEqual(self.s.post(self.url).status_code, 409)
        self.s.force_authenticate(None)
        self.assertIn(self.s.post(self.url).status_code, (401, 403))
