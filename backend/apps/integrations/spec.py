"""Single source of truth for the public contract: drives the OpenAPI JSON, the downloadable Markdown documentation, the
Integration screen and a test that every documented path really resolves. Nothing is documented here that is not routed."""
import json

VERSION = "1.0.0"
P = "MOTHYS"
SESSION = "nxt_example_session_token"
LIC = {"license_id": 12, "customer_id": 12, "license_type": "TRIAL", "state": "ACTIVE", "starts_at": "2026-11-17T09:00:00Z",
       "expires_at": "2026-12-17T09:00:00Z", "serial_number": "912345678", "user_id": "912345678"}

ENDPOINTS = [
    {"id": "register", "method": "POST", "path": "customers/register", "summary": "Register customer", "idempotent": True,
     "description": "Creates the Nexus customer, a 9-digit serial (= User ID), a temporary password, the licence and queues the welcome email. Repeat calls with the same Idempotency-Key replay the first answer. Paid licence types stay PENDING_APPROVAL until approved in Nexus.",
     "request": {"project_code": P, "external_customer_id": "MOTHYS-1001", "customer_name": "Kumar", "email": "kumar@example.com", "contact_number": "9876543210"},
     "response": {"status": "REGISTERED", "customer_id": 12, "license_id": 12, "serial_number": "912345678", "user_id": "912345678", "license_type": "TRIAL", "license_state": "ACTIVE", "expires_at": "2026-12-17T09:00:00Z", "welcome_email": "not_delivered|accepted|failed"},
     "http": 201},
    {"id": "update", "method": "POST", "path": "customers/update", "summary": "Sync customer updates", "idempotent": False,
     "description": "event: CUSTOMER_UPDATED | EMAIL_CHANGED | CONTACT_UPDATED | CUSTOMER_INACTIVATED | CUSTOMER_REACTIVATED | LICENSE_STATUS_UPDATED. license_status may only be SUSPENDED or ACTIVE (lifting a suspension); a project can never grant a licence.",
     "request": {"project_code": P, "external_customer_id": "MOTHYS-1001", "event": "EMAIL_CHANGED", "email": "new@example.com"},
     "response": {"status": "SYNCED", "customer_id": 12, "changed": ["email"], "license_state": "ACTIVE"}},
    {"id": "import", "method": "POST", "path": "customers/import", "summary": "One-time bulk import (max 200 per call)", "idempotent": False,
     "description": "Registers many existing customers with the same rules as customers/register. Each row succeeds or fails independently; failures appear in the Failed Sync Report.",
     "request": {"project_code": P, "send_welcome_email": True, "customers": [{"external_customer_id": "MOTHYS-1001", "customer_name": "Kumar", "email": "kumar@example.com", "contact_number": "9876543210"}]},
     "response": {"imported": 1, "failed": 0, "results": [{"index": 0, "external_customer_id": "MOTHYS-1001", "ok": True, "serial_number": "912345678"}]}},
    {"id": "auth_verify", "method": "POST", "path": "auth/verify", "summary": "Verify customer credentials",
     "description": "Checks User ID + password (5 failures lock the account for 15 minutes). Does not create a device session.",
     "request": {"project_code": P, "user_id": "912345678", "password": "Temp-Pass-1"},
     "response": {"verified": True, "customer_id": 12, "external_customer_id": "MOTHYS-1001", "user_id": "912345678", "name": "Kumar", "email": "kumar@example.com", "must_change_password": True, "license": LIC, "active_devices": 0}},
    {"id": "change_password", "method": "POST", "path": "auth/change-password", "summary": "Change password (first login)",
     "request": {"project_code": P, "user_id": "912345678", "current_password": "Temp-Pass-1", "new_password": "MyNewPass#9"}, "response": {"changed": True}},
    {"id": "license_validate", "method": "POST", "path": "licenses/validate", "summary": "Validate license",
     "description": "Identify the customer with user_id, serial_number or external_customer_id. state: ACTIVE | EXPIRED | PENDING_APPROVAL | SUSPENDED | INACTIVE.",
     "request": {"project_code": P, "user_id": "912345678"}, "response": {**LIC, "valid": True, "active_devices": 1, "device_limit": 1}},
    {"id": "activation_request", "method": "POST", "path": "activation/request", "summary": "Request activation key",
     "description": "Emails a one-time 9-digit key to the registered address (valid 15 minutes, 3 requests/hour). The key is never returned by the API.",
     "request": {"project_code": P, "user_id": "912345678", "password": "Temp-Pass-1"}, "response": {"sent_to": "ku***@example.com", "expires_in_seconds": 900, "email_status": "accepted"}},
    {"id": "activation_verify", "method": "POST", "path": "activation/verify", "summary": "Verify activation key / activate device",
     "description": "Success returns a session. If another device holds the licence the answer is HTTP 409 TRANSFER_CONFIRMATION_REQUIRED with a transfer_token; call devices/transfer/confirm next.",
     "request": {"project_code": P, "user_id": "912345678", "password": "Temp-Pass-1", "activation_key": "123456789", "device_id": "PC-A1B2", "device_name": "Office PC"},
     "response": {"session_token": SESSION, "session_id": 7, "device_id": "PC-A1B2", "license": LIC, "authorization": "ACTIVE"},
     "conflict": {"title": "License Already Active on Another Device", "message": "Your previous device session will be terminated and this device will become active. Do you want to continue?", "actions": [{"id": "cancel", "label": "Cancel"}, {"id": "confirm", "label": "OK – Activate This Device"}], "transfer_token": "nxx_example", "expires_in_seconds": 600, "active_device_name": "Office PC"}},
    {"id": "transfer_confirm", "method": "POST", "path": "devices/transfer/confirm", "summary": "Confirm or cancel device transfer",
     "description": "confirm=true revokes the old device's sessions immediately, activates this device and returns its session token. confirm=false changes nothing. Repeating a completed confirmation returns ALREADY_TRANSFERRED (the token is only ever returned once).",
     "request": {"project_code": P, "user_id": "912345678", "transfer_token": "nxx_example", "confirm": True},
     "response": {"status": "TRANSFERRED", "transferred": True, "session_token": SESSION, "session_id": 8, "device_id": "PC-C3D4", "revoked_sessions": [7], "license": LIC, "authorization": "ACTIVE"}},
    {"id": "session_introspect", "method": "POST", "path": "sessions/introspect", "summary": "Validate active session",
     "description": "Call on every protected request (or cache for <= 60 s). active=false means reject: reason is revoked-by-transfer, logout, license_expired, license_suspended, customer_inactivated or unknown_session.",
     "request": {"project_code": P, "session_token": SESSION}, "response": {"active": True, "session_id": 7, "device_id": "PC-A1B2", "user_id": "912345678", "customer_id": 12, "license_state": "ACTIVE", "expires_at": "2026-12-17T09:00:00Z"}},
    {"id": "logout", "method": "POST", "path": "sessions/logout", "summary": "Revoke / logout session",
     "request": {"project_code": P, "session_token": SESSION}, "response": {"revoked": True, "session_id": 7}},
    {"id": "usage", "method": "POST", "path": "usage/events", "summary": "Record application usage", "idempotent": False,
     "description": "event_type: APP_OPEN | LOGIN | HEARTBEAT | LOGOUT | DEVICE_ACTIVATION | DEVICE_TRANSFER. Send session_token or user_id. event_id makes retries safe. Up to 100 events via `events`.",
     "request": {"project_code": P, "session_token": SESSION, "event_type": "HEARTBEAT", "event_id": "evt-0001", "occurred_at": "2026-11-17T09:30:00Z"}, "response": {"recorded": 1, "duplicates": 0}},
    {"id": "config", "method": "GET", "path": "projects/{project_code}/config", "summary": "Retrieve safe integration configuration",
     "description": "Never includes secrets.", "request": None,
     "response": {"project_code": P, "name": "Mothys Inventory Management", "status": "SETUP_PENDING", "serial_length": 9, "activation_key_length": 9, "device_limit": 1, "transfer_policy": "SELF", "trial_days": 30, "default_license_type": "TRIAL", "api_version": VERSION}},
]

