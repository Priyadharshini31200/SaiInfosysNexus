# Mothys Inventory Management (FastAPI) – Nexus Integration Handoff

**Status:** all 12 APIs below passed an automated end-to-end run against the Nexus local test database (SQL Server). That run is `backend/apps/integrations/test_handoff.py`; every request/response in this document was captured from it.

**Not yet verified:** the Mothys FastAPI side (not built), a real SMTP delivery of the welcome email, browser verification of the Nexus screens, and production deployment. Do not treat this as production-ready until those are done.

**No secrets in this document.** `X-Client-Secret` is delivered separately (see *Secure provisioning*). Example tokens and passwords below are throw-away values from a test database.

## 1. Product configuration (project code `MOTHYS`)

| Setting | Value | Verified |
|---|---|---|
| Tally connection / Tally serial | Not used (`tally_serial_required = false`) | ✅ automated test |
| Serial number | Nexus generated, exactly 9 digits, starts with 9, unique | ✅ automated test |
| User ID | = Serial Number | ✅ automated test |
| Password | Nexus generated temporary password, stored hashed, emailed once, change at first login | ✅ automated test |
| Licence types | Trial (default 3 days, editable) · Annual · Lifetime. Annual/Lifetime are never auto-granted: they wait for Super Admin approval | ✅ automated test |
| Activation key | 9 digits, emailed only when requested, one-time, expires in 15 min, max 3 requests/hour, 5 wrong tries | ✅ automated test |
| Devices | 1 active device; a second device needs customer confirmation (no Super Admin step, no extra email OTP) | ✅ automated test |
| Framework / auth | FastAPI · JWT Bearer (existing `POST /auth/login`, `GET /auth/me`, `GET /health`) | ✅ automated test |

## 2. Base URLs and authentication

| Environment | Base URL |
|---|---|
| Local Nexus | `http://127.0.0.1:8001/api/integrations/v1` |
| Production Nexus | `https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1` (replace `<NEXUS_PRODUCTION_HOST>` with the real Nexus host; the live proxy mounts Nexus under `/nexus`) |

Every call is **server-to-server over HTTPS** from the Mothys FastAPI backend (never from React/browser code):

```
X-Client-Id: <NEXUS_CLIENT_ID>
X-Client-Secret: <NEXUS_CLIENT_SECRET>
Content-Type: application/json
Idempotency-Key: <stable key>      # register only (recommended)
```

Permission model: the credentials belong to project `MOTHYS` only. `project_code` in every body must equal `MOTHYS`, otherwise `403 PROJECT_MISMATCH`. Rate limit 300 requests/minute per client. A Super Admin can rotate or disable the credentials in Nexus at any time.

Response envelope: success `{"ok": true, "data": {…}, "request_id": "…"}`; error `{"ok": false, "error": {"code": "…", "message": "…"}, "request_id": "…"}`.

## 3. API reference (PASS/FAIL from the verified run)

| # | API | Method | Path | Result |
|---|---|---|---|---|
| 1 | Customer Registration | POST | `/customers/register` | **PASS** (3 cases) |
| 2 | Customer Update | POST | `/customers/update` | **PASS** (2 cases) |
| 3 | Customer Bulk Import | POST | `/customers/import` | **PASS** (1 cases) |
| 4 | Login Verification | POST | `/auth/verify` | **PASS** (2 cases) |
| 5 | Password Change | POST | `/auth/change-password` | **PASS** (1 cases) |
| 6 | License Validation | POST | `/licenses/validate` | **PASS** (1 cases) |
| 7 | Activation Key Request | POST | `/activation/request` | **PASS** (1 cases) |
| 8 | Activation Key Verification | POST | `/activation/verify` | **PASS** (3 cases) |
| 9 | Device Transfer Confirmation | POST | `/devices/transfer/confirm` | **PASS** (2 cases) |
| 10 |  Session Introspection | POST | `/sessions/introspect` | **PASS** (2 cases) |
| 11 |  Logout | POST | `/sessions/logout` | **PASS** (1 cases) |
| 12 |  Usage Tracking | POST | `/usage/events` | **PASS** (2 cases) |
| – | Safe project configuration | GET | `/projects/MOTHYS/config` | **PASS** |

