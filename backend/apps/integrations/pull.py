"""Configurable PULL synchronisation of customers from a project's own registration API (Universal API projects).

Additive: every customer goes through the existing, idempotent `services.register_customer`, so serial generation, licensing, welcome
e-mail and duplicate rules are exactly the ones the push API uses. Nothing here touches PRN, GSTR2Tally or Tally SL."""
import base64
import ipaddress
import json
import os
import re
import socket
import time
from datetime import timedelta
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from . import services as s
from . import tally as tally_service
from . import universe
from .models import IntegrationApiConfig, IntegrationProject, IntegrationSyncCheckpoint
from .services import IntegrationError

REQUIRED_MAP = ("external_customer_id", "customer_name", "email", "contact_number")
MAX_BODY = 5 * 1024 * 1024
RETRIES = 3
BACKOFF = 0.5  # seconds, doubled per attempt (tests set it to 0)


class UnsafeUrl(Exception):
    pass


def _blocked(ip):
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def validate_outbound_url(url):
    """SSRF guard for administrator-configured URLs: http(s) only, no credentials in the URL, and the host must not resolve to a
    private / loopback / link-local / metadata address. Local addresses are allowed only when NEXUS_ALLOW_PRIVATE_INTEGRATION_URLS is on
    (development). Redirects are never followed by the caller, so a validated host cannot bounce us elsewhere."""
    parts = urlparse(str(url or ""))
    allow_private = getattr(settings, "NEXUS_ALLOW_PRIVATE_INTEGRATION_URLS", settings.DEBUG)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise UnsafeUrl("Use a full http(s) URL.")
    if parts.scheme == "http" and not allow_private:
        raise UnsafeUrl("HTTPS is required.")
    if parts.username or parts.password:
        raise UnsafeUrl("Credentials must not be placed in the URL.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeUrl("The host name cannot be resolved.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip == ipaddress.ip_address("169.254.169.254") or (_blocked(ip) and not allow_private):
            raise UnsafeUrl("This address is not allowed as an integration target.")
    return url


def _headers(cfg):
    headers = {"Accept": "application/json", "User-Agent": "Nexus-Customer-Sync"}
    try:
        headers.update({str(k): str(v) for k, v in json.loads(cfg.request_headers or "{}").items()})
    except (ValueError, AttributeError):
        pass
    secret = os.environ.get(cfg.credential_ref, "") if cfg.credential_ref else ""  # the reference is an env-var name; the secret is never stored
    if cfg.auth_type == "BEARER" and secret:
        headers["Authorization"] = f"Bearer {secret}"
    elif cfg.auth_type == "API_KEY_HEADER" and secret:
        headers[cfg.auth_header or "X-API-Key"] = secret
    elif cfg.auth_type == "BASIC":
        if not secret:
            raise IntegrationError("AUTH_CREDENTIAL_MISSING", "The password environment variable is not set on the Nexus server.", 400)
        pair = f"{_auth_cfg(cfg).get('username', '')}:{secret}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    elif cfg.auth_type in ("CUSTOM", "OAUTH2"):
        _custom_auth(cfg, headers)
    return headers


# ------------------------------------------------------------------ API type / protocol
PROTOCOLS = ("REST", "GRAPHQL", "SOAP", "OTHER")
SUPPORTED_PROTOCOLS = ("REST",)  # the only adapter implemented; the others are saved as drafts and can never be validated or activated


def _protocol(cfg):
    try:
        out = json.loads(getattr(cfg, "api_protocol", "") or "{}")
    except ValueError:
        out = {}
    return out if isinstance(out, dict) and out.get("type") else {"type": "REST"}


def protocol_supported(cfg):
    return _protocol(cfg)["type"] in SUPPORTED_PROTOCOLS


def validate_protocol(raw):
    raw = raw if isinstance(raw, dict) else {"type": "REST"}
    kind = str(raw.get("type") or "REST").upper()
    if kind not in PROTOCOLS:
        return {}, {"api_protocol": ["Choose REST API (JSON), GraphQL API, SOAP / XML API or Other."]}
    out = {"type": kind}
    if kind == "OTHER":
        out.update({k: str(raw.get(k) or "").strip()[:120] for k in ("custom_type", "description", "content_type", "request_format", "response_format")})
        if not out["custom_type"]:
            return {}, {"api_custom_type": ["Enter the API type supplied by the external development team."]}
    return out, {}


# ------------------------------------------------------------------ CUSTOM ("Other") authentication
AUTH_TYPES = ("NONE", "BEARER", "API_KEY_HEADER", "BASIC", "OAUTH2", "CUSTOM")
STRATEGIES = ("STATIC_HEADER", "LOGIN_TOKEN", "OAUTH2_CLIENT_CREDENTIALS")
HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_|~-]{1,60}$")
FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "cookie", "proxy-authorization", "upgrade", "te", "trailer"}
ENV_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
PLACEHOLDERS = ("${SECRET}", "${SECRET2}")  # the ONLY substitutions in a login body; nothing is evaluated
TOKEN_SKEW = 30  # seconds: a cached token is renewed this long before it expires


