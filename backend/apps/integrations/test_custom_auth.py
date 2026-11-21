"""API Setup -> Authentication Method -> "Other (Custom Authentication)". All external endpoints are mocked; no network, no production data."""
import json
from unittest import mock

from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.accounts.models import UserModulePermission
from apps.accounts.services import ensure_account

from . import pull
from .models import IntegrationApiConfig, IntegrationProject
from .services import IntegrationError
from .test_pull import CONFIG, Resp, rec
from .tests import ADM, Base

ENV = {"EXT_TOKEN": "static-secret-1", "EXT_PASSWORD": "pw-secret", "EXT_USER": "svc-user", "EXT_CLIENT_SECRET": "oauth-secret"}
LOGIN = {"strategy": "LOGIN_TOKEN", "token_url": "/auth/login", "token_method": "POST", "token_path": "data.token", "expires_in_path": "data.expires_in",
         "body": {"username": "${SECRET2}", "password": "${SECRET}"}, "secondary_ref": "EXT_USER"}


class Api:
    """A fake external project: a login endpoint that issues JWTs and a customer API that only accepts the current one."""

    def __init__(self, login_status=200, accept=None):
        self.login_status, self.accept, self.issued, self.calls = login_status, accept, 0, []

    def __call__(self, method, url, headers=None, params=None, json=None, data=None, **kw):
        self.calls.append((method, url, dict(headers or {}), json, data))
        if url.endswith("/auth/login") or url.endswith("/oauth/token"):
            if self.login_status != 200:
                return Resp(self.login_status, {})
            self.issued += 1
            return Resp(200, {"data": {"token": f"jwt-{self.issued}", "expires_in": 3600}, "access_token": f"oauth-{self.issued}", "expires_in": 3600})
        want = self.accept() if callable(self.accept) else self.accept
        got = (headers or {}).get("Authorization") or (headers or {}).get("X-Auth-Token")
        if want is not None and got != want:
            return Resp(401, {})
        return Resp(200, {"data": [rec(1)]})