### 1. Customer Registration

`POST http://127.0.0.1:8001/api/integrations/v1/customers/register`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/customers/register`

**Required fields:** project_code, external_customer_id, customer_name, email, contact_number (header Idempotency-Key recommended)  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "external_customer_id": "MOTHYS-1001",
  "customer_name": "Kumar",
  "email": "kumar@example.com",
  "contact_number": "9876543210"
}
```
**Success – response (HTTP 201)**
```json
{
  "ok": true,
  "data": {
    "status": "REGISTERED",
    "customer_id": 1,
    "license_id": 1,
    "serial_number": "925100221",
    "user_id": "925100221",
    "license_type": "TRIAL",
    "license_state": "ACTIVE",
    "expires_at": "2026-11-20T07:00:09.577228Z",
    "welcome_email": "accepted"
  },
  "request_id": "b789552fa03e4b7fa4dc639c3486b1f5"
}
```

**Duplicate external id – request**
```json
{
  "project_code": "MOTHYS",
  "external_customer_id": "MOTHYS-1001",
  "customer_name": "Kumar",
  "email": "kumar@example.com",
  "contact_number": "9876543210"
}
```
**Duplicate external id – response (HTTP 409)**
```json
{
  "ok": false,
  "error": {
    "code": "CUSTOMER_ALREADY_REGISTERED",
    "message": "This external customer id is already registered.",
    "customer_id": 1,
    "license_id": 1,
    "serial_number": "925100221"
  },
  "request_id": "6b7ccbab7f094d3394cd2e7bb8fc32d9"
}
```

**Validation error – request**
```json
{
  "project_code": "MOTHYS",
  "external_customer_id": "MOTHYS-1002",
  "customer_name": "Kumar",
  "email": "bad",
  "contact_number": "1"
}
```
**Validation error – response (HTTP 422)**
```json
{
  "ok": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please correct the request fields.",
    "fields": {
      "email": [
        "Enter a valid email address."
      ],
      "contact_number": [
        "Enter a valid contact number (7-15 digits)."
      ]
    }
  },
  "request_id": "c8a8e59c0dd048d99ac85d4649ab7fa5"
}
```

### 2. Customer Update

`POST http://127.0.0.1:8001/api/integrations/v1/customers/update`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/customers/update`

**Required fields:** project_code, external_customer_id; event; plus the changed fields (customer_name / email / contact_number / active / license_status)  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "external_customer_id": "MOTHYS-1001",
  "event": "CONTACT_UPDATED",
  "contact_number": "9876500000"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "status": "SYNCED",
    "customer_id": 1,
    "changed": [
      "contact_number"
    ],
    "license_state": "ACTIVE"
  },
  "request_id": "261748953f30433ebb6f4f844d951531"
}
```

**Unknown customer – request**
```json
{
  "project_code": "MOTHYS",
  "external_customer_id": "NOPE",
  "event": "CUSTOMER_UPDATED",
  "customer_name": "X"
}
```
**Unknown customer – response (HTTP 404)**
```json
{
  "ok": false,
  "error": {
    "code": "CUSTOMER_NOT_FOUND",
    "message": "No customer is registered for this external customer id."
  },
  "request_id": "d816744fc18a49cfae4f89da243f3881"
}
```

### 3. Customer Bulk Import

`POST http://127.0.0.1:8001/api/integrations/v1/customers/import`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/customers/import`

**Required fields:** project_code, customers[] (1-200, each with the register fields); optional send_welcome_email (default true)  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "send_welcome_email": false,
  "customers": [
    {
      "external_customer_id": "MOTHYS-2001",
      "customer_name": "Bala",
      "email": "bala@example.com",
      "contact_number": "9000000001"
    }
  ]
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "imported": 1,
    "failed": 0,
    "results": [
      {
        "index": 0,
        "external_customer_id": "MOTHYS-2001",
        "ok": true,
        "serial_number": "927017187"
      }
    ]
  },
  "request_id": "e9d30d163d5841ed840f80b41c3efde4"
}
```

