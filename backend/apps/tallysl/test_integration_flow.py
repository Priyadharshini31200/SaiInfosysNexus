"""End-to-end registration -> customer list -> licence -> activation -> Tally sync -> usage flow, using only the public
client API and the staff API (no direct DB writes for the flow itself), in the isolated test database.

Set TALLY_DOC_DUMP=<file> to write every request/response pair as JSON (used to build docs/TALLY-SL-LICENSING.md)."""
import json
import os
import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.services import ensure_account
from datetime import timedelta

from django.utils import timezone

from apps.registrations import services as prn_services
from apps.registrations.models import ProjectClient, ProjectCustomer
from apps.registrations.portal_views import hash_key as prn_hash

from .client_views import hash_key
from .models import TallyProduct, TallyProductKey

BASE = "/api/v1/tally-sl/gstr2tally"
ADM = "/api/v1/tally-sl-admin"
KEY = "tsl_integration_key"
DEVICE = {"client_device_id": "9f1c2e7a-install-id", "fingerprint": "machine-guid-or-hardware-id", "device_name": "ACCOUNTS-PC", "app_version": "1.0.0"}
NEW_DEVICE = {"client_device_id": "5b7d8c10-install-id", "fingerprint": "other-machine-id", "device_name": "NEW-LAPTOP", "app_version": "1.0.0"}
LOG = []


def _body(resp):
    try:
        return json.loads(resp.content or b"null")
    except ValueError:
        return None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", TALLY_THROTTLE_RATE="10000/min",
                   REST_FRAMEWORK={**__import__("django.conf", fromlist=["settings"]).settings.REST_FRAMEWORK,
                                   "DEFAULT_THROTTLE_RATES": {"login": "1000/min", "portal": "1000/min", "device": "1000/min"}})