@mock.patch.dict("os.environ", ENV)
@mock.patch.object(pull, "BACKOFF", 0)
@mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
class CustomAuthTests(Base):
    def setUp(self):
        super().setUp()
        cache.clear()

    def put(self, client=None, code="MOTHYS", **over):
        return (client or self.staff).put(f"{ADM}projects/{code}/api-config/", {**CONFIG, **over}, format="json")

    def cfg(self, code="MOTHYS"):
        return IntegrationApiConfig.objects.select_related("project").get(project__code=code)

    # ---------------------------------------------------------------- existing methods unchanged
    def test_none_bearer_and_api_key_unchanged(self, _dns):
        self.assertEqual(self.put().status_code, 200)
        self.assertNotIn("Authorization", pull._headers(self.cfg()))
        self.put(auth_type="BEARER", credential_ref="EXT_TOKEN")
        self.assertEqual(pull._headers(self.cfg())["Authorization"], "Bearer static-secret-1")
        self.put(auth_type="API_KEY_HEADER", credential_ref="EXT_TOKEN", auth_header="X-API-Key")
        self.assertEqual(pull._headers(self.cfg())["X-API-Key"], "static-secret-1")
        self.assertEqual(self.cfg().auth_config, "{}")  # nothing custom is stored for the existing methods

    # ---------------------------------------------------------------- static header
    def test_custom_static_header_with_prefix_and_extra_headers(self, _dns):
        r = self.put(auth_type="CUSTOM", credential_ref="EXT_TOKEN", auth_config={
            "strategy": "STATIC_HEADER", "header_name": "X-Auth-Token", "header_prefix": "Token ", "extra_headers": {"X-Tenant": "acme"}})
        self.assertEqual(r.status_code, 200, r.data)
        h = pull._headers(self.cfg())
        self.assertEqual((h["X-Auth-Token"], h["X-Tenant"]), ("Token static-secret-1", "acme"))
        api = Api(accept="Token static-secret-1")
        with mock.patch("apps.integrations.pull.requests.request", side_effect=api):
            self.assertEqual(self.staff.post(f"{ADM}projects/MOTHYS/validate/customer/").data["ok"], True)

    # ---------------------------------------------------------------- login API -> JWT, caching, renewal
    def test_login_jwt_is_fetched_cached_and_used_for_customer_requests(self, _dns):
        self.assertEqual(self.put(auth_type="CUSTOM", credential_ref="EXT_PASSWORD", auth_config=LOGIN).status_code, 200)
        api = Api(accept="Bearer jwt-1")
        with mock.patch("apps.integrations.pull.requests.request", side_effect=api):
            pull._get(self.cfg(), {})
            pull._get(self.cfg(), {})
        self.assertEqual(api.issued, 1)  # cached until it expires
        login = api.calls[0]
        self.assertTrue(login[1].endswith("/auth/login"))
        self.assertEqual(login[3], {"username": "svc-user", "password": "pw-secret"})  # placeholders filled on the server only

    def test_expired_token_is_renewed_once_after_401(self, _dns):
        self.put(auth_type="CUSTOM", credential_ref="EXT_PASSWORD", auth_config=LOGIN)
        api = Api()
        api.accept = lambda: f"Bearer jwt-{api.issued}" if api.issued >= 2 else "never"
        with mock.patch("apps.integrations.pull.requests.request", side_effect=api):
            body = pull._get(self.cfg(), {})
        self.assertEqual((api.issued, body["data"][0]["id"]), (2, "X-1"))

    def test_saving_settings_discards_cached_token(self, _dns):
        self.put(auth_type="CUSTOM", credential_ref="EXT_PASSWORD", auth_config=LOGIN)
        api = Api()
        with mock.patch("apps.integrations.pull.requests.request", side_effect=api):
            pull._get(self.cfg(), {})
            self.put(auth_type="CUSTOM", credential_ref="EXT_PASSWORD", auth_config={**LOGIN, "token_ttl_seconds": 600})
            pull._get(self.cfg(), {})
        self.assertEqual(api.issued, 2)

    def test_oauth2_client_credentials(self, _dns):
        self.assertEqual(self.put(auth_type="CUSTOM", credential_ref="EXT_CLIENT_SECRET", auth_config={
            "strategy": "OAUTH2_CLIENT_CREDENTIALS", "token_url": "https://auth.example.com/oauth/token", "client_id": "nexus", "scope": "customers.read"}).status_code, 200)
        api = Api(accept="Bearer oauth-1")
        with mock.patch("apps.integrations.pull.requests.request", side_effect=api):
            pull._get(self.cfg(), {})
        self.assertEqual(api.calls[0][4], {"grant_type": "client_credentials", "client_id": "nexus", "client_secret": "oauth-secret", "scope": "customers.read"})

    # ---------------------------------------------------------------- errors
    def test_invalid_credentials_missing_secret_and_rejected_api(self, _dns):
        self.put(auth_type="CUSTOM", credential_ref="EXT_PASSWORD", auth_config=LOGIN)
        with mock.patch("apps.integrations.pull.requests.request", side_effect=Api(login_status=401)):
            with self.assertRaises(IntegrationError) as e:
                pull._get(self.cfg(), {})
        self.assertEqual(e.exception.code, "AUTH_REJECTED")
        self.assertNotIn("pw-secret", str(e.exception.message))
        self.put(auth_type="CUSTOM", credential_ref="NOT_SET_ANYWHERE", auth_config={"strategy": "STATIC_HEADER", "header_name": "X-Auth-Token"})
        with self.assertRaises(IntegrationError) as e:
            pull._headers(self.cfg())
        self.assertEqual(e.exception.code, "AUTH_CREDENTIAL_MISSING")
        self.put(auth_type="CUSTOM", credential_ref="EXT_TOKEN", auth_config={"strategy": "STATIC_HEADER", "header_name": "X-Auth-Token"})
        with mock.patch("apps.integrations.pull.requests.request", side_effect=Api(accept="something-else")):
            with self.assertRaises(IntegrationError) as e:
                pull._get(self.cfg(), {})
        self.assertIn("authentication was rejected", e.exception.message)

    def test_validation_rejects_unsafe_or_unknown_settings(self, _dns):
        bad = [
            {"strategy": "MAGIC"},
            {"strategy": "STATIC_HEADER", "header_name": "Bad Header"},
            {"strategy": "STATIC_HEADER", "header_name": "Host"},
            {"strategy": "STATIC_HEADER", "extra_headers": {"X-A": "line1\r\nX-Injected: 1"}},
            {**LOGIN, "body": {"password": "${ENV:DJANGO_SECRET_KEY}"}},  # no expressions / arbitrary env access
            {**LOGIN, "token_url": "ftp://x"},
            {"strategy": "OAUTH2_CLIENT_CREDENTIALS", "token_url": "/t"},  # client id missing
        ]
        for ac in bad:
            self.assertEqual(self.put(auth_type="CUSTOM", credential_ref="EXT_TOKEN", auth_config=ac).status_code, 400, ac)
        self.assertEqual(self.put(auth_type="CUSTOM", auth_config={"strategy": "STATIC_HEADER"}).status_code, 400)  # no credential reference

    def test_token_url_ssrf_guard(self, dns):
        self.put(auth_type="CUSTOM", credential_ref="EXT_PASSWORD", auth_config={**LOGIN, "token_url": "https://internal.example.com/login"})
        dns.return_value = [(2, 1, 6, "", ("10.0.0.5", 443))]
        with self.settings(NEXUS_ALLOW_PRIVATE_INTEGRATION_URLS=False), self.assertRaises(IntegrationError) as e:
            pull._fetch_token(self.cfg(), pull._auth_cfg(self.cfg()))
        self.assertEqual(e.exception.code, "UNSAFE_URL")

    # ---------------------------------------------------------------- persistence, isolation, permissions
    def test_save_reopen_without_secret_values_and_project_isolation(self, _dns):
        self.put(auth_type="CUSTOM", credential_ref="EXT_PASSWORD", auth_config=LOGIN)
        got = self.staff.get(f"{ADM}projects/MOTHYS/api-config/").data["config"]
        self.assertEqual((got["auth_type"], got["auth_config"]["strategy"], got["auth_config"]["token_path"]), ("CUSTOM", "LOGIN_TOKEN", "data.token"))
        dump = json.dumps(got, default=str)
        for secret in ENV.values():
            self.assertNotIn(secret, dump)
        other = IntegrationProject.objects.create(code="OTH001", name="OTHER", license_policy="CALENDAR", backend_url="https://other.example.com")
        self.assertIsNone(self.staff.get(f"{ADM}projects/OTH001/api-config/").data["config"])
        self.assertNotEqual(pull._token_key(self.cfg()), f"nexus:ext-token:{other.pk}")

    def test_only_superadmin_may_change_custom_auth(self, _dns):
        IntegrationProject.objects.create(code="CAL001", name="CAL", license_policy="CALENDAR", backend_url="https://cal.example.com")
        self.assertEqual(self.put(code="CAL001", auth_type="CUSTOM", credential_ref="EXT_TOKEN", auth_config={"strategy": "STATIC_HEADER", "header_name": "X-Auth-Token"}).status_code, 200)
        admin, _ = ensure_account("iadmin-auth", Role.ADMIN, "pw")
        UserModulePermission.objects.create(user=admin, module="CAL001:api", can_edit=True)
        client = APIClient()
        client.force_authenticate(admin)
        same = {"strategy": "STATIC_HEADER", "header_name": "X-Auth-Token"}
        self.assertEqual(self.put(client, "CAL001", auth_type="CUSTOM", credential_ref="EXT_TOKEN", auth_config=same).status_code, 200)  # unchanged: allowed
        self.assertEqual(self.put(client, "CAL001", auth_type="CUSTOM", credential_ref="EXT_TOKEN", auth_config={**same, "header_name": "X-Other"}).status_code, 403)