ERRORS = [
    ("UNAUTHENTICATED", 401, "Missing or wrong X-Client-Id / X-Client-Secret."), ("INTEGRATION_DISABLED", 403, "The integration is disabled in Nexus."),
    ("PROJECT_MISMATCH", 403, "project_code does not belong to these credentials."), ("RATE_LIMITED", 429, "Too many requests; retry later."),
    ("INVALID_JSON", 400, "Body is not valid JSON."), ("VALIDATION_ERROR", 422, "Field errors are in error.fields."),
    ("IDEMPOTENCY_KEY_REUSED", 422, "Same Idempotency-Key with a different body."), ("CUSTOMER_ALREADY_REGISTERED", 409, "external_customer_id already registered."),
    ("DUPLICATE_EMAIL", 409, "Email already used by another customer of the project."), ("CUSTOMER_NOT_FOUND", 404, "No such customer."),
    ("INVALID_CREDENTIALS", 401, "Wrong User ID or password."), ("ACCOUNT_LOCKED", 423, "Locked after repeated failures; see retry_after_seconds."),
    ("LICENSE_NOT_ACTIVE", 403, "Licence is pending approval, expired, suspended or the customer is inactive."),
    ("ACTIVATION_KEY_INVALID", 400, "Wrong, used or expired activation key."), ("TRANSFER_CONFIRMATION_REQUIRED", 409, "Licence active on another device; confirm via devices/transfer/confirm."),
    ("TRANSFER_DISABLED", 403, "Device transfer is disabled for the project."), ("INVALID_TRANSFER_TOKEN", 400, "Unknown, used or cancelled transfer token."),
    ("TRANSFER_EXPIRED", 410, "Transfer confirmation window (10 min) elapsed."), ("SESSION_NOT_FOUND", 404, "Unknown session token."),
    ("USAGE_TRACKING_DISABLED", 403, "Usage tracking is off for the project."), ("LICENSE_STATUS_NOT_ALLOWED", 422, "Sync cannot grant a licence."),
    ("EMAIL_FAILED", 502, "The activation email could not be sent."), ("SERIAL_EXHAUSTED", 503, "No free serial number."),
]


