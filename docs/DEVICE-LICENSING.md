# Barcode Auto Print — signed device licensing (Nexus side, contract v1)

Implements `D:\PRNPRINT\docs\NEXUS-LICENSE-CONTRACT.md`. Base path: `/api/device/prn-auto-print/` (HTTPS only in production). All bodies are JSON.
Error codes added in this phase: `device_blocked` (403).
Errors: `{"message": "...", "code": "...", "errors": {}}`.

## 1. Keys
| | |
|---|---|
| Generate (once, on the Nexus server) | `python manage.py generate_license_keypair --key-id k1` — prints the public key and, once, the private key |
| Private key | server environment only: `LICENSE_SIGNING_PRIVATE_KEY` (base64 of the raw 32 bytes). Never in the DB, repo, installer, a response or a log |
| Key id | `LICENSE_KEY_ID` (default `k1`); every entitlement carries it as `key_id` |
| Public key for PRN | `python manage.py show_license_public_key` or `GET /api/device/prn-auto-print/public/keys/` → `{"keys":[{"key_id":"k1","public_key":"<b64>","algorithm":"Ed25519"}]}`. PRN should **pin** the key in its build (`PRN_LICENSE_PUBLIC_KEYS`), not trust the endpoint |
| Rotation | set a new private key + `key_id`; PRN accepts a list of public keys |
| Not configured | enroll / refresh answer `503 licensing_unavailable` and change nothing |

## 2. Enroll — `POST /enroll/` (no project key; credentials only)
```json
{ "username": "712345678", "password": "…", "device_public_key": "<b64 raw 32-byte Ed25519 public key>",
  "fingerprint": {"smbios": "<64 hex>", "machine": "<64 hex>", "v3": "<64 hex>"},
  "device_name": "Front desk PC", "app_version": "1.0.0" }
```
At least one valid 64-hex hash is required (other keys, e.g. `macs`, are ignored and not stored). `200`:
```json
{ "device_id": "<uuid>", "token": "<customer token bound to this device>",
  "account": {"name","company_name","contact_number","email","user_id","serial_number","must_change_password"},
  "license": {"authorized","state","license_type","expires_at","checked_at","lease_until"},
  "entitlement": {"payload": {…}, "sig": "<base64url>", "key_id": "k1"}, "server_time": "…Z" }
```
| Case | Result |
|---|---|
| wrong credentials | `401 invalid_credentials` (5 in a row lock the account 15 min: `429 account_locked`) |
| temporary password still set | `200` with `account.must_change_password: true`; the returned token can only call `change-password/` - every other signed call answers `403 password_change_required` until it is changed |
| expired / cancelled / inactive | `403 license_expired` / `license_cancelled` / `account_inactive` |
| another computer already active | `409 device_limit` |
| same computer again (reinstall) | `200`, same `device_id`, new device key, generation and dates unchanged |
| bad key / no usable hash | `400` with field errors |

## 2a. First-run activation — `POST /activate/` (Serial Number + Activation Key; no password, no project key)
The flow for a newly approved customer: the approval email carries User ID (= Serial Number), a temporary password and the Activation Key.
```json
{ "serial_number": "712345678", "activation_key": "A7K9P2X4M",
  "device_public_key": "<b64>", "fingerprint": {"smbios","machine","v3"}, "device_name": "…", "app_version": "1.0.0" }
```
`200`: `{device_id, account: {user_id, serial_number, must_change_password}, license, entitlement, server_time}` — **no customer token**: activation proves the
licence, not the person. Then the app calls signed `login/` with User ID + password; if `must_change_password` is true it must call signed `change-password/`
and sign in again. The key is case/dash-insensitive and is stored only as a hash. New keys are exactly 9 uppercase letters/digits (e.g. `A7K9P2X4M`, always at least one letter and one digit, so never shaped like a Serial Number); keys issued earlier (16 characters, `ABCD-EFGH-JKLM-NPQR`) stay valid. A wrong serial or a wrong key both answer `401 invalid_activation`
(indistinguishable). Same limits as enroll: another computer already active → `409 device_limit`; expired / cancelled / inactive → `403`; same computer again → same registration.
Activation is not required again while the stored entitlement is valid; the hourly signed `entitlement/` refresh keeps it current.

