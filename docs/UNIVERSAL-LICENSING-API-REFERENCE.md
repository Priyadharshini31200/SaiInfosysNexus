# Nexus Universal Licensing API v1.0.0

Base URL: `https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/`  (HTTPS only in production; the secret is NEVER put in browser/React code)
Project code: `<PROJECT_CODE>`

## Authentication
Every call carries two headers issued in Nexus (Project Management → API Integration). The secret is shown once; store it as a server environment variable.

```
X-Client-Id: <client id>
X-Client-Secret: <api secret>
Content-Type: application/json
```

Credentials are scoped: `project_code` must match the credentials. Rate limit: 300 requests/minute per client.

## Response format
```json
{
  "ok": true,
  "data": {
    "...": "..."
  },
  "request_id": "a1b2\u2026"
}
{
  "ok": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "\u2026",
    "fields": {}
  },
  "request_id": "a1b2\u2026"
}
```

## Register customer
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/customers/register`

Creates the Nexus customer, a 9-digit serial (= User ID), a temporary password, the licence and queues the welcome email. Repeat calls with the same Idempotency-Key replay the first answer. Paid licence types stay PENDING_APPROVAL until approved in Nexus.

Supports the `Idempotency-Key` header.

Request:
```json
{
  "project_code": "MOTHYS",
  "external_customer_id": "MOTHYS-1001",
  "customer_name": "Kumar",
  "email": "kumar@example.com",
  "contact_number": "9876543210"
}
```
Response (201):
```json
{
  "ok": true,
  "data": {
    "status": "REGISTERED",
    "customer_id": 12,
    "license_id": 12,
    "serial_number": "912345678",
    "user_id": "912345678",
    "license_type": "TRIAL",
    "license_state": "ACTIVE",
    "expires_at": "2026-12-17T09:00:00Z",
    "welcome_email": "not_delivered|accepted|failed"
  }
}
```

## Sync customer updates
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/customers/update`

event: CUSTOMER_UPDATED | EMAIL_CHANGED | CONTACT_UPDATED | CUSTOMER_INACTIVATED | CUSTOMER_REACTIVATED | LICENSE_STATUS_UPDATED. license_status may only be SUSPENDED or ACTIVE (lifting a suspension); a project can never grant a licence.

Request:
```json
{
  "project_code": "MOTHYS",
  "external_customer_id": "MOTHYS-1001",
  "event": "EMAIL_CHANGED",
  "email": "new@example.com"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "status": "SYNCED",
    "customer_id": 12,
    "changed": [
      "email"
    ],
    "license_state": "ACTIVE"
  }
}
```

## One-time bulk import (max 200 per call)
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/customers/import`

Registers many existing customers with the same rules as customers/register. Each row succeeds or fails independently; failures appear in the Failed Sync Report.

Request:
```json
{
  "project_code": "MOTHYS",
  "send_welcome_email": true,
  "customers": [
    {
      "external_customer_id": "MOTHYS-1001",
      "customer_name": "Kumar",
      "email": "kumar@example.com",
      "contact_number": "9876543210"
    }
  ]
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "imported": 1,
    "failed": 0,
    "results": [
      {
        "index": 0,
        "external_customer_id": "MOTHYS-1001",
        "ok": true,
        "serial_number": "912345678"
      }
    ]
  }
}
```

## Verify customer credentials
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/auth/verify`

Checks User ID + password (5 failures lock the account for 15 minutes). Does not create a device session.

Request:
```json
{
  "project_code": "MOTHYS",
  "user_id": "912345678",
  "password": "Temp-Pass-1"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "verified": true,
    "customer_id": 12,
    "external_customer_id": "MOTHYS-1001",
    "user_id": "912345678",
    "name": "Kumar",
    "email": "kumar@example.com",
    "must_change_password": true,
    "license": {
      "license_id": 12,
      "customer_id": 12,
      "license_type": "TRIAL",
      "state": "ACTIVE",
      "starts_at": "2026-11-17T09:00:00Z",
      "expires_at": "2026-12-17T09:00:00Z",
      "serial_number": "912345678",
      "user_id": "912345678"
    },
    "active_devices": 0
  }
}
```

## Change password (first login)
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/auth/change-password`

Request:
```json
{
  "project_code": "MOTHYS",
  "user_id": "912345678",
  "current_password": "Temp-Pass-1",
  "new_password": "MyNewPass#9"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "changed": true
  }
}
```

## Validate license
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/licenses/validate`

Identify the customer with user_id, serial_number or external_customer_id. state: ACTIVE | EXPIRED | PENDING_APPROVAL | SUSPENDED | INACTIVE.

Request:
```json
{
  "project_code": "MOTHYS",
  "user_id": "912345678"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "license_id": 12,
    "customer_id": 12,
    "license_type": "TRIAL",
    "state": "ACTIVE",
    "starts_at": "2026-11-17T09:00:00Z",
    "expires_at": "2026-12-17T09:00:00Z",
    "serial_number": "912345678",
    "user_id": "912345678",
    "valid": true,
    "active_devices": 1,
    "device_limit": 1
  }
}
```