def base_url(request_base):
    return request_base.rstrip("/") + "/integrations/v1/"


def openapi(api_base, project_code=None):
    paths = {}
    for e in ENDPOINTS:
        path = "/" + e["path"]
        op = {"summary": e["summary"], "operationId": e["id"], "security": [{"ClientId": [], "ClientSecret": []}], "tags": ["Universal Licensing"],
              "responses": {str(e.get("http", 200)): {"description": "Success", "content": {"application/json": {"example": {"ok": True, "data": e["response"], "request_id": "…"}}}},
                            "default": {"description": "Error", "content": {"application/json": {"example": {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": "…"}, "request_id": "…"}}}}}}
        if e.get("description"):
            op["description"] = e["description"]
        if e.get("conflict"):
            op["responses"]["409"] = {"description": "Transfer confirmation required", "content": {"application/json": {"example": {"ok": False, "error": {"code": "TRANSFER_CONFIRMATION_REQUIRED", **e["conflict"]}}}}}
        if e["method"] == "GET":
            op["parameters"] = [{"name": "project_code", "in": "path", "required": True, "schema": {"type": "string"}}]
        else:
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"type": "object"}, "example": e["request"]}}}
            if e.get("idempotent"):
                op["parameters"] = [{"name": "Idempotency-Key", "in": "header", "required": False, "schema": {"type": "string", "maxLength": 100}}]
        paths[path] = {e["method"].lower(): op}
    return {
        "openapi": "3.0.3", "info": {"title": "Sai Infosys Nexus – Universal Licensing API", "version": VERSION,
                                     "description": "Server-to-server API. Authenticate with X-Client-Id and X-Client-Secret. Never call from a browser."},
        "servers": [{"url": base_url(api_base)}], "paths": paths,
        "components": {"securitySchemes": {"ClientId": {"type": "apiKey", "in": "header", "name": "X-Client-Id"}, "ClientSecret": {"type": "apiKey", "in": "header", "name": "X-Client-Secret"}}},
        "x-error-codes": [{"code": c, "http": s, "meaning": m} for c, s, m in ERRORS],
    }


def markdown(api_base, project=None):
    base = base_url(api_base)
    code = project.code if project else "<PROJECT_CODE>"
    out = [f"# Nexus Universal Licensing API v{VERSION}" + (f" – {project.name}" if project else ""), "",
           f"Base URL: `{base}`  (HTTPS only in production; the secret is NEVER put in browser/React code)", f"Project code: `{code}`", "",
           "## Authentication", "Every call carries two headers issued in Nexus (Project Management → API Integration). The secret is shown once; store it as a server environment variable.", "",
           "```", "X-Client-Id: <client id>", "X-Client-Secret: <api secret>", "Content-Type: application/json", "```", "",
           "Credentials are scoped: `project_code` must match the credentials. Rate limit: 300 requests/minute per client.", "",
           "## Response format", "```json", json.dumps({"ok": True, "data": {"...": "..."}, "request_id": "a1b2…"}, indent=2), json.dumps({"ok": False, "error": {"code": "VALIDATION_ERROR", "message": "…", "fields": {}}, "request_id": "a1b2…"}, indent=2), "```", ""]
    for e in ENDPOINTS:
        out += [f"## {e['summary']}", f"`{e['method']} {base}{e['path']}`", ""]
        if e.get("description"):
            out += [e["description"], ""]
        if e.get("idempotent"):
            out += ["Supports the `Idempotency-Key` header.", ""]
        if e["request"] is not None:
            out += ["Request:", "```json", json.dumps(e["request"], indent=2), "```"]
        out += [f"Response ({e.get('http', 200)}):", "```json", json.dumps({"ok": True, "data": e["response"]}, indent=2), "```"]
        if e.get("conflict"):
            out += ["Transfer confirmation (409):", "```json", json.dumps({"ok": False, "error": {"code": "TRANSFER_CONFIRMATION_REQUIRED", **e["conflict"]}}, indent=2), "```"]
        out.append("")
    out += ["## Error codes", "| Code | HTTP | Meaning |", "|---|---|---|"] + [f"| {c} | {s} | {m} |" for c, s, m in ERRORS]
    out += ["", "## Offline clients", "Revocation on the server is immediate: the next `sessions/introspect` returns `active:false`. A client that is offline keeps its local login only until it reconnects; call introspect on reconnect and on every protected request."]
    return "\n".join(out)