def _env(name):
    return os.environ.get(name, "") if name else ""


def _auth_cfg(cfg):
    try:
        out = json.loads(getattr(cfg, "auth_config", "") or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _token_key(cfg):
    return f"nexus:ext-token:{cfg.project_id}"


def forget_token(cfg):
    cache.delete(_token_key(cfg))


def _uses_token(cfg):
    return cfg.auth_type in ("CUSTOM", "OAUTH2") and _auth_cfg(cfg).get("strategy") in ("LOGIN_TOKEN", "OAUTH2_CLIENT_CREDENTIALS")


def _fill(value, cfg, ac):
    """Replaces the exact strings ${SECRET} / ${SECRET2} with the referenced server env values. No other syntax is interpreted."""
    if isinstance(value, dict):
        return {str(k): _fill(v, cfg, ac) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, cfg, ac) for v in value]
    if value in PLACEHOLDERS:
        secret = _env(cfg.credential_ref if value == "${SECRET}" else ac.get("secondary_ref", ""))
        if not secret:
            raise IntegrationError("AUTH_CREDENTIAL_MISSING", f"The server environment variable for {value} is not set.", 400)
        return secret
    return value


def _fetch_token(cfg, ac):
    """Calls the configured login / OAuth2 token endpoint from the Nexus backend. Returns (token, cache_seconds). Errors never contain secrets."""
    url = ac.get("token_url", "")
    if url.startswith("/"):
        url = cfg.project.backend_url.rstrip("/") + url
    try:
        validate_outbound_url(url)
    except UnsafeUrl as exc:
        raise IntegrationError("UNSAFE_URL", f"Authentication endpoint: {exc}", 400)
    method = ac.get("token_method", "POST")
    kwargs = {"headers": {"Accept": "application/json", "User-Agent": "Nexus-Customer-Sync"}, "timeout": 10, "allow_redirects": False, "stream": True}
    if ac.get("strategy") == "OAUTH2_CLIENT_CREDENTIALS":
        secret = _env(cfg.credential_ref)
        if not secret:
            raise IntegrationError("AUTH_CREDENTIAL_MISSING", "The client-secret environment variable is not set on the Nexus server.", 400)
        kwargs["data"] = {"grant_type": "client_credentials", "client_id": ac.get("client_id", ""), "client_secret": secret, **({"scope": ac["scope"]} if ac.get("scope") else {})}
    elif method == "GET":
        kwargs["params"] = _fill(ac.get("body") or {}, cfg, ac)
    else:
        kwargs["json"] = _fill(ac.get("body") or {}, cfg, ac)
    try:
        r = requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise IntegrationError("AUTH_ENDPOINT_UNAVAILABLE", f"The authentication endpoint could not be reached ({type(exc).__name__}).", 502)
    if r.is_redirect or r.is_permanent_redirect:
        raise IntegrationError("EXTERNAL_REDIRECT_BLOCKED", "The authentication endpoint answered with a redirect; redirects are not followed.", 502)
    if r.status_code in (401, 403):
        raise IntegrationError("AUTH_REJECTED", f"The authentication endpoint rejected the configured credentials (HTTP {r.status_code}).", 502)
    if r.status_code >= 400:
        raise IntegrationError("AUTH_FAILED", f"The authentication endpoint answered HTTP {r.status_code}.", 502)
    try:
        data = json.loads(r.raw.read(MAX_BODY + 1, decode_content=True))
    except ValueError:
        raise IntegrationError("AUTH_FAILED", "The authentication endpoint did not return JSON.", 502)
    token = dig(data, ac.get("token_path"))
    if not isinstance(token, str) or not token:
        raise IntegrationError("AUTH_TOKEN_NOT_FOUND", f"No token was found at '{ac.get('token_path')}' in the authentication response.", 502)
    ttl = None
    if ac.get("expires_in_path"):
        try:
            ttl = int(dig(data, ac["expires_in_path"]))
        except (TypeError, ValueError):
            ttl = None
    return token, max((ttl or int(ac.get("token_ttl_seconds") or 3600)) - TOKEN_SKEW, 0)


def _custom_auth(cfg, headers):
    ac = _auth_cfg(cfg)
    strategy, name, prefix = ac.get("strategy"), ac.get("header_name") or "Authorization", ac.get("header_prefix", "")
    if strategy == "STATIC_HEADER":
        secret = _env(cfg.credential_ref)
        if not secret:
            raise IntegrationError("AUTH_CREDENTIAL_MISSING", "The token / secret environment variable is not set on the Nexus server.", 400)
        headers[name] = f"{prefix}{secret}"
    elif strategy in ("LOGIN_TOKEN", "OAUTH2_CLIENT_CREDENTIALS"):
        token = cache.get(_token_key(cfg))
        if not token:
            token, ttl = _fetch_token(cfg, ac)
            if ttl:
                cache.set(_token_key(cfg), token, ttl)
        headers[name] = f"{prefix}{token}"
    else:
        raise IntegrationError("AUTH_UNSUPPORTED", "This custom authentication strategy is not supported.", 400)
    headers.update({str(k): str(v) for k, v in (ac.get("extra_headers") or {}).items()})