### 4. Login Verification

`POST http://127.0.0.1:8001/api/integrations/v1/auth/verify`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/auth/verify`

**Required fields:** project_code, user_id, password  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Wrong password – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "password": "wrong"
}
```
**Wrong password – response (HTTP 401)**
```json
{
  "ok": false,
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "The User ID or password is incorrect."
  },
  "request_id": "2597f8ed514e49869c1304dbda3d954f"
}
```

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "password": "<temporary-password-from-welcome-email>"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "verified": true,
    "customer_id": 1,
    "external_customer_id": "MOTHYS-1001",
    "user_id": "925100221",
    "name": "Kumar",
    "email": "kumar@example.com",
    "must_change_password": true,
    "license": {
      "license_id": 1,
      "customer_id": 1,
      "license_type": "TRIAL",
      "state": "ACTIVE",
      "starts_at": "2026-11-17T07:00:09.577228Z",
      "expires_at": "2026-11-20T07:00:09.577228Z",
      "serial_number": "925100221",
      "user_id": "925100221"
    },
    "active_devices": 0
  },
  "request_id": "4cc8ef447f134278b9d48a1f7e189534"
}
```

### 5. Password Change

`POST http://127.0.0.1:8001/api/integrations/v1/auth/change-password`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/auth/change-password`

**Required fields:** project_code, user_id, current_password, new_password (min 8 characters)  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "current_password": "<temporary-password-from-welcome-email>",
  "new_password": "MyNewPass#9"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "changed": true
  },
  "request_id": "336cd9c66e0949baad7800522f43d49d"
}
```

### 6. License Validation

`POST http://127.0.0.1:8001/api/integrations/v1/licenses/validate`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/licenses/validate`

**Required fields:** project_code and one of user_id / serial_number / external_customer_id  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "license_id": 1,
    "customer_id": 1,
    "license_type": "TRIAL",
    "state": "ACTIVE",
    "starts_at": "2026-11-17T07:00:09.577228Z",
    "expires_at": "2026-11-20T07:00:09.577228Z",
    "serial_number": "925100221",
    "user_id": "925100221",
    "valid": true,
    "active_devices": 0,
    "device_limit": 1
  },
  "request_id": "f338d73d18e54d62a6662523f753035f"
}
```

### 7. Activation Key Request

`POST http://127.0.0.1:8001/api/integrations/v1/activation/request`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/activation/request`

**Required fields:** project_code, user_id, password  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "password": "MyNewPass#9"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "sent_to": "ku***@example.com",
    "expires_in_seconds": 900,
    "email_status": "accepted"
  },
  "request_id": "6ebb9fc9dc864e04b46100342b5f6237"
}
```

### 8. Activation Key Verification

`POST http://127.0.0.1:8001/api/integrations/v1/activation/verify`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/activation/verify`

**Required fields:** project_code, user_id, password, activation_key (9 digits), device_id; optional device_name  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Wrong key – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "password": "MyNewPass#9",
  "activation_key": "000000000",
  "device_id": "PC-A",
  "device_name": "Office PC"
}
```
**Wrong key – response (HTTP 400)**
```json
{
  "ok": false,
  "error": {
    "code": "ACTIVATION_KEY_INVALID",
    "message": "The activation key is invalid or has expired. Request a new one."
  },
  "request_id": "46f0981993c6454bbc5017fcc5081ffc"
}
```

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "password": "MyNewPass#9",
  "activation_key": "212712969",
  "device_id": "PC-A",
  "device_name": "Office PC"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "session_token": "nxt_WvVfpWTzvt-4aQgPh51jRhTEn1DPPUT4y3su4Zcsw-E",
    "session_id": 1,
    "device_id": "PC-A",
    "license": {
      "license_id": 1,
      "customer_id": 1,
      "license_type": "TRIAL",
      "state": "ACTIVE",
      "starts_at": "2026-11-17T07:00:09.577228Z",
      "expires_at": "2026-11-20T07:00:09.577228Z",
      "serial_number": "925100221",
      "user_id": "925100221"
    },
    "authorization": "ACTIVE",
    "revocation": "Server-side revocation is immediate; an offline client only learns of it when it next reconnects."
  },
  "request_id": "f552d66fa43048ebab9eeedee6b80883"
}
```