## 2b. Usage reporting — signed `POST usage/` (customer token required)
`{"event": "login"|"start"|"print"|"heartbeat", "count": 1..1000, "app_version": "1.2.0"}` → `{recorded, last_used_at}`.
Sets the device's `last_used_at` and adds `count` to `use_count`. Successful signed `login/` also counts. Background `entitlement/` refreshes and activation do **not** count as use.
A device whose licence is not active gets `403` and is not counted. The Nexus dashboard "Used / Not Used" for PRN reads these records (merged by serial with the legacy PRN records, one unit per customer; cancelled/inactive excluded).
Suggested: send `start` once after sign-in and `print` with a count at most every few minutes; failures must never block printing (queue and retry).

## 3. Signed requests (every other call)
Headers: `X-Device-Id`, `X-Device-Timestamp` (UTC ISO-8601, ±5 min), `X-Device-Nonce` (16–64 chars `[A-Za-z0-9_-+/=]`, single use), `X-Device-Signature`
= base64 Ed25519 signature (device private key) over the UTF-8 bytes of

```
METHOD \n PATH \n TIMESTAMP \n NONCE \n lowercase-hex(SHA-256(raw request body))
```
`PATH` is the URL path only (e.g. `/api/device/prn-auto-print/entitlement/`, no query string); `METHOD` upper-case; the body hash of an empty body is that of zero bytes.
Send the same exact bytes you hashed. Failures (all `401` unless noted): `device_auth_required`, `invalid_signature` (unknown device, wrong key, tampered body/path — indistinguishable),
`bad_timestamp`, `timestamp_out_of_range`, `bad_nonce`, `replayed_request`; `403 device_revoked` after a Super Admin transfer.
Customer calls also send `X-Customer-Token` (issued by `enroll/` or `login/` **to this device**; project-key tokens are rejected).

| Method + path | Body | Result |
|---|---|---|
| `POST entitlement/` | `{"fingerprint": {…}}` | **Refresh** (hourly while online): `{entitlement, license, server_time}`. `403 device_mismatch`, `license_expired`, `license_cancelled`, `account_inactive`, `device_revoked` |
| `POST login/` | `{username, password}` | like portal login, token bound to the device. `403 wrong_device_account` for another customer's credentials |
| `GET license/status/` | – | `{authorized, state, license_type, expires_at, checked_at, lease_until}` (`403 password_change_required` until changed) |
| `GET account/` | – | account details |
| `POST change-password/` | `{current_password, new_password}` | applied in Nexus immediately; old tokens die |
| `POST register-request/`, `forgot-password/`, `recover-username/` | as portal | also available signed |

Public, no device, rate limited (10/min/IP), revealing nothing about which accounts exist: `POST public/register-request/`, `public/forgot-password/`, `public/recover-username/`, `public/reset-password/ {token,new_password}`, `GET public/keys/`.

## 4. Entitlement payload
`{"payload": {...}, "sig": base64url(Ed25519(canonical(payload))), "key_id": "k1"}`, `canonical = json.dumps(payload, sort_keys=True, separators=(",",":"), ensure_ascii=True)` — verified unchanged by `agent/license_core.verify_entitlement`.

`v:1, product, serial, customer_id, device_id, license_type, status:"ACTIVE", activated_at (first enrollment), expires_at, purchased_at, issued_at, valid_until,
device (smbios, else machine), device_machine, device_v3 (when presented), generation, device_name`.