## Request activation key
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/activation/request`

Emails a one-time 9-digit key to the registered address (valid 15 minutes, 3 requests/hour). The key is never returned by the API.

Request:
```json
{
  "project_code": "MOTHYS",
  "user_id": "912345678",
  "password": "Temp-Pass-1"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "sent_to": "ku***@example.com",
    "expires_in_seconds": 900,
    "email_status": "accepted"
  }
}
```

## Verify activation key / activate device
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/activation/verify`

Success returns a session. If another device holds the licence the answer is HTTP 409 TRANSFER_CONFIRMATION_REQUIRED with a transfer_token; call devices/transfer/confirm next.

Request:
```json
{
  "project_code": "MOTHYS",
  "user_id": "912345678",
  "password": "Temp-Pass-1",
  "activation_key": "123456789",
  "device_id": "PC-A1B2",
  "device_name": "Office PC"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "session_token": "nxt_example_session_token",
    "session_id": 7,
    "device_id": "PC-A1B2",
    "license": {
      "license_id": 12,
      "customer_id": 12,
      "license_type": "TRIAL",
      "state": "ACTIVE",
      "starts_at": "2026-11-17T09:00:00Z",
      "expires_at": "2026-12-17T09:00:00Z",
      "serial_number": "912345678",
      "user_id": "912345678"
    },
    "authorization": "ACTIVE"
  }
}
```
Transfer confirmation (409):
```json
{
  "ok": false,
  "error": {
    "code": "TRANSFER_CONFIRMATION_REQUIRED",
    "title": "License Already Active on Another Device",
    "message": "Your previous device session will be terminated and this device will become active. Do you want to continue?",
    "actions": [
      {
        "id": "cancel",
        "label": "Cancel"
      },
      {
        "id": "confirm",
        "label": "OK \u2013 Activate This Device"
      }
    ],
    "transfer_token": "nxx_example",
    "expires_in_seconds": 600,
    "active_device_name": "Office PC"
  }
}
```

## Confirm or cancel device transfer
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/devices/transfer/confirm`

confirm=true revokes the old device's sessions immediately, activates this device and returns its session token. confirm=false changes nothing. Repeating a completed confirmation returns ALREADY_TRANSFERRED (the token is only ever returned once).

Request:
```json
{
  "project_code": "MOTHYS",
  "user_id": "912345678",
  "transfer_token": "nxx_example",
  "confirm": true
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "status": "TRANSFERRED",
    "transferred": true,
    "session_token": "nxt_example_session_token",
    "session_id": 8,
    "device_id": "PC-C3D4",
    "revoked_sessions": [
      7
    ],
    "license": {
      "license_id": 12,
      "customer_id": 12,
      "license_type": "TRIAL",
      "state": "ACTIVE",
      "starts_at": "2026-11-17T09:00:00Z",
      "expires_at": "2026-12-17T09:00:00Z",
      "serial_number": "912345678",
      "user_id": "912345678"
    },
    "authorization": "ACTIVE"
  }
}
```

## Validate active session
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/sessions/introspect`

Call on every protected request (or cache for <= 60 s). active=false means reject: reason is revoked-by-transfer, logout, license_expired, license_suspended, customer_inactivated or unknown_session.

Request:
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_example_session_token"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "active": true,
    "session_id": 7,
    "device_id": "PC-A1B2",
    "user_id": "912345678",
    "customer_id": 12,
    "license_state": "ACTIVE",
    "expires_at": "2026-12-17T09:00:00Z"
  }
}
```

## Revoke / logout session
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/sessions/logout`

Request:
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_example_session_token"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "revoked": true,
    "session_id": 7
  }
}
```

## Record application usage
`POST https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/usage/events`

event_type: APP_OPEN | LOGIN | HEARTBEAT | LOGOUT | DEVICE_ACTIVATION | DEVICE_TRANSFER. Send session_token or user_id. event_id makes retries safe. Up to 100 events via `events`.

Request:
```json
{
  "project_code": "MOTHYS",
  "session_token": "nxt_example_session_token",
  "event_type": "HEARTBEAT",
  "event_id": "evt-0001",
  "occurred_at": "2026-11-17T09:30:00Z"
}
```
Response (200):
```json
{
  "ok": true,
  "data": {
    "recorded": 1,
    "duplicates": 0
  }
}
```

## Retrieve safe integration configuration
`GET https://<NEXUS_PRODUCTION_HOST>/nexus/api/integrations/v1/projects/{project_code}/config`

Never includes secrets.

Response (200):
```json
{
  "ok": true,
  "data": {
    "project_code": "MOTHYS",
    "name": "Mothys Inventory Management",
    "status": "SETUP_PENDING",
    "serial_length": 9,
    "activation_key_length": 9,
    "device_limit": 1,
    "transfer_policy": "SELF",
    "trial_days": 30,
    "default_license_type": "TRIAL",
    "api_version": "1.0.0"
  }
}
```

## Error codes
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

## Offline clients
Revocation on the server is immediate: the next `sessions/introspect` returns `active:false`. A client that is offline keeps its local login only until it reconnects; call introspect on reconnect and on every protected request.