class RegistrationToReportsFlow(TestCase):
    def setUp(self):
        cache.clear()
        LOG.clear()
        product = TallyProduct.objects.get(code="gstr2tally")
        TallyProductKey.objects.create(product=product, key_hash=hash_key(KEY))
        self.api = APIClient()
        su, _ = ensure_account("flowsuper", Role.SUPERADMIN, "pw")
        self.staff = APIClient()
        self.staff.force_authenticate(su)

    def call(self, name, method, path, data=None, token=None, key=True):
        headers = {}
        if key:
            headers["HTTP_X_PRODUCT_KEY"] = KEY
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        fn = getattr(self.api, method)
        resp = fn(f"{BASE}/{path}", **headers) if method == "get" else fn(f"{BASE}/{path}", data, format="json", **headers)
        LOG.append({"name": name, "method": method.upper(), "path": f"/api/v1/tally-sl/gstr2tally/{path}", "request": data, "status": resp.status_code, "response": _body(resp)})
        return resp

    def test_full_flow(self):
        serial = "700456789"
        # 1. product identification
        r = self.call("identify", "get", "identify/")
        self.assertEqual((r.status_code, r.data["product"]["code"]), (200, "gstr2tally"))
        # wrong / missing product key
        self.assertEqual(self.call("identify_no_key", "get", "identify/", key=False).status_code, 401)
        # 2. registration (legitimate request) -> persisted customer + welcome email
        signup = {"name": "Ravi Kumar", "email": "ravi@example.com", "contact_number": "9876543210", "tally_serial": serial, "product": "gstr2tally"}
        r = self.call("register", "post", "register/", signup)
        self.assertEqual(r.status_code, 201, r.data)
        welcome = next(m for m in mail.outbox if m.to == ["ravi@example.com"])
        temp = re.search(r"Temporary Password: (\S+)", welcome.body).group(1)
        dup = self.call("register_duplicate", "post", "register/", signup)
        self.assertEqual((dup.status_code, dup.data["code"]), (409, "account_exists"))
        self.assertEqual(self.call("register_invalid", "post", "register/", {**signup, "email": "bad"}).status_code, 400)
        # staff list shows the persisted customer, license pending
        lst = self.staff.get(f"{ADM}/products/gstr2tally/customers/").data
        row = lst["results"][0]
        self.assertEqual((lst["total"], row["tally_serial"], row["name"], row["email"], row["contact_number"], row["state"]),
                         (1, serial, "Ravi Kumar", "ravi@example.com", "9876543210", "pending"))
        # 3. login with temporary password -> must change password
        login = self.call("login_first", "post", "auth/login/", {"user_id": serial, "password": temp, "device": DEVICE})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data["must_change_password"])
        self.assertFalse(login.data["license"]["authorized"])  # registration alone is not a licence
        tok = login.data["token"]
        self.assertEqual(self.call("license_status_blocked", "get", "license/status/", token=tok).data["code"], "password_change_required")
        self.assertEqual(self.call("login_wrong_password", "post", "auth/login/", {"user_id": serial, "password": "nope"}).data["code"], "invalid_credentials")
        self.assertEqual(self.call("change_password", "post", "auth/change-password/", {"current_password": temp, "new_password": "My#Pass123"}, token=tok).status_code, 200)
        login = self.call("login", "post", "auth/login/", {"user_id": serial, "password": "My#Pass123", "device": DEVICE})
        self.assertFalse(login.data["must_change_password"])
        tok = login.data["token"]
        # 4. profile
        self.assertEqual(self.call("account", "get", "account/", token=tok).data["tally_serial"], serial)
        # 5. Tally metadata sync (only verified fields)
        info = {"edition": "TallyPrime Gold", "tss_details": "TSS active", "virtual_user_limit": 5, "license_administrator": "Ravi Kumar",
                "gateway_server": "Tally Gateway Server", "registered_email": "tally@example.com", "tss_expiry_date": "2027-03-31",
                "verified_fields": ["edition", "tss_details", "virtual_user_limit", "license_administrator", "gateway_server", "registered_email", "tss_expiry_date"]}
        r = self.call("tally_info", "post", "tally-info/", info, token=tok)
        self.assertEqual(r.status_code, 200, r.data)
        # 6. Superadmin assigns the licence plan (API the Nexus screen uses) -> activation key emailed
        cid = row["id"]
        mail.outbox.clear()
        act = self.staff.post(f"{ADM}/products/gstr2tally/customers/{cid}/license/activate/", {"license_plan": "ANNUAL"}, format="json")
        LOG.append({"name": "staff_activate_license", "method": "POST", "path": f"/api/v1/tally-sl-admin/products/gstr2tally/customers/{cid}/license/activate/",
                    "request": {"license_plan": "ANNUAL"}, "status": act.status_code, "response": _body(act)})
        self.assertEqual((act.status_code, act.data["customer"]["license_plan"]), (200, "ANNUAL"))
        key = act.data["activation_key"]
        self.assertIn(key, mail.outbox[-1].body)
        # 7. Activate User (device enrolment): request code -> verify (code + activation key) -> confirm
        r = self.call("activate_request_code", "post", "activate/request-code/", {"user_id": serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        v = self.call("activate_verify", "post", "activate/verify/", {"user_id": serial, "code": code, "activation_key": key})
        self.assertEqual(v.status_code, 200, v.data)
        self.assertFalse(v.data["requires_confirmation"])
        c = self.call("activate_confirm", "post", "activate/confirm/", {"ticket": v.data["ticket"], "device": DEVICE, "confirm_transfer": False})
        self.assertEqual(c.status_code, 200, c.data)
        self.assertTrue(c.data["license"]["authorized"])
        tok = c.data["token"]
        self.assertEqual(self.call("license_status", "get", "license/status/", token=tok).data["license_plan"], "ANNUAL")
        # 8. usage event (application open) -> performance report
        # the activation sign-in itself counted as one opening; a second open inside the 60 s window is de-duplicated
        r = self.call("usage_open_deduplicated", "post", "usage/open/", {"app_version": "1.0.0"}, token=tok)
        self.assertEqual((r.status_code, r.data["recorded"]), (200, False))
        with self.settings(TALLY_USAGE_DEDUPE_SECONDS=0):
            r = self.call("usage_open", "post", "usage/open/", {"app_version": "1.0.0"}, token=tok)
        self.assertEqual((r.status_code, r.data["recorded"]), (200, True))
        perf = self.staff.get(f"{ADM}/reports/user-performance/").data["results"][0]
        self.assertEqual((perf["tally_serial"], perf["opens"], perf["active_days"]), (serial, 2, 1))
        dash = self.staff.get(f"{ADM}/dashboard/").data
        self.assertEqual(dash["registrations"]["total"], 1)
        # Tally details report shows the synced values
        tr = self.staff.get(f"{ADM}/reports/tally-details/").data["results"][0]
        self.assertEqual((tr["tally_email"], tr["tally_expiry_date"]), ("tally@example.com", "2027-03-31"))
        det = self.staff.get(f"{ADM}/products/gstr2tally/customers/{cid}/").data
        self.assertEqual((det["tally"]["edition"], det["tally"]["virtual_user_limit"], det["license"]["plan"]), ("TallyPrime Gold", 5, "ANNUAL"))
        # 9. token refresh + session poll
        rf = self.call("refresh", "post", "auth/refresh/", token=tok)
        self.assertEqual(rf.status_code, 200)
        self.assertEqual(self.call("session_old_token", "get", "session/", token=tok).data["code"], "session_revoked")
        tok = rf.data["token"]
        self.assertTrue(self.call("session", "get", "session/", token=tok).data["valid"])
        # 10. replacement laptop: warning, confirm, old device revoked
        self.call("transfer_request_code", "post", "activate/request-code/", {"user_id": serial}) if False else None
        cache.clear()
        from .models import TallyVerificationCode
        TallyVerificationCode.objects.update(created_at=__import__("django.utils.timezone", fromlist=["x"]).now() - __import__("datetime").timedelta(minutes=5))
        self.call("transfer_request_code", "post", "activate/request-code/", {"user_id": serial})
        code = re.search(r"Verification Code: (\S+)", mail.outbox[-1].body).group(1)
        v = self.call("transfer_verify", "post", "activate/verify/", {"user_id": serial, "code": code, "activation_key": key})
        self.assertTrue(v.data["requires_confirmation"])
        no = self.call("transfer_confirm_without_consent", "post", "activate/confirm/", {"ticket": v.data["ticket"], "device": NEW_DEVICE, "confirm_transfer": False})
        self.assertEqual((no.status_code, no.data["code"]), (409, "confirmation_required"))
        yes = self.call("transfer_confirm", "post", "activate/confirm/", {"ticket": v.data["ticket"], "device": NEW_DEVICE, "confirm_transfer": True})
        self.assertEqual((yes.status_code, yes.data["device_replaced"]), (200, True))
        self.assertEqual(self.call("session_revoked_after_transfer", "get", "session/", token=tok).data["code"], "session_revoked")
        replay = self.call("transfer_confirm_replay", "post", "activate/confirm/", {"ticket": v.data["ticket"], "device": NEW_DEVICE, "confirm_transfer": True})
        self.assertEqual(replay.status_code, 400)
        # 11. logout
        self.assertEqual(self.call("logout", "post", "auth/logout/", token=yes.data["token"]).status_code, 200)
        self.assertEqual(self.call("session_after_logout", "get", "session/", token=yes.data["token"]).status_code, 401)
        # 12. forgot / reset password
        mail.outbox.clear()
        cache.clear()
        self.call("forgot_password", "post", "auth/forgot-password/", {"identifier": "ravi@example.com"})
        reset = re.search(r"Reset Code: (\S+)", mail.outbox[-1].body).group(1)
        self.assertEqual(self.call("reset_password", "post", "auth/reset-password/", {"token": reset, "new_password": "Fresh#Pass9"}).status_code, 200)
        self.assertEqual(self.call("reset_password_reuse", "post", "auth/reset-password/", {"token": reset, "new_password": "Other#Pass9"}).status_code, 400)
        self.assertEqual(self.call("login_after_reset", "post", "auth/login/", {"user_id": serial, "password": "Fresh#Pass9", "device": NEW_DEVICE}).status_code, 200)
        # cross-product isolation: the GSTR key cannot act on another product's URL
        other = TallyProduct.objects.create(code="otherapp", name="Other")
        r = self.api.post("/api/v1/tally-sl/otherapp/auth/login/", {"user_id": serial, "password": "x"}, format="json", HTTP_X_PRODUCT_KEY=KEY)
        self.assertEqual(r.status_code, 403)
        if os.environ.get("TALLY_DOC_DUMP"):
            with open(os.environ["TALLY_DOC_DUMP"], "w", encoding="utf-8") as fh:
                json.dump(LOG, fh, indent=1, default=str)

    def test_prn_portal_unaffected_in_same_environment(self):
        """The delivered PRN customer portal API still answers exactly as before while Tally SL is installed."""
        ProjectClient.objects.create(project="prn-auto-print", key_hash=prn_hash("nxk_prn_flow"))
        now = timezone.now()  # a PRN customer created directly (self-contained: no dependency on other test modules)
        ProjectCustomer.objects.create(
            project="prn-auto-print", name="Ravi", company_name="Ravi Traders", license_type="TRIAL", user_id="7111111111",
            password_hash=prn_services.hash_password("Temp#Pass1"), contact_number="9876543210", email="prn@example.com", address="Chennai",
            serial_number="7111111111", trial_days=3, activation_key_hash="7" * 64, activation_key_suffix="ABCD", activated_at=now,
            expires_at=now + timedelta(days=3), must_change_password=True)
        r = self.api.post("/api/portal/prn-auto-print/login/", {"username": "7111111111", "password": "Temp#Pass1"}, format="json", HTTP_X_PROJECT_KEY="nxk_prn_flow")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(sorted(r.data), ["account", "license", "must_change_password", "token"])
        self.assertEqual(r.data["account"]["user_id"], "7111111111")
        # a Tally product key is useless on PRN routes and vice-versa
        bad = self.api.post("/api/portal/prn-auto-print/login/", {"username": "7111111111", "password": "Temp#Pass1"}, format="json", HTTP_X_PROJECT_KEY=KEY)
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(self.api.get(f"{BASE}/identify/", HTTP_X_PROJECT_KEY="nxk_prn_flow").status_code, 401)