def _single_line(v):
    return "\r" not in str(v) and "\n" not in str(v)


def validate_auth_config(raw, auth_type, credential_ref):
    """BASIC / OAUTH2 / CUSTOM authentication settings: only declared fields are kept; secrets are env-variable references, never values."""
    raw = raw if isinstance(raw, dict) else {}
    if auth_type == "BASIC":
        username = str(raw.get("username") or "").strip()[:120]
        errors = {} if username else {"auth_username": ["Enter the Basic Authentication user name."]}
        if not credential_ref:
            errors["credential_ref"] = ["Enter the server environment variable that holds the password."]
        return {"username": username}, errors
    if auth_type == "OAUTH2":
        raw = {**raw, "strategy": "OAUTH2_CLIENT_CREDENTIALS"}
    elif auth_type != "CUSTOM":
        return {}, {}
    strategy = raw.get("strategy")
    if strategy not in STRATEGIES:
        return {}, {"auth_strategy": ["Choose Static Header, Login API Token or OAuth2 Client Credentials."]}
    token_based = strategy != "STATIC_HEADER"
    errors = {}
    out = {"strategy": strategy, "description": str(raw.get("description") or "")[:120]}
    name = str(raw.get("header_name") or "Authorization").strip()
    if not HEADER_NAME.match(name) or name.lower() in FORBIDDEN_HEADERS:
        errors["auth_header_name"] = ["Enter a valid header name, e.g. Authorization, X-Auth-Token or X-API-Key."]
    out["header_name"] = name
    prefix = raw.get("header_prefix")
    prefix = ("Bearer " if token_based else "") if prefix is None else str(prefix)
    if len(prefix) > 30 or not _single_line(prefix):
        errors["auth_header_prefix"] = ["The prefix is one line of at most 30 characters, e.g. 'Bearer '."]
    out["header_prefix"] = prefix
    if not credential_ref:
        errors["credential_ref"] = ["Enter the server environment variable that holds the token / secret."]
    extra = raw.get("extra_headers") or {}
    if not isinstance(extra, dict) or any(not HEADER_NAME.match(str(k)) or str(k).lower() in FORBIDDEN_HEADERS or not _single_line(v) for k, v in extra.items()):
        errors["auth_extra_headers"] = ["Additional headers must be a JSON object of valid header names and one-line values (no secrets)."]
        extra = {}
    out["extra_headers"] = {str(k): str(v)[:200] for k, v in extra.items()}
    if token_based:
        url = str(raw.get("token_url") or "").strip()
        if not url.startswith(("/", "https://", "http://")):
            errors["auth_token_url"] = ["Enter the authentication endpoint: a backend path (/auth/login) or a full https:// URL."]
        out["token_url"] = url[:300]
        method = "POST" if strategy == "OAUTH2_CLIENT_CREDENTIALS" else str(raw.get("token_method") or "POST").upper()
        if method not in ("GET", "POST"):
            errors["auth_token_method"] = ["Use GET or POST."]
        out["token_method"] = method
        out["token_path"] = str(raw.get("token_path") or ("access_token" if strategy == "OAUTH2_CLIENT_CREDENTIALS" else "token")).strip()[:100]
        out["expires_in_path"] = str(raw.get("expires_in_path") or ("expires_in" if strategy == "OAUTH2_CLIENT_CREDENTIALS" else "")).strip()[:100]
        try:
            out["token_ttl_seconds"] = int(raw.get("token_ttl_seconds") or 3600)
            if not 60 <= out["token_ttl_seconds"] <= 86400:
                raise ValueError
        except (TypeError, ValueError):
            errors["auth_token_ttl"] = ["Token lifetime must be 60-86400 seconds."]
    if strategy == "LOGIN_TOKEN":
        body = raw.get("body") or {}
        if not isinstance(body, dict):
            errors["auth_body"] = ["The request body must be a JSON object."]
            body = {}
        flat = json.dumps(body)
        if "${" in flat.replace("${SECRET}", "").replace("${SECRET2}", ""):
            errors["auth_body"] = ["Only the exact placeholders ${SECRET} and ${SECRET2} are allowed; keep secrets in server environment variables."]
        out["body"] = body
        ref2 = str(raw.get("secondary_ref") or "").strip()
        if ref2 and not ENV_REF.match(ref2):
            errors["auth_secondary_ref"] = ["Enter an environment-variable name (letters, digits, underscore)."]
        if "${SECRET2}" in flat and not ref2:
            errors["auth_secondary_ref"] = ["${SECRET2} is used in the body: enter its environment-variable name."]
        out["secondary_ref"] = ref2
    if strategy == "OAUTH2_CLIENT_CREDENTIALS":
        out["client_id"] = str(raw.get("client_id") or "").strip()[:200]
        if not out["client_id"]:
            errors["auth_client_id"] = ["Enter the OAuth2 client id."]
        out["scope"] = str(raw.get("scope") or "").strip()[:200]
    return out, errors