* **Packages** (`license_type` in the payload and in every `license` object): `TRIAL`, `ONE_YEAR`, `LIFETIME`.
  * `TRIAL` — PRN: first activation + 7 days (`TRIAL_DAYS_BY_PRODUCT`), longer only after an authorised **Extend Trial**.
  * `ONE_YEAR` — one year from first activation; moved on only by an authorised **Renew License** (+1 year from the current end, or from today if it already ended). The 7-day trial rule never applies to it.
  * `LIFETIME` — no fixed expiry: `expires_at` is `null`. Never counted in expiry reports.
  `expires_at` is the *licence* end (what Nexus shows in the drawer, reports and "Expiring This Month"). It is set once at first activation and thereafter changes only by an authorised extension/renewal. Reinstalling, logging in, refreshing never reset `activated_at` or `expires_at`.
* **The lease is separate from the licence.** `valid_until = min(issued_at + 7 days, expires_at)` — the 7-day cap is fixed in code (a larger setting is ignored). An online `entitlement/` refresh issues a new lease for as long as the licence itself is valid, so a valid 1 Year or Lifetime licence never lapses just because one lease ended. PRN must refresh while online (suggested hourly) and, if `valid_until` passes without a successful refresh, require reconnection before further access. When the licence itself has ended, refresh answers `403 license_expired` (no new lease).
* The hashes in the payload are the ones the device just presented, after they were verified against Nexus's stored copy (which is an HMAC of them, not the hashes).
* `generation` starts at 1 and increases by one each time a Super Admin transfer is followed by a new enrollment; the agent ignores older generations.

## 5. Device management (Super Admin only) and renewal
`GET /api/projects/prn-auto-print/customers/<id>/device/` → `{device, history, device_status (not_activated|active|revoked|transfer_pending), device_status_label, first_activated_at, last_used_at, license_type, license_type_label, effective_expires_at, can_revoke, can_transfer}` (no keys or hashes).
`POST …/device/revoke/ {"reason", "block": true|false}` revokes the enrolled computer (audit `DEVICE_REVOKED`); with `block` no other computer can be activated/enrolled (`403 device_blocked`) until a transfer.
`POST …/device/transfer/ {"reason"}` authorises moving the licence: revokes the current computer (if any), lifts a block (audit `DEVICE_TRANSFER_AUTHORIZED`); the next activation on the new computer gets generation + 1. Dates never change.
Admins, customers and the PRN app cannot call these. The old computer is refused at its next refresh (`device_revoked`) and can keep printing only until its current `valid_until`.
Renewal (Edit permission, like Extend Trial): `POST …/customers/<id>/renew/ {"note"}` (1 Year only; audit `LICENSE_RENEWED`), history `GET …/customers/<id>/renewals/`. New customers: approve/register with `license_type` `TRIAL`, `ONE_YEAR` or `LICENSED` (= Lifetime).

## 6. Storage
`DeviceRegistration`: device id, customer, device **public** key, HMAC-protected `{smbios, machine, v3}`, device name, app version, generation, enrolled / last-seen / revoked timestamps and reason.
`DeviceNonce`: device + nonce, pruned after `DEVICE_NONCE_TTL_SECONDS`. One active device per customer is a database constraint. Migration `registrations.0005` is additive.

## 7. Settings
`LICENSE_SIGNING_PRIVATE_KEY`, `LICENSE_KEY_ID`, `PRN_VALIDITY_DAYS=7` (PRN trial length; `TRIAL_DAYS_BY_PRODUCT`), `LICENSE_LEASE_DAYS=7` (never above 7), `DEVICE_CLOCK_SKEW_SECONDS=300`, `DEVICE_NONCE_TTL_SECONDS=600`,
`DEVICE_EVIDENCE_KEY` (defaults to `DJANGO_SECRET_KEY`; changing it makes stored hashes unmatchable), throttles `DEVICE_THROTTLE_RATE`, `DEVICE_ENROLL_THROTTLE_RATE`, `DEVICE_PUBLIC_THROTTLE_RATE`.