**Second device: confirmation required – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "password": "MyNewPass#9",
  "activation_key": "965514124",
  "device_id": "PC-B",
  "device_name": "Home Laptop"
}
```
**Second device: confirmation required – response (HTTP 409)**
```json
{
  "ok": false,
  "error": {
    "code": "TRANSFER_CONFIRMATION_REQUIRED",
    "message": "Your previous device session will be terminated and this device will become active. Do you want to continue?",
    "title": "License Already Active on Another Device",
    "actions": [
      {
        "id": "cancel",
        "label": "Cancel"
      },
      {
        "id": "confirm",
        "label": "OK – Activate This Device"
      }
    ],
    "transfer_token": "nxx_DUDQNPyDQEMG-CoAd6wcwaEA2ZpnMCWk",
    "expires_in_seconds": 600,
    "active_device_name": "Office PC"
  },
  "request_id": "b1cef32c75e94a6e919734a6a79d737c"
}
```

### 9. Device Transfer Confirmation

`POST http://127.0.0.1:8001/api/integrations/v1/devices/transfer/confirm`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/devices/transfer/confirm`

**Required fields:** project_code, user_id, transfer_token (from the 409 answer), confirm (true = OK – Activate This Device, false = Cancel)  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Cancel – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "transfer_token": "nxx_DUDQNPyDQEMG-CoAd6wcwaEA2ZpnMCWk",
  "confirm": false
}
```
**Cancel – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "status": "CANCELLED",
    "transferred": false,
    "previous_device_still_authorized": true
  },
  "request_id": "e9b2a0b963ae4ecfb14a57eaa0683835"
}
```

**Confirm – request**
```json
{
  "project_code": "MOTHYS",
  "user_id": "925100221",
  "transfer_token": "nxx_M2w5B4NoI5jqDqZR8gPllKa-iPE-7ANZ",
  "confirm": true
}
```
**Confirm – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "session_token": "nxt_vv9P31OF31YeJfaVYmBjiOY5zr2Et63TedeucdflTd4",
    "session_id": 2,
    "device_id": "PC-B",
    "license": {
      "license_id": 1,
      "customer_id": 1,
      "license_type": "TRIAL",
      "state": "ACTIVE",
      "starts_at": "2026-11-17T07:00:09.577228Z",
      "expires_at": "2026-11-20T07:00:09.577228Z",
      "serial_number": "925100221",
      "user_id": "925100221"
    },
    "authorization": "ACTIVE",
    "revocation": "Server-side revocation is immediate; an offline client only learns of it when it next reconnects.",
    "status": "TRANSFERRED",
    "transferred": true,
    "revoked_sessions": [
      1
    ]
  },
  "request_id": "eea5ebd66e93412cbc3cfa529300afc4"
}
```

### 10. Session Introspection

`POST http://127.0.0.1:8001/api/integrations/v1/sessions/introspect`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/sessions/introspect`

**Required fields:** project_code, session_token  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_WvVfpWTzvt-4aQgPh51jRhTEn1DPPUT4y3su4Zcsw-E"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "active": true,
    "session_id": 1,
    "device_id": "PC-A",
    "user_id": "925100221",
    "customer_id": 1,
    "license_state": "ACTIVE",
    "expires_at": "2026-11-20T07:00:09.577228Z"
  },
  "request_id": "629711e9c85a49acbaa963e1c19079fe"
}
```

**Old device after transfer – request**
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_WvVfpWTzvt-4aQgPh51jRhTEn1DPPUT4y3su4Zcsw-E"
}
```
**Old device after transfer – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "active": false,
    "reason": "device_transfer",
    "revoked_at": "2026-11-17T07:00:19.804529Z"
  },
  "request_id": "fd6f25b9b7544d50ab02cad121cc3176"
}
```

### 11. Logout

`POST http://127.0.0.1:8001/api/integrations/v1/sessions/logout`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/sessions/logout`