def _get(cfg, params, path=None):
    if not protocol_supported(cfg):
        p = _protocol(cfg)
        raise IntegrationError("PROTOCOL_NOT_SUPPORTED", f"Protocol Not Supported Yet: {p.get('custom_type') or p['type']}. The configuration is saved as a draft; "
                               "only REST API (JSON) can be validated, synchronised or activated.", 400)
    url = cfg.project.backend_url.rstrip("/") + "/" + (path or cfg.customer_path).lstrip("/")
    try:
        validate_outbound_url(url)
    except UnsafeUrl as exc:
        raise IntegrationError("UNSAFE_URL", str(exc), 400)
    last, renewed = None, False
    for attempt in range(RETRIES):
        try:
            r = requests.request(cfg.customer_method, url, headers=_headers(cfg), params=params, timeout=10, allow_redirects=False, stream=True)
            if r.status_code == 401 and not renewed and _uses_token(cfg):
                forget_token(cfg)  # the cached token expired early or was revoked: obtain a new one once and retry
                renewed = True
                r = requests.request(cfg.customer_method, url, headers=_headers(cfg), params=params, timeout=10, allow_redirects=False, stream=True)
            if r.is_redirect or r.is_permanent_redirect:
                raise IntegrationError("EXTERNAL_REDIRECT_BLOCKED", "The project API answered with a redirect; redirects are not followed.", 502)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                hint = " - authentication was rejected; check the configured credentials" if r.status_code in (401, 403) else ""
                raise IntegrationError("EXTERNAL_API_REJECTED", f"The project API answered HTTP {r.status_code}{hint}.", 502)
            body = r.raw.read(MAX_BODY + 1, decode_content=True)
            if len(body) > MAX_BODY:
                raise IntegrationError("EXTERNAL_RESPONSE_TOO_LARGE", "The project API response is too large.", 502)
            return json.loads(body)
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF * (2 ** attempt))
    raise IntegrationError("EXTERNAL_API_UNAVAILABLE", f"The project API could not be read ({type(last).__name__}).", 502)


def dig(obj, path):
    """Dotted-path lookup: 'data.items', 'customer.email'. An empty path returns the object itself."""
    for part in [p for p in str(path or "").split(".") if p]:
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


def fetch_tally(project, external_id):
    """Tally License Details API of the project, queried per customer. None when the project has no such API configured."""
    cfg = IntegrationApiConfig.objects.filter(project=project).first()
    if cfg is None or not cfg.tally_path:
        return None
    body = _get(cfg, {cfg.tally_id_param or "customer_id": external_id}, path=cfg.tally_path)
    mapping = {k[6:]: v for k, v in json.loads(cfg.field_mapping).items() if k.startswith("tally_")}
    data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
    return {k: dig(data, path) for k, path in mapping.items()}


def _sample(cfg):
    pag = json.loads(cfg.pagination or "{}")
    params = {}
    if pag.get("type") == "page":
        params = {pag.get("page_param", "page"): 1, pag.get("size_param", "page_size"): min(int(pag.get("page_size", 100)), 5)}
    body = _get(cfg, params)
    items = dig(body, pag.get("results_path")) if pag.get("results_path") else body
    return body, items


def _mapped(cfg, item):
    return {nexus: dig(item, path) for nexus, path in json.loads(cfg.field_mapping).items()}


# ------------------------------------------------------------------ field auto-detection (sample JSON is parsed as data only, never stored)
INBOUND_FIELDS = ("external_customer_id", "customer_name", "email", "contact_number", "company_name", "address", "gstin")  # the push contract
CANDIDATES = {
    "external_customer_id": ("external_customer_id", "customer_id", "customerid", "cust_id", "client_id", "user_id", "id"),
    "customer_name": ("customer_name", "full_name", "fullname", "name", "display_name", "username"),
    "email": ("email", "email_address", "emailaddress", "mail", "email_id"),
    "contact_number": ("contact_number", "phone", "phone_number", "mobile", "mobile_number", "contact", "telephone"),
    "company_name": ("company_name", "company", "organisation", "organization", "business_name"),
    "address": ("address", "full_address", "street_address"),
    "gstin": ("gstin", "gst_number", "gst_no", "gst"),
}
MAX_SAMPLE = 20000