@mock.patch.dict("os.environ", ENV)
@mock.patch.object(pull, "BACKOFF", 0)
@mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
class UniversalApiConfigTests(Base):
    """API Type / Protocol, Backend Framework, Basic + OAuth 2.0, and REST responses shaped like FastAPI / DRF / ASP.NET backends."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def put(self, **over):
        return self.staff.put(f"{ADM}projects/MOTHYS/api-config/", {**CONFIG, **over}, format="json")

    def cfg(self):
        return IntegrationApiConfig.objects.select_related("project").get(project__code="MOTHYS")

    def test_default_protocol_is_rest_and_existing_configs_keep_working(self, _dns):
        self.assertEqual(self.put().status_code, 200)
        got = self.staff.get(f"{ADM}projects/MOTHYS/api-config/").data["config"]
        self.assertEqual((got["api_protocol"], got["protocol_supported"]), ({"type": "REST"}, True))

    def test_other_protocol_saved_as_draft_but_never_called_or_activated(self, _dns):
        self.assertEqual(self.put(api_protocol={"type": "OTHER"}).status_code, 400)  # custom type required
        r = self.put(api_protocol={"type": "OTHER", "custom_type": "gRPC-Web", "content_type": "application/grpc-web"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["config"]["api_protocol"]["custom_type"], r.data["config"]["protocol_supported"]), ("gRPC-Web", False))
        with mock.patch("apps.integrations.pull.requests.request") as http:
            with self.assertRaises(IntegrationError) as e:
                pull._get(self.cfg(), {})
            self.assertFalse(http.called)  # no outbound call at all
        self.assertEqual(e.exception.code, "PROTOCOL_NOT_SUPPORTED")
        for kind in ("GRAPHQL", "SOAP"):
            self.assertEqual(self.put(api_protocol={"type": kind}).data["config"]["protocol_supported"], False)
        self.assertEqual(self.put(api_protocol={"type": "CORBA"}).status_code, 400)

    def test_unsupported_protocol_blocks_activation(self, _dns):
        IntegrationProject.objects.create(code="GQL001", name="GQL", license_policy="CALENDAR", backend_url="https://gql.example.com")
        self.staff.put(f"{ADM}projects/GQL001/api-config/", {**CONFIG, "api_protocol": {"type": "GRAPHQL"}}, format="json")
        r = self.staff.post(f"{ADM}projects/GQL001/activate/", {}, format="json")
        self.assertEqual((r.status_code, r.data.get("error", {}).get("code") or r.data.get("code")), (409, "PROTOCOL_NOT_SUPPORTED"))

    def test_backend_framework_is_metadata_and_persists(self, _dns):
        for fw in ("FastAPI (Python)", "ASP.NET Core Web API", "Go / Gin"):
            self.assertEqual(self.staff.patch(f"{ADM}projects/MOTHYS/", {"framework": fw}, format="json").status_code, 200)
            self.assertEqual(self.staff.get(f"{ADM}projects/MOTHYS/").data["project"]["framework"], fw)

    def test_basic_and_oauth2_authentication(self, _dns):
        self.assertEqual(self.put(auth_type="BASIC", credential_ref="EXT_PASSWORD", auth_config={}).status_code, 400)  # user name required
        self.assertEqual(self.put(auth_type="BASIC", credential_ref="EXT_PASSWORD", auth_config={"username": "svc"}).status_code, 200)
        import base64
        self.assertEqual(pull._headers(self.cfg())["Authorization"], "Basic " + base64.b64encode(b"svc:pw-secret").decode())
        r = self.put(auth_type="OAUTH2", credential_ref="EXT_CLIENT_SECRET", auth_config={"token_url": "https://auth.example.com/oauth/token", "client_id": "nexus"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["config"]["auth_config"]["strategy"], "OAUTH2_CLIENT_CREDENTIALS")
        with mock.patch("apps.integrations.pull.requests.request", side_effect=Api(accept="Bearer oauth-1")):
            self.assertEqual(pull._get(self.cfg(), {})["data"][0]["id"], "X-1")

    def test_rest_backends_of_different_frameworks_map_by_configuration_only(self, _dns):
        shapes = {
            "FastAPI": ({"items": [{"customer_id": "C101", "full_name": "Kumar", "email_address": "k@example.com", "mobile": "9876543210"}]},
                        "items", {"external_customer_id": "customer_id", "customer_name": "full_name", "email": "email_address", "contact_number": "mobile"}),
            "DRF": ({"count": 1, "next": None, "results": [{"id": 7, "profile": {"name": "Bala", "email": "b@example.com", "phone": "9876500000"}}]},
                    "results", {"external_customer_id": "id", "customer_name": "profile.name", "email": "profile.email", "contact_number": "profile.phone"}),
            "ASP.NET": ({"value": [{"CustomerId": "A-1", "Name": "Chitra", "Email": "c@example.com", "Mobile": "9000000001"}]},
                        "value", {"external_customer_id": "CustomerId", "customer_name": "Name", "email": "Email", "contact_number": "Mobile"}),
        }
        for label, (payload, path, mapping) in shapes.items():
            self.assertEqual(self.put(field_mapping=mapping, pagination={"type": "none", "results_path": path}).status_code, 200, label)
            with mock.patch("apps.integrations.pull.requests.request", return_value=Resp(200, payload)):
                r = self.staff.post(f"{ADM}projects/MOTHYS/validate/mapping/")
            self.assertTrue(r.data["ok"], (label, r.data))
            row = r.data["mapped"][0] if isinstance(r.data.get("mapped"), list) else r.data["mapped"]
            self.assertTrue(row["email"].endswith("@example.com"), label)


@mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
class PushModeSyncTests(Base):
    """Webhook (PUSH) projects: Sync Now never calls an external Customer List API; customers arrive through customers/register."""

    def new_project(self):
        r = self.staff.post(ADM + "projects/", {"code": "PSH001", "name": "Push Project", "frontend_url": "https://push.example.com",
                                                 "backend_url": "https://push.example.com/api", "framework": "FastAPI"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.staff.put(f"{ADM}projects/PSH001/api-config/", {**CONFIG, "sync_mode": "PUSH", "auth_type": "BEARER", "credential_ref": "EXT_TOKEN"}, format="json").status_code, 200)
        return r.data["credentials"]["client_id"], r.data["credentials"]["api_secret"]

    def test_sync_now_in_push_mode_makes_no_outbound_call_and_logs_no_failure(self, _dns):
        from .models import IntegrationSyncLog
        self.new_project()
        before = IntegrationSyncLog.objects.filter(project__code="PSH001").count()
        with mock.patch("apps.integrations.pull.requests.request") as http:
            r = self.staff.post(f"{ADM}projects/PSH001/sync/", {}, format="json")
        self.assertFalse(http.called)  # the old behaviour was GET <backend>/customers -> HTTP 404
        self.assertEqual(r.status_code, 409)
        body = json.dumps(r.data)
        self.assertIn("PUSH_MODE_NO_PULL", body)
        self.assertIn("customers/register", body)
        self.assertEqual(IntegrationSyncLog.objects.filter(project__code="PSH001").count(), before)

    def test_poll_mode_still_pulls(self, _dns):
        self.new_project()
        self.staff.put(f"{ADM}projects/PSH001/api-config/", {**CONFIG, "sync_mode": "POLL"}, format="json")
        with mock.patch("apps.integrations.pull.requests.request", return_value=Resp(200, {"data": [rec(1)]})) as http:
            self.staff.post(f"{ADM}projects/PSH001/sync/", {}, format="json")
        self.assertTrue(http.called)

    def test_inbound_registration_route_scope_mapping_and_audit(self, _dns):
        from .models import IntegrationAudit, IntegrationCustomer, IntegrationRegistration
        cid, secret = self.new_project()
        body = {"project_code": "PSH001", "external_customer_id": "EXT-9", "customer_name": "Webhook Customer", "email": "wh@example.com", "contact_number": "9876500009"}
        r = self.call("customers/register", body, cid=cid, secret=secret)
        self.assertIn(r.status_code, (200, 201, 202), r.data)
        self.assertEqual(r.data["data"]["status"], "PENDING_REVIEW")  # new projects: approval first, nothing issued yet
        reg = IntegrationRegistration.objects.get(project__code="PSH001", external_customer_id="EXT-9")
        self.assertEqual((reg.name, reg.email, reg.contact_number), ("Webhook Customer", "wh@example.com", "9876500009"))
        self.assertFalse(IntegrationCustomer.objects.filter(project__code="PSH001").exists())  # no customer / licence before approval
        self.call("customers/register", body, cid=cid, secret=secret)  # the same event delivered again
        self.assertEqual(IntegrationRegistration.objects.filter(project__code="PSH001", external_customer_id="EXT-9").count(), 1)  # no duplicate
        self.assertTrue(IntegrationAudit.objects.filter(project__code="PSH001").exists())
        self.assertEqual(self.call("customers/register", {**body, "project_code": "MOTHYS"}, cid=cid, secret=secret).status_code, 403)  # credentials are project-scoped
        self.assertEqual(self.call("customers/register", body, cid=cid, secret="wrong").status_code, 401)
        self.assertFalse(IntegrationCustomer.objects.filter(project__code="MOTHYS", external_customer_id="EXT-9").exists())

    # ---------------------------------------------------------------- auto-detect + webhook validation + activation gate
    def test_auto_detect_from_pasted_sample_nested_and_suggestions(self, _dns):
        self.new_project()
        sample = json.dumps({"customer_id": "C101", "full_name": "Kumar", "contact": {"email_address": "k@example.com", "mobile": "9876543210"}, "tags": ["a"]})
        with mock.patch("apps.integrations.pull.requests.request") as http:
            r = self.staff.post(f"{ADM}projects/PSH001/detect-fields/", {"sample": sample}, format="json").data
        self.assertFalse(http.called)
        self.assertTrue(r["ok"])
        self.assertEqual({f["path"] for f in r["fields"]}, {"customer_id", "full_name", "contact.email_address", "contact.mobile"})
        self.assertEqual(r["suggested"], {"external_customer_id": "customer_id", "customer_name": "full_name", "email": "contact.email_address", "contact_number": "contact.mobile"})
        saved = self.staff.get(f"{ADM}projects/PSH001/api-config/").data["config"]["field_mapping"]
        self.assertEqual(saved, CONFIG["field_mapping"])  # detection never saves or overwrites the mapping

    def test_auto_detect_errors_and_poll_mode_reads_one_sample_without_import(self, _dns):
        from .models import IntegrationCustomer, IntegrationRegistration
        self.new_project()
        url = f"{ADM}projects/PSH001/detect-fields/"
        self.assertTrue(self.staff.post(url, {}, format="json").data["needs_sample"])  # webhook mode: no payload -> ask for a sample
        self.assertIn("Invalid JSON", self.staff.post(url, {"sample": "{bad"}, format="json").data["message"])
        self.staff.put(f"{ADM}projects/PSH001/api-config/", {**CONFIG, "sync_mode": "POLL"}, format="json")
        with mock.patch("apps.integrations.pull.requests.request", return_value=Resp(200, {"data": [rec(1), rec(2)]})):
            r = self.staff.post(url, {}, format="json").data
        self.assertEqual((r["ok"], r["source"]), (True, "api"))
        self.assertIn("contact.email", {f["path"] for f in r["fields"]})
        self.assertFalse(IntegrationCustomer.objects.filter(project__code="PSH001").exists() or IntegrationRegistration.objects.filter(project__code="PSH001").exists())

    def test_webhook_validation_never_calls_out_and_checks_mapping_against_sample(self, _dns):
        self.new_project()
        mapping = {"external_customer_id": "customer_id", "customer_name": "full_name", "email": "email_address", "contact_number": "mobile"}
        self.staff.put(f"{ADM}projects/PSH001/api-config/", {**CONFIG, "sync_mode": "PUSH", "field_mapping": mapping}, format="json")
        contract = json.dumps({"external_customer_id": "C1", "customer_name": "Kumar", "email": "k@example.com", "contact_number": "9876543210"})
        foreign = json.dumps({"customer_id": "C1", "full_name": "Kumar", "email_address": "k@example.com", "mobile": "9876543210"})
        val = lambda body: self.staff.post(f"{ADM}projects/PSH001/validate/mapping/", body, format="json").data  # noqa: E731
        with mock.patch("apps.integrations.pull.requests.request") as http:
            cust = self.staff.post(f"{ADM}projects/PSH001/validate/customer/", {}, format="json").data
            nosample, ok, renamed = val({}), val({"sample": contract}), val({"sample": foreign})
            bad_email = val({"sample": json.dumps({**json.loads(contract), "email": "not-an-email"})})
        self.assertFalse(http.called)  # no GET /customers (the old HTTP 404)
        self.assertTrue(cust["ok"])
        self.assertIn("Awaiting the first live", cust["message"])
        self.assertTrue(nosample["needs_sample"])
        self.assertTrue(ok["ok"])  # matches what POST customers/register accepts, whatever the saved polling mapping says
        self.assertIn("does not prove live delivery", ok["message"])
        self.assertFalse(renamed["ok"])  # the live endpoint would reject these names: no false PASS
        self.assertEqual(renamed["rename"], {"external_customer_id": "customer_id", "customer_name": "full_name", "email": "email_address", "contact_number": "mobile"})
        self.assertFalse(bad_email["ok"])
        self.assertIn("email", bad_email["message"])

    def test_webhook_activation_waits_for_a_live_authenticated_event(self, _dns):
        cid, secret = self.new_project()
        mapping = {"external_customer_id": "external_customer_id", "customer_name": "customer_name", "email": "email", "contact_number": "contact_number"}
        self.staff.put(f"{ADM}projects/PSH001/api-config/", {**CONFIG, "sync_mode": "PUSH", "field_mapping": mapping}, format="json")
        body = {"project_code": "PSH001", "external_customer_id": "E1", "customer_name": "Live", "email": "live@example.com", "contact_number": "9876500001"}
        self.staff.post(f"{ADM}projects/PSH001/validate/customer/", {}, format="json")
        self.staff.post(f"{ADM}projects/PSH001/validate/mapping/", {"sample": json.dumps(body)}, format="json")
        r = self.staff.post(f"{ADM}projects/PSH001/activate/", {}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertIn("AWAITING_LIVE_WEBHOOK", json.dumps(r.data))
        self.assertIn(self.call("customers/register", body, cid=cid, secret=secret).status_code, (200, 201, 202))  # first real authenticated event
        self.assertEqual(self.staff.post(f"{ADM}projects/PSH001/activate/", {}, format="json").status_code, 200)
        self.assertFalse(IntegrationProject.objects.get(code="MOTHYS").license_policy == "CALENDAR")  # legacy MOTHYS untouched

    def test_end_to_end_webhook_registration_pending_list_save_and_allow_single_email(self, _dns):
        from django.core import mail
        from .models import IntegrationCustomer, IntegrationRegistration
        cid, secret = self.new_project()
        base = f"{ADM}projects/PSH001/universe-customers/"
        body = {"project_code": "PSH001", "external_customer_id": "C-77", "customer_name": "Kavya", "email": "kavya@example.com",
                "contact_number": "9876500077", "company_name": "Kavya Traders"}
        mail.outbox.clear()
        r = self.call("customers/register", body, cid=cid, secret=secret, HTTP_IDEMPOTENCY_KEY="evt-77")
        self.assertEqual(r.data["data"]["status"], "PENDING_REVIEW")
        self.assertEqual(len(mail.outbox), 0)  # approval first: no credentials before Save & Allow
        self.call("customers/register", body, cid=cid, secret=secret, HTTP_IDEMPOTENCY_KEY="evt-77")  # retried delivery
        self.assertEqual(IntegrationRegistration.objects.filter(project__code="PSH001", external_customer_id="C-77").count(), 1)
        rows = self.staff.get(base).data["results"]
        row = next(x for x in rows if x.get("email") == "kavya@example.com")  # appears automatically, no re-entry
        self.assertEqual(row["kind"], "registration")
        self.assertIn("Pending", row["status"])
        reg = IntegrationRegistration.objects.get(project__code="PSH001", external_customer_id="C-77")
        popup = self.staff.get(f"{base}registration/{reg.pk}/?license_type=ONE_YEAR&years=1").data["record"]
        self.assertEqual(popup["registration"]["company_name"], "Kavya Traders")
        out = self.staff.post(f"{base}registration/{reg.pk}/approve/", {"license_type": "ONE_YEAR", "years": 1}, format="json").data["result"]
        self.assertRegex(out["user_id"], r"^9\d{8}$")
        cust = IntegrationCustomer.objects.get(project__code="PSH001", external_customer_id="C-77")
        self.assertEqual((cust.name, cust.email, cust.license.license_type), ("Kavya", "kavya@example.com", "ONE_YEAR"))
        sent = [m for m in mail.outbox if "kavya@example.com" in m.to]
        self.assertEqual(len(sent), 1)  # the welcome email, once
        self.assertIn(out["user_id"], sent[0].body)
        self.assertNotIn(secret, sent[0].body)  # never the Nexus API secret
        again = self.staff.post(f"{base}registration/{reg.pk}/approve/", {"license_type": "ONE_YEAR"}, format="json").data["result"]
        self.assertTrue(again["already_approved"])
        self.assertEqual(len([m for m in mail.outbox if "kavya@example.com" in m.to]), 1)  # repeated Save & Allow: no duplicate email
        self.assertEqual(IntegrationCustomer.objects.filter(project__code="PSH001").count(), 1)
        self.assertFalse(IntegrationCustomer.objects.filter(project__code="MOTHYS", email="kavya@example.com").exists())


@mock.patch("apps.integrations.pull.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
class SyncModeValidationTests(Base):
    """API Setup Save: polling-only settings are validated only for Scheduled Polling."""

    def put(self, **over):
        return self.staff.put(f"{ADM}projects/MOTHYS/api-config/", {**CONFIG, **over}, format="json")

    def test_webhook_save_ignores_polling_only_fields_and_external_auth(self, _dns):
        bad_poll = {"sync_interval_minutes": "", "pagination": {"type": "page", "page_size": 0}, "customer_path": "", "customer_method": "PATCH"}
        with mock.patch("apps.integrations.pull.requests.request") as http:
            r = self.put(sync_mode="PUSH", auth_type="BEARER", credential_ref="", **bad_poll)  # the reported error case
            r2 = self.put(sync_mode="PUSH", auth_type="CUSTOM", credential_ref="", auth_config={"strategy": "STATIC_HEADER"})  # incomplete external auth
        self.assertFalse(http.called)  # saving never calls GET /customers
        self.assertEqual(r.status_code, 200, r.data)
        cfg = r.data["config"]
        self.assertEqual((cfg["sync_mode"], cfg["customer_path"], cfg["sync_interval_minutes"], cfg["pagination"]["page_size"]), ("PUSH", "", 60, 100))
        self.assertEqual(r2.status_code, 200, r2.data)

    def test_webhook_still_validates_external_auth_when_nexus_calls_other_project_apis(self, _dns):
        r = self.put(sync_mode="PUSH", product_path="/product", auth_type="CUSTOM", credential_ref="", auth_config={"strategy": "STATIC_HEADER"})
        self.assertEqual(r.status_code, 400)

    def test_polling_keeps_all_its_validation(self, _dns):
        self.assertIn("pagination", self.put(sync_mode="POLL", sync_interval_minutes="").data["errors"])
        self.assertIn("pagination", self.put(sync_mode="POLL", pagination={"type": "page", "page_size": 0}).data["errors"])
        self.assertIn("customer_path", self.put(sync_mode="POLL", customer_path="").data["errors"])
        self.assertIn("credential_ref", self.put(sync_mode="POLL", auth_type="CUSTOM", credential_ref="", auth_config={"strategy": "STATIC_HEADER"}).data["errors"])
        self.assertEqual(self.put(sync_mode="POLL").status_code, 200)

    def test_switching_poll_to_webhook_keeps_valid_values_and_nexus_credentials_stay_mandatory(self, _dns):
        self.assertEqual(self.put(sync_mode="POLL", sync_interval_minutes=30).status_code, 200)
        r = self.put(sync_mode="PUSH", sync_interval_minutes=30)
        self.assertEqual((r.status_code, r.data["config"]["sync_interval_minutes"]), (200, 30))
        body = {"project_code": "MOTHYS", "external_customer_id": "W1", "customer_name": "W", "email": "w@example.com", "contact_number": "9876500011"}
        self.assertEqual(self.call("customers/register", body, secret="wrong").status_code, 401)  # inbound still needs Client ID + Secret

    def test_detect_fields_requires_project_configure_permission(self, _dns):
        PushModeSyncTests.new_project(self)
        admin, _ = ensure_account("iadmin-detect", Role.ADMIN, "pw")
        c = APIClient()
        c.force_authenticate(admin)
        url = f"{ADM}projects/PSH001/detect-fields/"
        sample = {"sample": json.dumps({"external_customer_id": "C1", "customer_name": "K", "email": "k@example.com", "contact_number": "9876543210"})}
        self.assertEqual(APIClient().post(url, sample, format="json").status_code, 401)  # anonymous
        self.assertEqual(c.post(url, sample, format="json").status_code, 403)  # Admin without a grant for this project
        UserModulePermission.objects.create(user=admin, module="PSH001:api", can_edit=True)
        self.assertEqual(c.post(url, sample, format="json").status_code, 200)
        self.assertEqual(c.post(f"{ADM}projects/MOTHYS/detect-fields/", sample, format="json").status_code, 403)  # no cross-project access

    def test_full_universal_sequence_registration_list_popup_save_and_allow_email_and_logs(self, _dns):
        from django.core import mail
        from .models import IntegrationCustomer, IntegrationLicense
        cid, secret = PushModeSyncTests.new_project(self)
        base = f"{ADM}projects/PSH001/universe-customers/"
        body = {"project_code": "PSH001", "external_customer_id": "LOCAL-TEST-006", "customer_name": "Nexus Manual Test", "email": "test-inbox@example.com", "contact_number": "9876543210"}
        mail.outbox.clear()
        # 1-2: wrong credentials rejected and logged against the project (secret never logged)
        bad = self.call("customers/register", body, cid=cid, secret="wrong-secret")
        self.assertEqual(bad.status_code, 401)
        # 1, 3: correct credentials accepted, saved under the project
        ok = self.call("customers/register", body, cid=cid, secret=secret)
        self.assertEqual((ok.status_code in (200, 201, 202), ok.data["data"]["status"]), (True, "PENDING_REVIEW"))
        # 4-5: in the Customer List as pending; 6: nothing issued yet; 7: no duplicate
        self.call("customers/register", body, cid=cid, secret=secret)
        rows = [r for r in self.staff.get(base).data["results"] if r["email"] == "test-inbox@example.com"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["status"]), ("registration", "Pending Review"))
        self.assertEqual((len(mail.outbox), IntegrationCustomer.objects.filter(project__code="PSH001").count()), (0, 0))
        # popup: customer details + licence configuration preview, no secrets
        popup = self.staff.get(f"{base}registration/{rows[0]['id']}/?license_type=TRIAL&trial_days=5")
        rec = popup.data["record"]
        self.assertEqual((rec["registration"]["name"], rec["registration"]["email"], rec["registration"]["contact_number"]), ("Nexus Manual Test", "test-inbox@example.com", "9876543210"))
        self.assertIn("9-digit serial", rec["account"]["user_id_rule"])
        self.assertNotIn(secret, popup.content.decode())
        # Save & Allow: Trial 5 days -> 9-digit User ID, licence saved, one welcome email without the API secret
        res = self.staff.post(f"{base}registration/{rows[0]['id']}/approve/", {"license_type": "TRIAL", "trial_days": 5}, format="json").data["result"]
        self.assertRegex(res["user_id"], r"^9\d{8}$")
        lic = IntegrationLicense.objects.get(customer__project__code="PSH001", customer__external_customer_id="LOCAL-TEST-006")
        self.assertEqual(lic.license_type, "TRIAL")
        self.assertEqual((lic.expires_at - lic.starts_at).days, 5)
        sent = [m for m in mail.outbox if "test-inbox@example.com" in m.to]
        self.assertEqual(len(sent), 1)
        self.assertIn(res["user_id"], sent[0].body)
        self.assertNotIn(secret, sent[0].body)
        self.staff.post(f"{base}registration/{rows[0]['id']}/approve/", {"license_type": "TRIAL"}, format="json")  # repeated click
        self.assertEqual((IntegrationLicense.objects.filter(customer__project__code="PSH001").count(), len([m for m in mail.outbox if "test-inbox@example.com" in m.to])), (1, 1))
        # row updated
        row = next(r for r in self.staff.get(base).data["results"] if r["email"] == "test-inbox@example.com")
        self.assertEqual((row["kind"], row["user_id"], row["license_type"]), ("customer", res["user_id"], "Trial"))
        # Sync Logs: registration received, approval and the rejected request are all visible for this project
        actions = [a["action"] for a in self.staff.get(f"{ADM}projects/PSH001/audit/").data["audit"]]
        for expected in ("registration.received", "registration.save_and_allow", "api.unauthenticated"):
            self.assertIn(expected, actions)
        self.assertNotIn("wrong-secret", self.staff.get(f"{ADM}projects/PSH001/audit/").content.decode())

    def test_rotation_issues_new_pair_invalidates_old_and_never_logs_the_secret(self, _dns):
        from .models import IntegrationAudit, IntegrationCredential
        old_cid, old_secret = PushModeSyncTests.new_project(self)
        body = {"project_code": "PSH001", "external_customer_id": "R1", "customer_name": "Rotate Test", "email": "rot@example.com", "contact_number": "9876500021"}
        self.assertEqual(self.staff.get(f"{ADM}projects/PSH001/").status_code, 200)  # opening / refreshing the page never rotates
        self.assertEqual(IntegrationCredential.objects.filter(project__code="PSH001").count(), 1)
        r = self.staff.post(f"{ADM}projects/PSH001/rotate-credentials/", {}, format="json").data["credentials"]
        new_cid, new_secret = r["client_id"], r["api_secret"]
        self.assertNotEqual((new_cid, new_secret), (old_cid, old_secret))
        self.assertEqual(self.staff.get(f"{ADM}projects/PSH001/").data["project"]["client_id"], new_cid)  # header shows the valid Client ID
        self.assertEqual(self.call("customers/register", body, cid=old_cid, secret=old_secret).status_code, 401)  # old pair stops working
        self.assertEqual(self.call("customers/register", body, cid=new_cid, secret=old_secret).status_code, 401)  # old secret + new id: no
        self.assertIn(self.call("customers/register", body, cid=new_cid, secret=new_secret).status_code, (200, 201, 202))  # new pair works
        self.assertNotIn(new_secret, self.staff.get(f"{ADM}projects/PSH001/").content.decode())  # never shown again
        self.assertFalse(IntegrationAudit.objects.filter(detail__contains=new_secret).exists() or IntegrationAudit.objects.filter(subject__contains=new_secret).exists())
        self.assertEqual(self.client.post(f"{ADM}projects/PSH001/rotate-credentials/", {}, format="json").status_code, 401)  # anonymous cannot rotate
        self.assertTrue(IntegrationCredential.objects.filter(project__code="MOTHYS", active=True).exists())  # other projects untouched

    def _pending(self, ext="AP1", email="ap1@example.com"):
        from .models import IntegrationRegistration
        if not hasattr(self, "_creds"):
            self._creds = PushModeSyncTests.new_project(self)
        cid, secret = self._creds
        self.call("customers/register", {"project_code": "PSH001", "external_customer_id": ext, "customer_name": "Approve Test", "email": email, "contact_number": "9876500031"}, cid=cid, secret=secret)
        return IntegrationRegistration.objects.get(project__code="PSH001", external_customer_id=ext)

    def test_approval_user_id_password_overrides_and_custom_license(self, _dns):
        from django.contrib.auth.hashers import check_password as hash_ok
        from django.core import mail
        from .models import IntegrationActivationKey, IntegrationCustomer
        reg = self._pending()
        url = f"{ADM}projects/PSH001/universe-customers/registration/{reg.pk}/"
        popup = self.staff.get(url).data["record"]
        self.assertRegex(popup["account"]["suggested_user_id"], r"^9\d{8}$")
        ap = lambda **b: self.staff.post(url + "approve/", {"license_type": "TRIAL", **b}, format="json")  # noqa: E731
        self.assertEqual(ap(user_id="812345678").status_code, 422)  # must start with 9
        self.assertEqual(ap(user_id="91234567").status_code, 422)  # must be 9 digits
        self.assertEqual(ap(password="12").status_code, 422)  # password policy: at least 3 characters (no complexity rule)
        self.assertEqual(ap(license_type="CUSTOM").status_code, 422)  # custom needs days
        self.assertEqual(ap(license_type="CUSTOM", custom_days=0).status_code, 422)
        mail.outbox.clear()
        r = ap(license_type="CUSTOM", custom_days=45, user_id="912340001", password="Welcome2Nexus").data["result"]
        self.assertEqual(r["user_id"], "912340001")
        c = IntegrationCustomer.objects.get(user_id="912340001")
        self.assertTrue(hash_ok("Welcome2Nexus", c.password_hash))  # stored only as a hash
        self.assertNotEqual(c.password_hash, "Welcome2Nexus")
        self.assertTrue(c.must_change_password)
        self.assertEqual((c.license.license_type, (c.license.expires_at - c.license.starts_at).days), ("CUSTOM", 45))
        key = IntegrationActivationKey.objects.get(license=c.license, internal=True)  # internal activation reference: hash only
        self.assertEqual(len(key.key_hash), 64)
        body = mail.outbox[0].body
        self.assertIn("Welcome2Nexus", body)
        self.assertIn("912340001", body)
        self.assertNotIn(key.key_hash, body)
        reg2 = self._pending("AP2", "ap2@example.com")  # duplicate User ID is refused
        self.assertEqual(self.staff.post(f"{ADM}projects/PSH001/universe-customers/registration/{reg2.pk}/approve/", {"license_type": "TRIAL", "user_id": "912340001"}, format="json").status_code, 409)

    def test_yearly_and_lifetime_terms(self, _dns):
        from .models import IntegrationCustomer
        reg = self._pending("Y1", "y1@example.com")
        r = self.staff.post(f"{ADM}projects/PSH001/universe-customers/registration/{reg.pk}/approve/", {"license_type": "ONE_YEAR", "years": 3}, format="json").data["result"]
        lic = IntegrationCustomer.objects.get(user_id=r["user_id"]).license
        self.assertEqual(lic.expires_at.year - lic.starts_at.year, 3)
        reg = self._pending("L1", "l1@example.com")
        r = self.staff.post(f"{ADM}projects/PSH001/universe-customers/registration/{reg.pk}/approve/", {"license_type": "LIFETIME"}, format="json").data["result"]
        self.assertIsNone(IntegrationCustomer.objects.get(user_id=r["user_id"]).license.expires_at)

    def test_welcome_email_content_no_localhost_link_and_reset_for_approved(self, _dns):
        from django.contrib.auth.hashers import check_password as hash_ok
        from django.core import mail
        from .models import IntegrationCustomer
        reg = self._pending("EM1", "em1@example.com")
        mail.outbox.clear()
        url = f"{ADM}projects/PSH001/universe-customers/registration/{reg.pk}/approve/"
        r = self.staff.post(url, {"license_type": "ONE_YEAR", "years": 2, "password": "Edited7Pass"}, format="json", HTTP_ORIGIN="http://localhost:5173").data["result"]
        body = mail.outbox[0].body
        for expected in ("Customer Name: Approve Test", "Product Name: Push Project", f"User ID / Serial Number: {r['user_id']}", "Temporary Password: Edited7Pass",
                         "License Type: Yearly", "License Duration: 2 years", "Expiry Date: ", "Login URL: https://push.example.com"):
            self.assertIn(expected, body)
        self.assertNotIn("localhost", body)  # no dev setup link in a customer email
        self.assertNotIn("setup-customer-password", body)
        c = IntegrationCustomer.objects.get(user_id=r["user_id"])
        self.assertTrue(hash_ok("Edited7Pass", c.password_hash) and c.must_change_password)
        self.staff.post(url, {"license_type": "ONE_YEAR"}, format="json")  # repeated Save & Allow
        self.assertEqual(len([m for m in mail.outbox if "em1@example.com" in m.to]), 1)  # one welcome email only
        # approved account: authorised Reset Temporary Password (new hash, shown once to staff, not emailed, sessions revoked)
        mail.outbox.clear()
        reset = self.staff.post(f"{ADM}projects/PSH001/universe-customers/customer/{c.pk}/reset-password/", {}, format="json")
        self.assertEqual(reset.status_code, 200, reset.data)
        newpw = reset.data["result"]["temporary_password"]
        c.refresh_from_db()
        self.assertTrue(hash_ok(newpw, c.password_hash) and not hash_ok("Edited7Pass", c.password_hash) and c.must_change_password)
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn(newpw, self.staff.get(f"{ADM}projects/PSH001/universe-customers/customer/{c.pk}/").content.decode())  # never shown again

    # ---------------------------------------------------------------- developer credential handoff
    def _send(self, **body):
        from django.core.cache import cache
        cache.delete_pattern("*int:docs:lock*") if hasattr(cache, "delete_pattern") else cache.clear()
        return self.staff.post(f"{ADM}projects/PSH001/send-docs/", {"to": "dev@example.com", **body}, format="json", HTTP_ORIGIN="https://nexus.example.com")

    def test_developer_email_is_concise_has_client_id_and_single_link_that_rotates_secret_once(self, _dns):
        import re as _re
        from django.core import mail
        from .models import IntegrationAudit
        cid, old_secret = PushModeSyncTests.new_project(self)
        self.assertEqual(self.staff.post(f"{ADM}projects/PSH001/send-docs/", {"to": "dev@example.com", "provision_secret": True}, format="json").status_code, 403)  # no confirmation
        mail.outbox.clear()
        r = self._send(provision_secret=True, confirm=True)
        self.assertEqual((r.status_code, r.data["credential_link"], r.data["to"], r.data["project"]), (200, "sent", "dev@example.com", "PSH001"))
        self.assertNotIn("verification_code", r.data)  # no separate code any more
        body = mail.outbox[0].body
        for expected in (f"Client ID          : {cid}", "Project            : Push Project (PSH001)", "customers/register", "X-Client-Id", "HTTP 202", "PENDING_REVIEW",
                         "devices/authorize", "TRANSFER_CONFIRMATION_REQUIRED", "sessions/introspect"):
            self.assertIn(expected, body)
        self.assertLess(len(body), 4000)  # concise - the full manual is a separate download
        self.assertNotIn(old_secret, body)
        token = _re.search(r"developer-credentials\?token=(\S+)", body).group(1)
        redeem = lambda t: self.client.post("/api/integrations/developer-credentials/redeem/", {"token": t}, content_type="application/json")  # noqa: E731
        ok = redeem(token)
        self.assertEqual(ok.status_code, 200)
        data = ok.json()["data"]
        self.assertEqual((data["client_id"], data["project_code"]), (cid, "PSH001"))  # same Client ID as in the email
        new_secret = data["api_secret"]
        self.assertIn("no-store", ok["Cache-Control"])
        reg = {"project_code": "PSH001", "external_customer_id": "H1", "customer_name": "Handoff", "email": "h1@example.com", "contact_number": "9876500041"}
        self.assertEqual(self.call("customers/register", reg, cid=cid, secret=old_secret).status_code, 401)  # previous secret stops working
        self.assertIn(self.call("customers/register", reg, cid=cid, secret=new_secret).status_code, (200, 201, 202))
        self.assertEqual(redeem(token).status_code, 400)  # single use
        for a in IntegrationAudit.objects.filter(project__code="PSH001"):
            self.assertNotIn(new_secret, f"{a.subject}{a.detail}")
            self.assertNotIn(token[:40], f"{a.subject}{a.detail}")

    def test_handoff_expiry_rotation_tamper_and_duplicate_send(self, _dns):
        import re as _re
        from django.core import mail
        PushModeSyncTests.new_project(self)
        mail.outbox.clear()
        self._send(provision_secret=True, confirm=True)
        token = _re.search(r"developer-credentials\?token=(\S+)", mail.outbox[0].body).group(1)
        self.assertEqual(self.staff.post(f"{ADM}projects/PSH001/send-docs/", {"to": "dev@example.com"}, format="json").status_code, 409)  # duplicate within 30 s
        redeem = lambda t: self.client.post("/api/integrations/developer-credentials/redeem/", {"token": t}, content_type="application/json")  # noqa: E731
        with mock.patch("django.core.signing.time.time", return_value=__import__("time").time() + 25 * 3600):
            self.assertEqual(redeem(token).json()["error"]["code"], "HANDOFF_EXPIRED")
        self.staff.post(f"{ADM}projects/PSH001/rotate-credentials/", {}, format="json")  # an authorised rotation cancels outstanding links
        self.assertEqual(redeem(token).json()["error"]["code"], "HANDOFF_USED")
        self.assertEqual(redeem(token[:-2] + "xx").json()["error"]["code"], "HANDOFF_INVALID")  # tampered / other project
        mail.outbox.clear()
        r = self._send()  # documentation only: Client ID, no link
        self.assertEqual(r.data["credential_link"], "not_sent")
        self.assertNotIn("developer-credentials", mail.outbox[0].body)

    def test_deleted_test_customer_can_register_again_and_rejections_are_logged(self, _dns):
        from .models import IntegrationAudit, IntegrationCustomer, IntegrationRegistration
        cid, secret = PushModeSyncTests.new_project(self)
        base = f"{ADM}projects/PSH001/universe-customers/"
        body = {"project_code": "PSH001", "external_customer_id": "DEL-1", "customer_name": "Delete Me", "email": "del@example.com", "contact_number": "9876500051"}
        self.assertEqual(self.call("customers/register", body, cid=cid, secret=secret).status_code, 202)
        reg = IntegrationRegistration.objects.get(project__code="PSH001", external_customer_id="DEL-1")
        self.staff.post(f"{base}registration/{reg.pk}/approve/", {"license_type": "TRIAL"}, format="json")
        cust = IntegrationCustomer.objects.get(project__code="PSH001", external_customer_id="DEL-1")
        dup = self.call("customers/register", body, cid=cid, secret=secret)  # while approved: a clear conflict, not a fake success
        self.assertEqual(dup.status_code, 409)
        self.assertTrue(IntegrationAudit.objects.filter(project__code="PSH001", action="registration.rejected", detail="CUSTOMER_ALREADY_REGISTERED").exists())
        self.assertEqual(self.staff.delete(f"{base}customer/{cust.pk}/").status_code in (200, 204), True)  # permanent delete of the TEST customer
        self.assertFalse(IntegrationRegistration.objects.filter(project__code="PSH001", external_customer_id="DEL-1").exists())
        again = self.call("customers/register", body, cid=cid, secret=secret)
        self.assertEqual((again.status_code, again.data["data"]["status"]), (202, "PENDING_REVIEW"))  # a genuinely new pending record
        rows = [x for x in self.staff.get(base).data["results"] if x["email"] == "del@example.com"]
        self.assertEqual((len(rows), rows[0]["kind"], rows[0]["status"]), (1, "registration", "Pending Review"))  # visible again, exactly once

    def test_local_environment_is_labelled(self, _dns):
        from django.core import mail
        PushModeSyncTests.new_project(self)
        mail.outbox.clear()
        from django.core.cache import cache
        cache.clear()
        r = self.staff.post(f"{ADM}projects/PSH001/send-docs/", {"to": "dev@example.com"}, format="json", HTTP_ORIGIN="http://localhost:5173")
        self.assertIn("LOCAL", r.data["environment"])
        self.assertIn("[LOCAL]", mail.outbox[0].subject)