**Required fields:** project_code, session_token  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_vv9P31OF31YeJfaVYmBjiOY5zr2Et63TedeucdflTd4"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "revoked": true,
    "session_id": 2
  },
  "request_id": "d8ee05c3d6f1422ab7d09c82c22538e5"
}
```

### 12. Usage Tracking

`POST http://127.0.0.1:8001/api/integrations/v1/usage/events`  
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/usage/events`

**Required fields:** project_code, event_type (APP_OPEN | LOGIN | HEARTBEAT | LOGOUT | DEVICE_ACTIVATION | DEVICE_TRANSFER) and session_token or user_id; optional event_id, occurred_at, device_id; or events[] (max 100)  
**Permission:** MOTHYS client credentials (`X-Client-Id` + `X-Client-Secret`).

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_vv9P31OF31YeJfaVYmBjiOY5zr2Et63TedeucdflTd4",
  "event_type": "LOGIN",
  "event_id": "evt-1"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "recorded": 1,
    "duplicates": 0
  },
  "request_id": "79bfbd7928eb4b2da7885f8ed3fa968e"
}
```

**Success – request**
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_vv9P31OF31YeJfaVYmBjiOY5zr2Et63TedeucdflTd4",
  "event_type": "HEARTBEAT",
  "event_id": "evt-2"
}
```
**Success – response (HTTP 200)**
```json
{
  "ok": true,
  "data": {
    "recorded": 1,
    "duplicates": 0
  },
  "request_id": "4beb41f6ecc2415a80853be52601de28"
}
```

### Safe project configuration
`GET http://127.0.0.1:8001/api/integrations/v1/projects/MOTHYS/config` (same headers). Returns limits and `verified_steps`; never secrets. Nexus marks the integration **Verified** only after your backend has really called register, auth/verify, activation/verify, sessions/introspect and usage/events.

```json
{
  "ok": true,
  "data": {
    "project_code": "MOTHYS",
    "name": "Mothys Inventory Management",
    "status": "ACTIVE",
    "frontend_url": "https://inventory.tallysolutionstamilnadu.com",
    "serial_length": 9,
    "serial_prefix": "9",
    "activation_key_length": 9,
    "device_limit": 1,
    "transfer_policy": "SELF",
    "trial_days": 3,
    "default_license_type": "TRIAL",
    "usage_tracking": true,
    "api_version": "1.0.0",
    "required_steps": [
      "register",
      "auth_verify",
      "activation_verify",
      "session_introspect",
      "usage_event"
    ],
    "verified_steps": [
      "register",
      "auth_verify",
      "activation_verify",
      "session_introspect",
      "usage_event"
    ]
  },
  "request_id": "a0b11d3757274244832d03ffe57f829b"
}
```

### Error codes
| Code | HTTP | Meaning |
|---|---|---|
| UNAUTHENTICATED | 401 | Missing or wrong X-Client-Id / X-Client-Secret. |
| INTEGRATION_DISABLED | 403 | The integration is disabled in Nexus. |
| PROJECT_MISMATCH | 403 | project_code does not belong to these credentials. |
| RATE_LIMITED | 429 | Too many requests; retry later. |
| INVALID_JSON | 400 | Body is not valid JSON. |
| VALIDATION_ERROR | 422 | Field errors are in error.fields. |
| IDEMPOTENCY_KEY_REUSED | 422 | Same Idempotency-Key with a different body. |
| CUSTOMER_ALREADY_REGISTERED | 409 | external_customer_id already registered. |
| DUPLICATE_EMAIL | 409 | Email already used by another customer of the project. |
| CUSTOMER_NOT_FOUND | 404 | No such customer. |
| INVALID_CREDENTIALS | 401 | Wrong User ID or password. |
| ACCOUNT_LOCKED | 423 | Locked after repeated failures; see retry_after_seconds. |
| LICENSE_NOT_ACTIVE | 403 | Licence is pending approval, expired, suspended or the customer is inactive. |
| ACTIVATION_KEY_INVALID | 400 | Wrong, used or expired activation key. |
| TRANSFER_CONFIRMATION_REQUIRED | 409 | Licence active on another device; confirm via devices/transfer/confirm. |
| TRANSFER_DISABLED | 403 | Device transfer is disabled for the project. |
| INVALID_TRANSFER_TOKEN | 400 | Unknown, used or cancelled transfer token. |
| TRANSFER_EXPIRED | 410 | Transfer confirmation window (10 min) elapsed. |
| SESSION_NOT_FOUND | 404 | Unknown session token. |
| USAGE_TRACKING_DISABLED | 403 | Usage tracking is off for the project. |
| LICENSE_STATUS_NOT_ALLOWED | 422 | Sync cannot grant a licence. |
| EMAIL_FAILED | 502 | The activation email could not be sent. |
| SERIAL_EXHAUSTED | 503 | No free serial number. |