## 8. Credentials email, delivery status, reissue (customer hand-over)
* Approving a request (`POST /api/projects/<p>/registration-requests/<id>/approve/`) or Add Customer creates ONE set of credentials: User ID = Serial Number (9 digits, starts with 7), a random temporary password, a separate Activation Key. They are emailed together with the product, licence, and the steps; password/key are stored only hashed.
* Response fields: `email_sent` (true **only** when a real mail server accepted it), `credentials_email_status` = `accepted` | `not_delivered` (console/file dev backend) | `failed`, `credentials_emailed_at`. SMTP gives no read receipt, so `accepted` means "handed to the mail server", never "delivered". When `email_sent` is false the response carries `temporary_password` and `activation_key` **once** so an admin can pass them on.
* `POST /api/projects/<p>/customers/<id>/reissue-activation-key/` (Super Admin): new key (old one stops), emailed; password, serial, dates and an activated computer untouched. `POST /api/projects/<p>/temporary-password/` supplies a generated password to the Add Customer form.
* Diagnosing a refused activation: `python manage.py diagnose_activation --serial <serial> [--ask-key]` (redacted; prints environment, signing status, whether the customer exists in THIS database, licence state, key suffix, and whether a typed key matches). The server also logs `activation rejected project=… reason=unknown_serial|key_mismatch serial=…<last 3>`.
* Development ports: Nexus 8001 (frontend/.env `VITE_API_PROXY_TARGET`), PRN 8000. PRN's `NEXUS_BASE_URL` must be the Nexus whose database holds the customer; PRN must trust that Nexus' public key (`LICENSE_PUBLIC_KEYS`).

## 9. A computer that belongs to another customer — device transfer request

One computer is registered to one licence at a time. Enrollment (section 2 and 2a) is refused with **403 `device_bound_to_other`** when the
computer presenting its hardware hashes is the *active* registration of a different customer; a signed `login/` with a device that belongs
to another customer is refused with **403 `wrong_device_account`**. Neither deletes or resets anything. The refused customer can ask for a
transfer, and only a Super Admin can grant it.

**Request** — `POST /api/device/prn-auto-print/transfer-request/` (no project key, not device-signed; the customer's own credentials + this
computer's hardware hashes, exactly like enroll):

```json
{"username": "780038592", "password": "…", "fingerprint": {"smbios": "…", "machine": "…", "v3": "…"}, "device_name": "COUNTER-PC"}
```

* `201` `{"id": 7, "status": "pending", "from_user_id": "...", "to_user_id": "...", …}`. Asking again returns the same pending request.
* `401 invalid_credentials` (nothing is created) · `403` licence not usable · `403 device_blocked` · `409 device_limit` (the requester's licence is
  already on another computer) · `409 no_transfer_needed` (the computer is not registered to anyone else).
* `POST …/transfer-request/status/` (same body) → `{"needed": true, "state": "none" | "pending" | "approved" | "rejected", "id": 7}`.

**Decision (Super Admin only)** — `GET /api/projects/prn-auto-print/device-transfer-requests/?status=pending`,
`POST …/device-transfer-requests/<id>/approve/` and `…/reject/` (`{"reason": "…"}` optional). The pending requests are also listed in the
customer drawer (Device Management) of both customers.

* **Approve** revokes the registration that was in the way (the row stays as history with the reason "Transferred to …") and marks the request
  approved. It does **not** register the requester: the requester's next sign-in does, with their own credentials (a new registration; the
  licence's first-activation date and expiry are unchanged). The previous customer can no longer take the computer back without a transfer of
  their own.
* **Reject** changes no registration; the customer may send a new request.
* Every step is in the audit log: `DEVICE_TRANSFER_REQUESTED`, `DEVICE_REVOKED`, `DEVICE_TRANSFER_APPROVED` / `DEVICE_TRANSFER_REJECTED`.

Barcode Auto Print sends this request when the sign-in is refused, and after approval its old (revoked) device key answers
`403 device_revoked`, which makes it register again with the customer's credentials. Requires migration `0011_device_transfer_request`.