def parse_sample(raw):
    """Administrator-pasted sample: JSON object (or a list / {"data": [...]} whose first object is used). Returns (record, error)."""
    if isinstance(raw, (dict, list)):
        obj = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return None, "No Customer Payload Available - paste a sample JSON registration."
        if len(text) > MAX_SAMPLE:
            return None, "The sample is too large (max 20 KB). Paste one customer record."
        try:
            obj = json.loads(text)
        except ValueError as exc:
            return None, f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})."
    if isinstance(obj, dict) and not any(isinstance(v, (str, int, float)) for v in obj.values()):
        inner = next((v for v in obj.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
        obj = inner if inner is not None else obj
    if isinstance(obj, list):
        obj = next((x for x in obj if isinstance(x, dict)), None)
    if not isinstance(obj, dict) or not obj:
        return None, "The sample must be a JSON object describing one customer."
    return obj, None


def field_paths(obj, prefix="", depth=0):
    """Dotted paths of every scalar value (nested objects supported, lists are not descended)."""
    out = []
    for key, value in obj.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and depth < 4:
            out += field_paths(value, path + ".", depth + 1)
        elif not isinstance(value, list):
            out.append((path, type(value).__name__ if value is not None else "null"))
    return out


def suggest(paths):
    """Best candidate path per Nexus field, by the LAST path segment; each path is suggested at most once."""
    names = [p for p, _ in paths]
    used, out = set(), {}
    for field, cands in CANDIDATES.items():
        for cand in cands:
            hit = next((p for p in names if p.split(".")[-1].lower().replace("-", "_") == cand and p not in used), None)
            if hit:
                out[field] = hit
                used.add(hit)
                break
    return out


def detect_fields(project, sample=None):
    """Auto Detect Fields. POLL: one safe sample read of the customer API (nothing is imported). PUSH: only the pasted sample -
    the external registration API is never called just to discover fields."""
    cfg = IntegrationApiConfig.objects.filter(project=project).first()
    if cfg is None:
        return {"ok": False, "message": "Save the API configuration first."}
    if cfg.sync_mode in PUSH_MODES or sample not in (None, ""):
        record, error = parse_sample(sample)
        source = "sample"
        if error:
            return {"ok": False, "message": error, "needs_sample": True}
    else:
        try:
            _, items = _sample(cfg)
        except IntegrationError as exc:
            return {"ok": False, "message": exc.message, "code": exc.code}
        except UnsafeUrl as exc:
            return {"ok": False, "message": str(exc), "code": "UNSAFE_URL"}
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return {"ok": False, "message": "No customer record was found at the configured results path - paste a sample JSON instead.", "needs_sample": True}
        record, source = items[0], "api"
    paths = field_paths(record)
    return {"ok": True, "source": source, "fields": [{"path": p, "type": t} for p, t in paths], "suggested": suggest(paths),
            "message": f"{len(paths)} field(s) detected from the {'pasted sample' if source == 'sample' else 'customer API'}."}


def validate_mapping(mapping, record):
    """Required fields present, no path used twice, values are text/numbers (not objects)."""
    problems, mapped = [], {}
    paths = [v for v in mapping.values() if v]
    dupes = sorted({p for p in paths if paths.count(p) > 1})
    if dupes:
        problems.append("The same source field is mapped twice: " + ", ".join(dupes) + ".")
    for field, path in mapping.items():
        if not path:
            continue
        value = dig(record, path)
        if isinstance(value, (dict, list)):
            problems.append(f"{field} points to an object/list ({path}), not a value.")
        mapped[field] = value
    missing = [k for k in REQUIRED_MAP if mapped.get(k) in (None, "")]
    if missing:
        problems.append("Required fields are missing in the sample: " + ", ".join(missing) + ".")
    return problems, mapped


def _push_probe(project, cfg, kind, sample):
    """Webhook (PUSH) checks: Nexus's own inbound contract and the mapping - never an outbound call to the project."""
    from django.urls import Resolver404, resolve
    from .models import IntegrationCredential, IntegrationRegistration
    if kind == "customer":
        try:  # the real Nexus inbound route, resolved by Django - not an external URL
            route = resolve("/api/integrations/v1/customers/register").func.view_class.__name__ == "RegisterView"
        except Resolver404:
            route = False
        creds = IntegrationCredential.objects.filter(project=project, active=True).exists()
        received = IntegrationRegistration.objects.filter(project=project).count() + project.customers.count()
        checks = {"route": route, "credentials": creds, "live_events": received}
        if not route:
            return {"ok": False, "checks": checks, "message": "Inbound route POST /integrations/v1/customers/register is not available on this Nexus server."}
        if not creds:
            return {"ok": False, "checks": checks, "message": "No active Nexus Client ID / API Secret for this project. Issue credentials first."}
        live = f" {received} authenticated registration(s) received so far." if received else " Awaiting the first live authenticated registration."
        return {"ok": True, "checks": checks, "live_events": received, "message": f"Webhook mode: Nexus accepts POST /integrations/v1/customers/register for {project.code} "
                "with this project's Client ID + API Secret; no external Customer List API is called." + live}
    record, error = parse_sample(sample)
    if error:
        return {"ok": False, "message": error, "needs_sample": True}
    if kind == "preview":
        return {"ok": True, "message": "Sample payload parsed (not stored).", "raw_sample": record}
    # The inbound customers/register API reads the fixed Nexus field names (it does not apply field_mapping), so the sample is checked
    # with the SAME validator the live endpoint uses - a mapping-based check could pass payloads the endpoint would reject.
    from .services import _clean_customer
    try:
        mapped = _clean_customer(record)
    except IntegrationError as exc:
        fields = (exc.data or {}).get("fields", {})
        rename = {nexus: path for nexus, path in suggest(field_paths(record)).items() if nexus in INBOUND_FIELDS and path != nexus}
        hint = (" Send these fields under the Nexus names: " + ", ".join(f"{path} -> {nexus}" for nexus, path in rename.items()) + ".") if rename else ""
        detail = "; ".join(f"{k}: {' '.join(v)}" for k, v in fields.items()) or exc.message
        return {"ok": False, "message": f"The sample would be rejected by POST /integrations/v1/customers/register ({detail}).{hint}",
                "fields": fields, "rename": rename}
    return {"ok": True, "mapped": mapped, "message": "Sample Payload Validated: it matches the inbound customers/register contract "
            "(external_customer_id, customer_name, email, contact_number). A sample does not prove live delivery."}


def probe(project, kind, sample_id="", sample=None):
    """Real, SSRF-guarded calls used by the Validate / Preview / Test buttons. Always returns {ok, message, ...}; never raises to the caller."""
    cfg = IntegrationApiConfig.objects.filter(project=project).first()
    if cfg is None:
        return {"ok": False, "message": "Save the API configuration first."}
    if cfg.sync_mode in PUSH_MODES and kind in ("customer", "preview", "mapping"):
        return _push_probe(project, cfg, kind, sample)
    try:
        if kind in ("customer", "preview", "mapping"):
            body, items = _sample(cfg)
            if not isinstance(items, list):
                return {"ok": False, "message": "The customer list was not found at the configured results path. Check 'Results list path'."}
            if not items:
                return {"ok": True, "message": "The API answered correctly but returned no customers yet.", "count": 0, "warning": True}
            mapped = _mapped(cfg, items[0])
            missing = [k for k in REQUIRED_MAP if not mapped.get(k)]
            if kind == "preview":
                return {"ok": True, "message": "Response received.", "count": len(items), "raw_sample": json.loads(json.dumps(items[0], default=str)) if len(json.dumps(items[0], default=str)) < 4000 else "(record too large to preview)"}
            if missing:
                return {"ok": False, "message": "These required fields are empty with the current mapping: " + ", ".join(missing), "mapped": mapped, "missing": missing}
            if kind == "customer":
                return {"ok": True, "message": f"Customer API answered with {len(items)} record(s) in the expected list.", "count": len(items)}
            return {"ok": True, "message": "Mapping produces every required field.", "mapped": mapped, "count": len(items)}
        if kind == "product":
            if not cfg.product_path:
                return {"ok": False, "message": "Enter the Product Information API path first."}
            body = _get(cfg, {}, path=cfg.product_path)
            return {"ok": True, "message": "Product API answered with valid JSON.", "keys": sorted(body.keys())[:20] if isinstance(body, dict) else "list"}
        if kind == "tally":
            if not cfg.tally_path:
                return {"ok": False, "message": "Enter the Tally License Details API path first."}
            ident = sample_id
            if not ident:
                _, items = _sample(cfg)
                ident = _mapped(cfg, items[0]).get("external_customer_id") if isinstance(items, list) and items else ""
            if not ident:
                return {"ok": False, "message": "No sample customer id available; enter one to test with."}
            fetched = fetch_tally(project, ident) or {}
            from . import tally as _t
            clean, problems = _t.validate_tally(fetched, {"email": "", "external_customer_id": str(ident)})
            problems = [p for p in problems if "different customer" not in p]
            return {"ok": not problems, "message": "Tally details are complete." if not problems else "Tally details incomplete: " + " ".join(problems), "mapped": {k: ("***" if k == "serial" else v) for k, v in fetched.items()}}
    except IntegrationError as exc:
        return {"ok": False, "message": exc.message, "code": exc.code}
    except UnsafeUrl as exc:
        return {"ok": False, "message": str(exc), "code": "UNSAFE_URL"}
    except Exception as exc:  # noqa: BLE001 - surface as an actionable message, never a 500
        return {"ok": False, "message": f"Unexpected error: {type(exc).__name__}"}
    return {"ok": False, "message": "Unknown check."}


PUSH_MODES = ("PUSH", "REALTIME")  # PUSH = Authenticated Webhook; REALTIME = Real-time Push to Nexus (project backend calls customers/register)


def _int_or(value, default, low, high):
    try:
        v = int(value)
        return v if low <= v <= high else default
    except (TypeError, ValueError):
        return default


def validate_config(data):
    errors, out = {}, {}
    sync_mode = data.get("sync_mode", "POLL")
    poll = sync_mode not in PUSH_MODES  # Authenticated Webhook: the project calls Nexus, so the polling-only settings are not validated
    outbound = any(str(data.get(k) or "").strip() for k in ("product_path", "identity_path", "usage_path", "tally_path"))
    method = str(data.get("customer_method", "GET")).upper()
    if method not in ("GET", "POST"):
        if poll:
            errors["customer_method"] = ["Use GET or POST."]
        method = "GET"
    path = str(data.get("customer_path", "")).strip()
    if not path.startswith("/"):
        if poll:
            errors["customer_path"] = ["Enter the endpoint path, starting with /."]
        path = ""  # webhook: no external Customer List API is needed
    auth = data.get("auth_type", "NONE")
    if auth not in AUTH_TYPES:
        errors["auth_type"] = ["Choose None, Bearer token, API key header or Other (custom authentication)."]
    auth_config, auth_errors = validate_auth_config(data.get("auth_config"), auth, str(data.get("credential_ref", "")).strip())
    if poll or outbound:  # external credentials matter only when Nexus itself calls the project's API
        errors.update(auth_errors)
    api_protocol, protocol_errors = validate_protocol(data.get("api_protocol"))
    errors.update(protocol_errors)
    ref = str(data.get("credential_ref", "")).strip()
    if ref and not ref.replace("_", "").isalnum():
        errors["credential_ref"] = ["The credential reference is an environment-variable name (letters, digits, underscore)."]
    mapping = data.get("field_mapping")
    if not isinstance(mapping, dict) or any(not mapping.get(k) for k in REQUIRED_MAP):
        errors["field_mapping"] = [f"Map every required field: {', '.join(REQUIRED_MAP)}."]
    pag = data.get("pagination") or {}
    pag = pag if isinstance(pag, dict) else {}
    ptype = pag.get("type", "none")
    if ptype not in ("none", "page", "offset"):
        if poll:
            errors["pagination"] = ["Pagination type must be none, page or offset."]
        ptype = "none"
    if poll:
        try:
            size = int(pag.get("page_size", 100))
            interval = int(data.get("sync_interval_minutes", 60))
            if not 1 <= size <= 1000 or not 5 <= interval <= 1440:
                raise ValueError
        except (TypeError, ValueError):
            errors["pagination"] = ["Page size must be 1-1000 and the interval 5-1440 minutes."]
    else:  # not used in webhook mode: keep any sensible value, otherwise the defaults - never a validation error
        size = _int_or(pag.get("page_size"), 100, 1, 1000)
        interval = _int_or(data.get("sync_interval_minutes"), 60, 5, 1440)
    if sync_mode not in ("POLL", *PUSH_MODES):
        errors["sync_mode"] = ["Choose Scheduled Polling, Authenticated Webhook or Real-time Push to Nexus."]
    paths = {}
    for key in ("product_path", "identity_path", "usage_path"):
        v = str(data.get(key, "")).strip()
        if v and not v.startswith("/"):
            errors[key] = ["Enter the endpoint path, starting with /."]
        paths[key] = v[:200]
    tpath = str(data.get("tally_path", "")).strip()
    if tpath and not tpath.startswith("/"):
        errors["tally_path"] = ["Enter the Tally License Details endpoint path, starting with /."]
    if errors:
        return None, errors
    return dict(**paths, tally_path=tpath, tally_id_param=str(data.get("tally_id_param") or "customer_id")[:40], customer_method=method, customer_path=path, auth_type=auth, auth_header=str(data.get("auth_header", ""))[:60], credential_ref=ref, auth_config=json.dumps(auth_config), api_protocol=json.dumps(api_protocol),
                request_headers=json.dumps(data.get("request_headers") or {}), field_mapping=json.dumps({k: str(v) for k, v in mapping.items() if v}),  # blank optional paths are dropped (an empty path would map the whole record)
                pagination=json.dumps({"type": ptype, "page_param": pag.get("page_param", "page"), "size_param": pag.get("size_param", "page_size"),
                                       "page_size": size, "results_path": pag.get("results_path", "")}),
                sync_mode=data.get("sync_mode", "POLL"), sync_interval_minutes=interval,
                developer_name=str(data.get("developer_name", ""))[:100], developer_email=str(data.get("developer_email", ""))[:150]), {}


def pull_sync(project, actor="", max_pages=50):
    """One synchronisation run. Resumes from the checkpoint after an interrupted run; safe to repeat (no duplicate customer or licence)."""
    with transaction.atomic():
        project = IntegrationProject.objects.select_for_update().get(pk=project.pk)
        if not project.enabled:
            raise IntegrationError("PROJECT_DISABLED", "The integration is disabled.", 409)
        cfg = IntegrationApiConfig.objects.filter(project=project).first()
        if cfg is None:
            raise IntegrationError("NOT_CONFIGURED", "The customer registration API is not configured.", 409)
        if cfg.sync_mode in PUSH_MODES:  # webhook projects have no Customer List API to read: never call one (that produced the HTTP 404)
            # wording deliberately avoids words the UI maps to other messages (auth, credential, url, host, connect)
            raise IntegrationError("PUSH_MODE_NO_PULL", "This project uses Webhook (push) delivery: customers arrive when the project's backend sends them "
                                   "to Nexus (POST /integrations/v1/customers/register), so there is nothing for Sync Now to fetch. "
                                   "Received registrations appear in Sync Logs and Customers.", 409)
        cp, _ = IntegrationSyncCheckpoint.objects.get_or_create(project=project)
        now = timezone.now()
        # Atomic claim: only one worker/process runs a project's sync at a time (a crashed run is reclaimed after 15 minutes).
        claimed = IntegrationSyncCheckpoint.objects.filter(pk=cp.pk).exclude(status="RUNNING", last_started_at__gt=now - timedelta(minutes=15)).update(status="RUNNING", last_started_at=now)
        if not claimed:
            raise IntegrationError("SYNC_IN_PROGRESS", "A synchronisation of this project is already running.", 409)
    cp.refresh_from_db()
    mapping, pag = json.loads(cfg.field_mapping), json.loads(cfg.pagination)
    page = cp.next_page if cp.last_error and cp.next_page else 1
    stats = {"fetched": 0, "created": 0, "existing": 0, "failed": 0, "pending_tally": 0, "pages": 0}
    try:
        while stats["pages"] < max_pages:
            params = {}
            if pag["type"] == "page":
                params = {pag["page_param"]: page, pag["size_param"]: pag["page_size"]}
            elif pag["type"] == "offset":
                params = {pag["page_param"] if pag["page_param"] != "page" else "offset": (page - 1) * pag["page_size"], pag["size_param"]: pag["page_size"]}
            body = _get(cfg, params)
            items = dig(body, pag["results_path"]) if pag["results_path"] else body
            if not isinstance(items, list):
                raise IntegrationError("EXTERNAL_RESPONSE_SHAPE", "The results list was not found at the configured path.", 502)
            for item in items:
                stats["fetched"] += 1
                record = {nexus: dig(item, path) for nexus, path in mapping.items()}
                ext = str(record.get("external_customer_id") or "")
                try:
                    with transaction.atomic():
                        if universe.is_universe(project):  # UNIVERSE: queue + notification; credentials and licence only after Save & Allow
                            tally = {k[6:]: v for k, v in record.items() if k.startswith("tally_") and v is not None}
                            fetched = {}
                            if project.tally_serial_required:
                                try:
                                    fetched = fetch_tally(project, ext) or {}
                                except IntegrationError:
                                    fetched = {}  # connector unreachable: stays Pending Tally Verification
                            reg, created_now = universe.ingest(project, record, {**tally, **{k: v for k, v in fetched.items() if v is not None}}, actor=actor or "sync")
                            stats["created" if created_now else "existing"] += 1
                            if reg.status == "PENDING_TALLY_VERIFICATION":
                                stats["pending_tally"] += 1
                            continue
                        if project.tally_serial_required:  # Tally Required = Yes: verification first, never a silent Non-Tally fallback
                            tally = {k[6:]: v for k, v in record.items() if k.startswith("tally_") and v is not None}
                            try:
                                fetched = fetch_tally(project, ext)
                            except IntegrationError:
                                fetched = {}  # source unreachable: the customer is kept as Pending Tally Verification
                            status, body = tally_service.register_tally_customer(project, record, {**tally, **{k: v for k, v in (fetched or {}).items() if v is not None}},
                                                                                 idem_key=f"pull:{ext}" if ext else "", send_email=cfg.send_welcome_email, actor=actor or "sync")
                            if status == 202:
                                stats["pending_tally"] += 1
                                continue
                        else:
                            _, body = s.register_customer(project, record, idem_key=f"pull:{ext}" if ext else "", send_email=cfg.send_welcome_email, actor=actor or "sync")
                    stats["existing" if body.get("idempotent_replay") else "created"] += 1  # a replay means it was linked on an earlier run
                except IntegrationError as exc:
                    if exc.code == "CUSTOMER_ALREADY_REGISTERED":
                        stats["existing"] += 1  # idempotent: already linked to this project
                    else:
                        stats["failed"] += 1
                        s.log_sync(project, "pull", ext, "FAILED", exc, payload={k: record.get(k) for k in ("external_customer_id", "customer_name", "email")})
            stats["pages"] += 1
            cp.next_page, cp.fetched_total = page + 1, cp.fetched_total + len(items)
            cp.save()
            if pag["type"] == "none" or len(items) < pag["page_size"]:
                break
            page += 1
        done = timezone.now()
        cp.status, cp.next_page, cp.last_finished_at, cp.last_success_at, cp.last_error = "OK", 1, done, done, ""
        cp.consecutive_failures, cp.next_run_at = 0, done + timedelta(minutes=cfg.sync_interval_minutes)
        s.audit(project, "sync.pull", project.code, actor=actor, detail=json.dumps(stats))
        project.last_sync_status, project.last_sync_at = "OK" if not stats["failed"] else "PARTIAL", timezone.now()
        project.save(update_fields=["last_sync_status", "last_sync_at", "updated_at"])
    except (IntegrationError, UnsafeUrl) as exc:
        cp.status, cp.last_error = "FAILED", (getattr(exc, "message", None) or str(exc))[:300]
        cp.consecutive_failures += 1
        if cp.consecutive_failures == 3:  # one failure notification per streak (dead-letter: details stay in the sync log)
            from .models import IntegrationNotification
            IntegrationNotification.objects.create(project=project, kind="SYNC_FAILURE", title=f"Customer sync failing – {project.name}")
        # retry with backoff: 5, 10, 20 ... minutes, never later than the configured interval
        cp.next_run_at = timezone.now() + timedelta(minutes=min(cfg.sync_interval_minutes, 5 * 2 ** min(cp.consecutive_failures - 1, 6)))
        s.log_sync(project, "pull", "", "FAILED", exc if isinstance(exc, IntegrationError) else IntegrationError("UNSAFE_URL", str(exc), 400))
        cp.save()
        raise
    cp.save()
    return stats
