"""Signed device licensing: enrollment, entitlements, signed requests, replay protection, revocation and expiry."""
import base64
import json
import secrets
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from . import device_crypto as dc
from .models import DeviceNonce, DeviceRegistration, LicenseAuditLog, ProjectClient, ProjectCustomer
from .portal_views import hash_key
from .test_portal import REST, make_customer

PRN, GSTR = "prn-auto-print", "gstr2tally"
SERIAL, PASSWORD = "712345678", "Temp#Pass1"
PRIV, PUB = dc.generate_keypair()
FP = {"smbios": "a" * 64, "machine": "b" * 64, "v3": "c" * 64}
OTHER_FP = {"smbios": "d" * 64, "machine": "e" * 64, "v3": "f" * 64}
E = f"/api/device/{PRN}/"


def b64(raw):
    return base64.b64encode(raw).decode()


class Device:
    """What the PRN agent does on the customer's PC: owns the private key and signs requests."""

    def __init__(self, fp=FP):
        self.key = Ed25519PrivateKey.generate()
        self.public = b64(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        self.fp, self.device_id, self.token = fp, None, None

    def sign(self, method, path, body, ts, nonce):
        import hashlib

        msg = "\n".join([method, path, ts, nonce, hashlib.sha256(body).hexdigest()]).encode()
        return b64(self.key.sign(msg))


def verify_like_the_agent(ent, public_b64):
    """agent/license_core.verify_entitlement, unchanged."""
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(
            base64.urlsafe_b64decode(ent["sig"]), json.dumps(ent["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        )
        return True
    except Exception:
        return False


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST,
    LICENSE_SIGNING_PRIVATE_KEY=PRIV, LICENSE_KEY_ID="k1", PRN_VALIDITY_DAYS=7,
)
class DeviceTestBase(TestCase):
    """Fixtures and helpers shared by the device licensing tests."""

    def setUp(self):
        cache.clear()
        rates = {"portal": "1000/min", "device": "1000/min", "device_enroll": "1000/min", "device_public": "1000/min", "login": "1000/min"}
        patcher = patch.dict(ScopedRateThrottle.THROTTLE_RATES, rates)
        patcher.start()
        self.addCleanup(patcher.stop)
        ProjectClient.objects.create(project=PRN, key_hash=hash_key("nxk_prn"), reset_url="https://prn.example/reset")
        self.c = APIClient()
        self.customer = make_customer(PRN, SERIAL, "ravi@example.com", password=PASSWORD, must_change_password=False)

    # ---- helpers ------------------------------------------------------------------------------------------------
    def enroll(self, dev=None, username=SERIAL, password=PASSWORD, **over):
        dev = dev or Device()
        body = {"username": username, "password": password, "device_public_key": dev.public, "fingerprint": dev.fp,
                "device_name": "Front desk PC", "app_version": "1.0.0", **over}
        r = self.c.post(E + "enroll/", body, format="json")
        if r.status_code == 200:
            dev.device_id, dev.token = r.json()["device_id"], r.json()["token"]
        return r, dev

    def call(self, dev, method, path, body=None, ts=None, nonce=None, signer=None, token=True, device_id=None, raw=None):
        raw = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        ts = ts or timezone.now().isoformat().replace("+00:00", "Z")
        nonce = nonce or secrets.token_urlsafe(18)
        full = E + path
        headers = {
            "HTTP_X_DEVICE_ID": device_id or dev.device_id, "HTTP_X_DEVICE_TIMESTAMP": ts, "HTTP_X_DEVICE_NONCE": nonce,
            "HTTP_X_DEVICE_SIGNATURE": (signer or dev).sign(method, full, raw, ts, nonce),
        }
        if token and dev.token:
            headers["HTTP_X_CUSTOMER_TOKEN"] = dev.token
        return self.c.generic(method, full, raw, content_type="application/json", **headers)

    def refresh(self, dev, **kw):
        return self.call(dev, "POST", "entitlement/", {"fingerprint": dev.fp}, **kw)

    def reload(self):
        self.customer.refresh_from_db()
        return self.customer


class DeviceLicensingTests(DeviceTestBase):
    # ---- enrollment ---------------------------------------------------------------------------------------------
    def test_first_enrollment_issues_a_signed_device_bound_entitlement(self):
        before = self.reload().expires_at
        r, dev = self.enroll()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        ent = body["entitlement"]
        self.assertTrue(verify_like_the_agent(ent, PUB))
        self.assertEqual(ent["key_id"], "k1")
        p = ent["payload"]
        self.assertEqual((p["v"], p["product"], p["serial"], p["status"], p["generation"]), (1, PRN, SERIAL, "ACTIVE", 1))
        self.assertEqual((p["device"], p["device_machine"], p["device_v3"], p["device_id"]), (FP["smbios"], FP["machine"], FP["v3"], dev.device_id))
        self.assertEqual(p["customer_id"], self.customer.pk)
        # the agent derives the device from the payload exactly like this
        self.assertEqual({"smbios": p["device"], "machine": p["device_machine"], "v3": p["device_v3"]}, FP)
        self.assertEqual(body["license"]["state"], "active")
        self.assertIn("server_time", body)
        c = self.reload()
        self.assertIsNotNone(c.first_activated_at)
        self.assertEqual(c.expires_at, c.first_activated_at + timedelta(days=7))  # trial: first activation + 7 days
        self.assertGreater(c.expires_at, before)
        self.assertTrue(LicenseAuditLog.objects.filter(customer=c, action="DEVICE_ENROLLED").exists())

    def test_tampered_or_foreign_entitlements_fail_verification(self):
        ent = self.enroll()[0].json()["entitlement"]
        edited = json.loads(json.dumps(ent))
        edited["payload"]["expires_at"] = "2099-01-01T00:00:00Z"
        self.assertFalse(verify_like_the_agent(edited, PUB))
        self.assertFalse(verify_like_the_agent(ent, dc.generate_keypair()[1]))

    def test_nothing_secret_is_returned_or_stored(self):
        r, dev = self.enroll()
        text = r.content.decode()
        for secret in (PRIV, PASSWORD, "pbkdf2", "password_hash"):
            self.assertNotIn(secret, text)
        row = DeviceRegistration.objects.get()
        stored = " ".join(str(getattr(row, f.name)) for f in row._meta.fields)
        self.assertNotIn(PRIV, stored)
        for raw_hash in FP.values():  # the agent's hashes are stored only wrapped with the server key
            self.assertNotIn(raw_hash, stored)
        self.assertEqual(row.device_public_key, dev.public)
        self.assertNotIn(PRIV, str(self.c.get(E + "public/keys/").json()))
        self.assertEqual(self.c.get(E + "public/keys/").json()["keys"][0]["public_key"], PUB)

    def test_enrollment_rejects_bad_credentials_states_and_input(self):
        self.assertEqual(self.enroll(password="wrong")[0].status_code, 401)
        self.assertEqual(self.enroll(dev=Device(), device_public_key="not-a-key")[0].status_code, 400)
        self.assertEqual(self.enroll(fingerprint={"smbios": "short"})[0].status_code, 400)
        cases = (
            ({"must_change_password": False, "expires_at": timezone.now() - timedelta(hours=1), "first_activated_at": timezone.now() - timedelta(days=8)}, 403, "license_expired"),
            ({"expires_at": timezone.now() + timedelta(days=2), "status": "TRIAL_CANCELLED"}, 403, "license_cancelled"),
            ({"status": "INACTIVE"}, 403, "account_inactive"),
        )
        for patch_fields, status, code in cases:
            ProjectCustomer.objects.filter(pk=self.customer.pk).update(
                **{"status": "ACTIVE", "expires_at": timezone.now() + timedelta(days=2), "first_activated_at": None, **patch_fields}
            )
            r = self.enroll()[0]
            self.assertEqual((r.status_code, r.json()["code"]), (status, code))
        self.assertEqual(DeviceRegistration.objects.count(), 0)
        self.assertIsNone(self.reload().first_activated_at)  # failed attempts never start the validity period

    @override_settings(ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE=True)
    def test_a_customer_on_the_temporary_password_can_enroll_but_only_change_the_password_until_they_have(self):
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(must_change_password=True)
        r, dev = self.enroll()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["account"]["must_change_password"])
        self.assertTrue(verify_like_the_agent(r.json()["entitlement"], PUB))
        for path in ("license/status/", "account/"):                      # nothing else works on the temporary password
            g = self.call(dev, "GET", path)
            self.assertEqual((g.status_code, g.json()["code"]), (403, "password_change_required"))
        done = self.call(dev, "POST", "change-password/", {"current_password": PASSWORD, "new_password": "Brand#New42x"})
        self.assertEqual(done.status_code, 200, done.content)
        again = self.call(dev, "POST", "login/", {"username": SERIAL, "password": "Brand#New42x"}, token=False)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertFalse(again.json()["must_change_password"])

    def test_signing_not_configured_fails_closed_and_changes_nothing(self):
        with override_settings(LICENSE_SIGNING_PRIVATE_KEY=""):
            r = self.enroll()[0]
        self.assertEqual((r.status_code, r.json()["code"]), (503, "licensing_unavailable"))
        self.assertEqual(DeviceRegistration.objects.count(), 0)
        self.assertIsNone(self.reload().first_activated_at)

    def test_one_active_device_per_licence_and_reinstall_keeps_everything(self):
        r, dev = self.enroll()
        first = r.json()
        activated, expires = first["entitlement"]["payload"]["activated_at"], first["entitlement"]["payload"]["expires_at"]
        stamp = self.reload().first_activated_at
        # a second computer is refused
        second = self.enroll(dev=Device(OTHER_FP))[0]
        self.assertEqual((second.status_code, second.json()["code"]), (409, "device_limit"))
        # reinstall of the same computer: new device key, same registration / generation / dates
        again_dev = Device()
        again, again_dev = self.enroll(dev=again_dev)
        self.assertEqual(again.status_code, 200, again.content)
        body = again.json()
        self.assertEqual(body["device_id"], first["device_id"])
        self.assertEqual((body["entitlement"]["payload"]["generation"], body["entitlement"]["payload"]["activated_at"], body["entitlement"]["payload"]["expires_at"]),
                         (1, activated, expires))
        self.assertEqual(self.reload().first_activated_at, stamp)
        self.assertEqual(DeviceRegistration.objects.count(), 1)
        self.assertEqual(DeviceRegistration.objects.get().device_public_key, again_dev.public)
        # the replaced key no longer works; the new one does
        self.assertEqual(self.refresh(dev).status_code, 401)
        self.assertEqual(self.refresh(again_dev).status_code, 200)

    def test_database_rejects_a_second_active_device(self):
        self.enroll()
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            DeviceRegistration.objects.create(customer=self.customer, project=PRN, device_public_key=PUB, fingerprint_hashes="{}")

    # ---- validity policy (packages are tested in test_packages.py) ----------------------------------------------
    def test_valid_until_is_capped_by_expiry(self):
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(expires_at=timezone.now() + timedelta(days=1))
        r, dev = self.enroll()
        p = r.json()["entitlement"]["payload"]
        self.assertEqual(p["valid_until"], p["expires_at"])

    # ---- refresh / licence states -------------------------------------------------------------------------------
    def test_refresh_requires_the_enrolled_computer_and_an_active_licence(self):
        r, dev = self.enroll()
        ok = self.refresh(dev)
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(verify_like_the_agent(ok.json()["entitlement"], PUB))
        dev.fp = OTHER_FP
        r = self.refresh(dev)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "device_mismatch"))
        dev.fp = FP
        for fields, code in (({"status": "TRIAL_CANCELLED"}, "license_cancelled"), ({"status": "INACTIVE"}, "account_inactive")):
            ProjectCustomer.objects.filter(pk=self.customer.pk).update(**fields)
            r = self.refresh(dev)
            self.assertEqual((r.status_code, r.json()["code"]), (403, code))
        ProjectCustomer.objects.filter(pk=self.customer.pk).update(status="ACTIVE", expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.refresh(dev).json()["code"], "license_expired")

    # ---- signed requests ----------------------------------------------------------------------------------------
    def test_signed_request_rules(self):
        _, dev = self.enroll()
        self.assertEqual(self.call(dev, "GET", "license/status/").status_code, 200)
        # replay of the exact same request
        nonce = secrets.token_urlsafe(18)
        ts = timezone.now().isoformat().replace("+00:00", "Z")
        self.assertEqual(self.call(dev, "GET", "license/status/", nonce=nonce, ts=ts).status_code, 200)
        r = self.call(dev, "GET", "license/status/", nonce=nonce, ts=ts)
        self.assertEqual((r.status_code, r.json()["code"]), (401, "replayed_request"))
        # timestamps outside +-5 minutes, either direction
        for delta in (-6, 6):
            old = (timezone.now() + timedelta(minutes=delta)).isoformat().replace("+00:00", "Z")
            r = self.call(dev, "GET", "license/status/", ts=old)
            self.assertEqual((r.status_code, r.json()["code"]), (401, "timestamp_out_of_range"))
        # signed with another computer's key (impersonation) / wrong device id / unknown device
        r = self.call(dev, "GET", "license/status/", signer=Device())
        self.assertEqual((r.status_code, r.json()["code"]), (401, "invalid_signature"))
        self.assertEqual(self.call(dev, "GET", "license/status/", device_id="00000000-0000-0000-0000-000000000000").json()["code"], "invalid_signature")
        self.assertEqual(self.call(dev, "GET", "license/status/", device_id="nope").json()["code"], "invalid_signature")
        # bad nonce, missing headers, tampered body
        self.assertEqual(self.call(dev, "GET", "license/status/", nonce="short").json()["code"], "bad_nonce")
        self.assertEqual(self.c.get(E + "license/status/").json()["code"], "device_auth_required")
        body = json.dumps({"fingerprint": dev.fp}).encode()
        ts = timezone.now().isoformat().replace("+00:00", "Z")
        n = secrets.token_urlsafe(18)
        sig = dev.sign("POST", E + "entitlement/", body, ts, n)
        r = self.c.generic("POST", E + "entitlement/", body.replace(b"a", b"b", 1), content_type="application/json",
                           HTTP_X_DEVICE_ID=dev.device_id, HTTP_X_DEVICE_TIMESTAMP=ts, HTTP_X_DEVICE_NONCE=n, HTTP_X_DEVICE_SIGNATURE=sig)
        self.assertEqual(r.json()["code"], "invalid_signature")
        # a failed (unsigned-by-device) request must not consume nonces
        self.assertFalse(DeviceNonce.objects.filter(nonce__in=["short", n]).exists())
        # the signature covers the path: a request signed for one endpoint cannot be sent to another
        ts = timezone.now().isoformat().replace("+00:00", "Z")
        n = secrets.token_urlsafe(18)
        sig = dev.sign("GET", E + "account/", b"", ts, n)
        r = self.c.generic("GET", E + "license/status/", b"", HTTP_X_DEVICE_ID=dev.device_id, HTTP_X_DEVICE_TIMESTAMP=ts,
                           HTTP_X_DEVICE_NONCE=n, HTTP_X_DEVICE_SIGNATURE=sig, HTTP_X_CUSTOMER_TOKEN=dev.token)
        self.assertEqual(r.json()["code"], "invalid_signature")

    def test_old_nonces_are_pruned_but_recent_ones_kept(self):
        _, dev = self.enroll()
        self.call(dev, "GET", "license/status/")
        DeviceNonce.objects.update(created_at=timezone.now() - timedelta(hours=1))
        self.call(dev, "GET", "license/status/")
        with patch("apps.registrations.device_views.random.random", return_value=0.0):
            self.call(dev, "GET", "license/status/")
        self.assertEqual(DeviceNonce.objects.count(), 2)  # the two recent ones; the old one is gone

    def test_device_tokens_are_bound_to_the_device_and_account(self):
        _, dev = self.enroll()
        # a token from the project-key portal login is not a device token
        portal_token = self.c.post(f"/api/portal/{PRN}/login/", {"username": SERIAL, "password": PASSWORD}, format="json",
                                   HTTP_X_PROJECT_KEY="nxk_prn").json()["token"]
        old, dev.token = dev.token, portal_token
        r = self.call(dev, "GET", "license/status/")
        self.assertEqual((r.status_code, r.json()["code"]), (401, "invalid_token"))
        dev.token = old
        self.assertEqual(self.call(dev, "GET", "account/").status_code, 200)
        # another customer's credentials cannot sign in on this computer
        make_customer(PRN, "722222222", "o@example.com", password="Other#Pass1", must_change_password=False, activation_key_hash="o" * 64)
        r = self.call(dev, "POST", "login/", {"username": "722222222", "password": "Other#Pass1"})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "wrong_device_account"))
        r = self.call(dev, "POST", "login/", {"username": SERIAL, "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("hash", r.content.decode().lower())

    def test_password_change_from_the_device_is_applied_in_nexus_immediately(self):
        _, dev = self.enroll()
        r = self.call(dev, "POST", "change-password/", {"current_password": PASSWORD, "new_password": "Brand#New42x"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.call(dev, "GET", "account/").status_code, 401)  # old token died with the password
        self.assertEqual(self.call(dev, "POST", "login/", {"username": SERIAL, "password": PASSWORD}).status_code, 401)
        self.assertEqual(self.call(dev, "POST", "login/", {"username": SERIAL, "password": "Brand#New42x"}).status_code, 200)
        self.assertIsNotNone(self.reload().password_changed_at)

    # ---- revocation / transfer ----------------------------------------------------------------------------------
    @override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
    def test_superadmin_transfer_revokes_then_new_computer_enrols_with_next_generation(self):
        from apps.accounts.models import AdminPermission

        call_command("seed_initial_users", verbosity=0)
        r, old = self.enroll()
        stamp = self.reload().first_activated_at
        expiry = self.customer.expires_at
        url = f"/api/projects/{PRN}/customers/{self.customer.pk}/device/"
        staff = APIClient()
        staff.post("/api/auth/login/", {"username": "Admin", "password": "Admin"}, format="json")
        AdminPermission.objects.update_or_create(action="edit", defaults={"name": "Edit Access", "enabled": True})
        self.assertEqual(staff.post(url + "revoke/", {}, format="json").status_code, 403)  # Admin cannot transfer
        self.assertEqual(staff.get(url).status_code, 403)
        self.assertEqual(self.refresh(old).status_code, 200)
        staff = APIClient()
        staff.post("/api/auth/login/", {"username": "Superadmin", "password": "Superadmin"}, format="json")
        info = staff.get(url).json()
        self.assertEqual(info["device"]["device_name"], "Front desk PC")
        self.assertNotIn("device_public_key", str(info))
        self.assertNotIn("fingerprint", str(info))
        self.assertEqual(staff.post(url + "revoke/", {"reason": "PC replaced"}, format="json").status_code, 200)
        self.assertEqual(staff.post(url + "revoke/", {}, format="json").status_code, 409)  # nothing left to revoke
        self.assertTrue(LicenseAuditLog.objects.filter(action="DEVICE_REVOKED", customer=self.customer).exists())
        # the old computer is refused (it proved it holds the key, so it is told why)
        r = self.refresh(old)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "device_revoked"))
        # the customer enrols the new computer: generation 2, same activation date and expiry
        new = Device(OTHER_FP)
        r, new = self.enroll(dev=new)
        self.assertEqual(r.status_code, 200, r.content)
        p = r.json()["entitlement"]["payload"]
        self.assertEqual(p["generation"], 2)
        self.assertNotEqual(r.json()["device_id"], old.device_id)
        self.assertEqual(self.reload().first_activated_at, stamp)
        self.assertEqual(self.customer.expires_at, expiry)
        self.assertEqual(p["activated_at"], stamp.isoformat().replace("+00:00", "Z"))
        self.assertEqual(self.refresh(new).status_code, 200)
        self.assertEqual(DeviceRegistration.objects.filter(customer=self.customer, revoked_at__isnull=True).count(), 1)
        self.assertEqual(staff.get(url).json()["history"][0]["revoked_reason"], "PC replaced")

    # ---- surface rules ------------------------------------------------------------------------------------------
    def test_device_api_exists_only_for_prn_and_public_calls_reveal_nothing(self):
        r = self.c.post(f"/api/device/{GSTR}/enroll/", {"username": "x", "password": "y"}, format="json")
        self.assertEqual(r.status_code, 404)
        body = {"name": "Asha", "contact_number": "9876501234", "email": "ravi@example.com"}  # already has an account
        known = self.c.post(E + "public/register-request/", body, format="json")
        fresh = self.c.post(E + "public/register-request/", {**body, "email": "new@example.com"}, format="json")
        self.assertEqual((known.status_code, fresh.status_code), (201, 201))
        self.assertNotIn("id", known.json())
        for ident in ("ravi@example.com", "nobody@example.com"):
            self.assertEqual(self.c.post(E + "public/forgot-password/", {"identifier": ident}, format="json").json(),
                             {"message": "If the account exists, a password reset link has been emailed."})
        link = [m for m in mail.outbox if "reset" in m.subject.lower()][-1].body.split("?token=")[1].split()[0]
        self.assertEqual(self.c.post(E + "public/reset-password/", {"token": link, "new_password": "Fresh#Pass88"}, format="json").status_code, 200)

    def test_enroll_is_rate_limited_and_failures_lock_the_account(self):
        rates = {"device_enroll": "3/min"}
        with patch.dict(ScopedRateThrottle.THROTTLE_RATES, rates):
            cache.clear()
            codes = [self.enroll(password="wrong")[0].status_code for _ in range(4)]
        self.assertEqual(codes, [401, 401, 401, 429])
        with override_settings(CUSTOMER_MAX_FAILED_LOGINS=3):
            cache.clear()
            ProjectCustomer.objects.filter(pk=self.customer.pk).update(failed_logins=0, locked_until=None)
            for _ in range(3):
                self.enroll(password="wrong")
            r = self.enroll()[0]  # right password, but the account is locked
            self.assertEqual((r.status_code, r.json()["code"]), (429, "account_locked"))

    def test_migrations_leave_existing_customers_untouched(self):
        c = self.reload()
        self.assertIsNone(c.first_activated_at)
        self.assertTrue(c.password_hash and c.expires_at)
        before, expiry = (c.status, c.license_type, c.password_hash), c.expires_at
        self.enroll()
        c = self.reload()
        self.assertEqual((c.status, c.license_type, c.password_hash), before)
        self.assertGreaterEqual(c.expires_at, expiry)  # activation never shortens an existing date