## 4. How Mothys must call Nexus automatically

Mothys code must trigger Nexus; nobody types customers into Nexus.

* **Customer created** (Mothys register/create-customer handler): after the local row is saved, call `customers/register` with `external_customer_id = "MOTHYS-<your customer id>"` and `Idempotency-Key = "reg-<your customer id>"`. Save `serial_number`/`customer_id` returned. If Nexus is unreachable, store the customer as *pending sync* and retry with the **same** idempotency key (a retry can never create a second Nexus customer). Treat `409 CUSTOMER_ALREADY_REGISTERED` as success (it returns the existing ids).
* **Customer edited** (name, email, phone): call `customers/update` with `event` `CUSTOMER_UPDATED`, `EMAIL_CHANGED` or `CONTACT_UPDATED`. **Customer deactivated:** `CUSTOMER_INACTIVATED` (revokes the customer's device sessions immediately); re-enabled: `CUSTOMER_REACTIVATED`.
* **Existing customers:** run `customers/import` once (≤200 per call). Each row succeeds or fails independently; failures appear in Nexus → Project Management → API Integration Status → Failed Sync Report and can be retried there.
* Nexus emails the User ID, serial number and temporary password. Mothys must **not** generate, email or store a password.
* Within a minute the customer is visible in Nexus: Mothys Inventory → Customers, the Dashboard counts, the Overall Dashboard card and Mothys Reports.

## 5. Mapping Nexus credentials to Mothys accounts and JWT

* Identity link: `external_customer_id` (your id) ⇄ Nexus `customer_id`. Store both plus `serial_number` on your customer row. **Nexus User ID = Serial Number** is the login name customers type.
* Replace the password check in `POST /auth/login` with `auth/verify` (Nexus is the only password store for these users). Local passwords of existing users are not migrated: imported customers receive a Nexus temporary password by email.
* If `must_change_password` is true, show a change-password screen and call `auth/change-password`.
* A successful `auth/verify` proves the password only. A **JWT is issued only after a device session exists** (activation, section 6). Put the Nexus session token inside the JWT, e.g. `{"sub": "<user_id>", "nsid": "<session_token>"}`; `GET /auth/me` keeps working from your own user row.

## 6. Activation, device transfer, licence checks and JWT revocation

1. App asks for activation → `activation/request` (credentials) → Nexus emails a 9-digit key.
2. `activation/verify` with the key, `device_id`, `device_name`.
   * **200** → session created. Issue your JWT with the returned `session_token` in `nsid`.
   * **409 `TRANSFER_CONFIRMATION_REQUIRED`** → another device holds the licence. Show the dialog from the response (`title`, `message`, buttons **Cancel** / **OK – Activate This Device**) and keep `transfer_token`.
3. `devices/transfer/confirm` with `confirm:false` (Cancel – old device stays authorised) or `confirm:true` (OK). On true Nexus revokes the old device's session **immediately**, creates the new session and returns its `session_token`; issue a JWT for it. No Super Admin approval and no extra OTP.
4. **Every protected Mothys request** must call `sessions/introspect` with the JWT's `nsid` (cache the result ≤ 30–60 s). `active:false` → reject with 401 and the `reason` (`device_transfer`, `logout`, `license_expired`, `license_suspended`, `trial_cancelled`, `customer_inactivated`, `unknown_session`). This is how the old device's JWT dies after a transfer even though the JWT itself has not expired.
5. `licenses/validate` returns `state` (`ACTIVE`, `EXPIRED`, `PENDING_APPROVAL`, `SUSPENDED`, `CANCELLED`, `INACTIVE`) and `expires_at` for screens that show licence status; enforcement itself comes from `introspect`.
6. Logout → `sessions/logout`. Report usage with `usage/events` (APP_OPEN on start, LOGIN after login, HEARTBEAT ≈ every 5 min, LOGOUT); `DEVICE_ACTIVATION` / `DEVICE_TRANSFER` are recorded by Nexus. Send an `event_id` so retries are safe. Fire-and-forget; never block the user on it.
7. **Offline clients:** Nexus revocation is immediate, but an offline desktop client keeps its local login until it reconnects. On reconnect it must call a protected endpoint (which calls introspect) before trusting the session.

### FastAPI client sketch
```python
import os, httpx
BASE = os.environ["NEXUS_API_BASE"]
HEAD = {"X-Client-Id": os.environ["NEXUS_CLIENT_ID"], "X-Client-Secret": os.environ["NEXUS_CLIENT_SECRET"]}

async def nexus(path, body=None, idem=None):
    h = dict(HEAD, **({"Idempotency-Key": idem} if idem else {}))
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{BASE}/{path}", json={"project_code": "MOTHYS", **(body or {})}, headers=h)
    j = r.json()
    if not j["ok"]:
        raise NexusError(r.status_code, j["error"])
    return j["data"]
```

## 7. Environment configuration (Mothys server)
```
NEXUS_API_BASE=https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1
NEXUS_CLIENT_ID=<issued in Nexus>
NEXUS_CLIENT_SECRET=<delivered securely, never committed>
NEXUS_PROJECT_CODE=MOTHYS
```
Same content in `mothys.env.example`. Local testing uses `http://127.0.0.1:8001/api/integrations/v1`.

## 8. Client ID setup and secure secret provisioning (Nexus Super Admin)

1. Apply the Nexus migrations (`python manage.py migrate integrations`). Migration 0002 registers MOTHYS (3-day trial, no Tally, 9/9/9, 1 device) in *Setup Pending* with **no credentials**.
2. Sign in as Super Admin → Project Management → Project List → Mothys → **API Integration** → **Regenerate / Rotate Credentials**.
3. The screen shows `Client ID` and the **API Secret exactly once**. Copy it straight into the Mothys server's secret store (environment variable / secrets manager). Never put it in the repository, chat, e-mail, tickets or this document. Share the **Client ID** by normal channel; share the **secret** only through the agreed secure channel (password manager share or in person), and ask the Mothys lead to confirm receipt.
4. Nexus stores only a hash of the secret; if it is lost, rotate – the old secret stops working instantly.
5. Run *Test Connection* (credentials active + Mothys health URL reachable). That is **not** proof of integration: the status becomes **Verified** only after Mothys has really called register, auth/verify, activation/verify, sessions/introspect and usage/events.
6. Rotate the secret when a developer leaves or if exposure is suspected; Disable Integration is the emergency stop.

## 9. Acceptance checklist for the Mothys team
- [ ] New Mothys customer → appears in Nexus Mothys Customers with a 9-digit serial starting with 9 and receives the welcome email.
- [ ] Retrying registration (same key) creates no duplicate.
- [ ] Login uses `auth/verify`; first-login password change works.
- [ ] Activation key is emailed only on request; wrong/expired/reused key is rejected.
- [ ] Second device shows the confirmation dialog; Cancel keeps device A; OK ends device A's access on its next request.
- [ ] Expired / cancelled / inactive licence blocks protected calls.
- [ ] Usage appears in the Nexus Dashboard and User Performance report.
- [ ] `GET /projects/MOTHYS/config` shows all five `verified_steps`.
