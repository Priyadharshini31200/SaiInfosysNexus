# Nexus Tally SL – API contract for GSTR2Tally (v1)

Verified against local Nexus (`apps/tallysl`): `python manage.py test apps.tallysl` → 69 tests OK. Barcode AutoPrint (PRN) routes are not part of this API and are unchanged.

Base: `{NEXUS}/api/v1/tally-sl/gstr2tally/`  (local dev: `http://127.0.0.1:8001/api/v1/tally-sl/gstr2tally/`)

## Common
| Header | Where | Value |
|---|---|---|
| `X-Product-Key` | every call | the product key (Nexus `create_tally_product_key gstr2tally`; only its hash is stored) |
| `Authorization: Bearer <token>` | signed-in calls | token from `auth/login/` or `activate/confirm/` |
| `Content-Type: application/json` | POST | |

Errors: `{"message": str, "code": str, "errors": {field: [msgs]}}`. Codes: `unauthenticated` 401, `forbidden` 403, `validation_error` 400, `invalid_credentials` 401, `account_locked` 429 (5 failures → 15 min), `account_exists` 409, `invalid_serial` 400, `password_change_required` 403, `session_revoked` 401, `invalid_token` 400 (bad/expired/used OTP), `invalid_code` 400, `invalid_activation_key` 400, `license_not_active` 403, `confirmation_required` 409, `throttled` 429 (120/min per IP).
A product key only works for its own product (otherwise 403).

User ID = Tally Serial Number = Product Serial Number (5–30 letters/digits).

## 1. Registration & credentials
**GET `identify/`** → `{"api_version":"v1","product":{"code","name","kind"},"server_time"}`

**POST `register/`**
Req: `{"name","email","contact_number","tally_serial"}` (`product` optional, must equal the URL product).
Res 201: `{"user_id","status":"registered","license_status":"pending","message","welcome_email":"submitted|<failure status>"}`.
Effects: customer + PENDING licence created (no access), welcome email sent (User ID + temporary password, hashed in DB), Superadmins notified, customer visible at once in Nexus list/dashboard. Duplicate serial → 409 `account_exists` (no new row, owner unchanged).

**POST `auth/resend-credentials/`** Req `{"user_id"}` → 200 neutral message. Retry for a failed welcome email: only for an account that never signed in; 60 s cooldown; issues a NEW temporary password; never creates a customer.

## 2. Login / session
**POST `auth/login/`** Req `{"user_id","password","device":{"client_device_id","fingerprint","device_name","app_version"}}` (device optional)
Res 200: `{"token","must_change_password","account":{"name","email","contact_number","user_id","tally_serial","product"},"license":<License>}`.
**No forced first-login password change.** The emailed temporary password signs in directly; `must_change_password` is always `false` (kept for older clients) and `password_change_required` is never returned. Changing the password later is voluntary (`auth/change-password/`, e.g. Profile → Change Password).

**POST `auth/change-password/`** (bearer) Req `{"current_password","new_password"}` → 200; the calling session stays signed in, every OTHER session is signed out; the old password stops working. Wrong current password → 400 `wrong_password`.
**POST `auth/refresh/`** (bearer) → `{"token","expires_in"}` (old token revoked).
**POST `auth/logout/`** (bearer) → 200.
**GET `session/`** (bearer, cheap poll, NOT counted as usage) → `{"valid":true,"must_change_password","license":<License>}`; 401 `session_revoked` once the device was replaced/password changed.
**GET `account/`** (bearer) → account object above.

## 3. Forgot password (6-digit OTP)
**POST `auth/forgot-password/`** Req `{"identifier": <email or tally serial>}` → always 200 neutral message. Emails a 6-digit OTP: single use, expires in 10 min (`PASSWORD_OTP_TTL_SECONDS`), 60 s between requests, hash only stored.
**POST `auth/verify-otp/`** Req `{"identifier","otp"}` → 200 `{"valid":true}` or 400 `invalid_token`. Does not consume the OTP. 5 wrong tries kill it.
**POST `auth/reset-password/`** Req `{"identifier","token":<otp>,"new_password"}` → 200; OTP verified first, spent atomically, all sessions revoked. Reuse → 400 `invalid_token`.

## 4. Licence
`<License>` = `{"product","tally_serial","authorized":bool,"state","license_plan":"TRIAL|ANNUAL|LIFETIME","license_type":"TRIAL|LICENSED|LIFETIME","license_status","activated_at","expires_at"(null for Lifetime),"must_change_password","checked_at","lease_until"}`.
`state`: `active | pending | expired | cancelled | inactive | device_not_enrolled | device_not_authorized`. `authorized` is true only for an active licence on the customer's current enrolled device.
`lease_until` = how long an OFFLINE client may rely on the answer (`TALLY_LEASE_DAYS`, default 7, never past the licence expiry). Offline revocation is enforced at the next check after the lease; a disconnected old laptop cannot be closed remotely.
**GET `license/status/`** (bearer, finished password change) → `<License>`.

## 5. Activate User / device transfer (same account, no Nexus approval)
**No email OTP.** The Activation Key itself is the emailed proof of ownership and is sent ONLY when the customer asks to activate (never at registration, login, password reset or by a staff action).
1. **POST `activate/request-key/`** (legacy alias `activate/request-code/`) `{"user_id"}` → 200 neutral (`"If the account exists and is licensed, an Activation Key has been sent to the registered email."`). For an ACTIVE licence, issues a NEW single-use Activation Key, stores only its keyed hash (bound to the customer), emails it to the registered address, and invalidates any earlier unused key. Valid `TALLY_ACTIVATION_KEY_TTL_SECONDS` (default 900). Unknown account / non-active licence / throttled (60 s apart, 5 per hour) → same 200, nothing sent.
2. **POST `activate/verify/`** `{"user_id","activation_key"}` → `{"ticket","requires_confirmation":bool,"message","current_device":{"name","enrolled_at"}|null,"expires_in"}`. Wrong, expired, used or another customer's key → 400 `invalid_activation_key` (same answer for an unknown user); 5 wrong tries kill the key. `requires_confirmation` true when the licence is active on another laptop: show "License Already Active on Another Device — Your GSTR2Tally license is currently active on another laptop. Do you want to log out from the previous laptop and activate this laptop?" with OK – Activate This Laptop / Cancel (Cancel = do nothing; the ticket is simply abandoned and the old device stays active).
3. **POST `activate/confirm/`** `{"ticket","device":{...},"confirm_transfer":bool}` → 200 `{"token","license":<License>,"account","device_replaced":bool,"must_change_password"}`. Without `confirm_transfer:true` on an occupied licence → 409 `confirmation_required` (ticket stays valid). On OK: atomic, audited, old device revoked, its sessions invalidated, one active device kept. Ticket is single use.
Different-account ownership is never changed by this flow (Superadmin only).

## 6. Usage (drives the dashboard)
**POST `usage/open/`** (bearer) `{"app_version"}` → `{"recorded":bool,"license":<License>}`. Call once per real application start. Only authorised, enrolled devices are recorded (else 403 `not_authorized`); opens within 60 s count once. A successful login on an authorised device also records one. `session/`, `license/status/`, refreshes and polling are never counted.
Categories (mutually exclusive, order new → one-time → speed → normal), config in settings: `TALLY_NEW_USER_DAYS=7`, `TALLY_SPEED_WINDOW_DAYS=30`, `TALLY_SPEED_MIN_ACTIVE_DAYS=15`. New = registered within 7 days; One-Time = exactly 1 recorded open; Speed = active on ≥15 distinct days in the rolling 30-day window; Normal = at least 1 active day in the window.

## 7. Tally details
**POST `tally-info/`** (bearer) `{"edition","tss_details","virtual_user_limit","license_administrator","gateway_server","registered_email","tss_expiry_date","verified_fields":[...]}` → `{"message","availability":{field:"verified|unavailable"}}`. Only verified values stored; a verified value is never overwritten by a blank. Tally expiry is kept separate from the Nexus licence expiry.

## 8. Device-transfer notification (GSTR → Nexus, notification only)
**POST `events/device-transfer/`** headers: `X-Product-Key`, `X-Nexus-Timestamp` (unix s, ±300 s), `X-Nexus-Signature` = hex HMAC-SHA256(secret, `"{timestamp}.{raw body}"`). Secret: Nexus `TALLY_EVENT_SIGNING_SECRET` = GSTR `NEXUS_EVENT_SIGNING_SECRET`.
Body (GSTR outbox shape): `{"event_id","event_type":"DEVICE_TRANSFER_COMPLETED","product":"gstr2tally","tally_serial","old_device":{"id","name"},"new_device":{"id","name"},"transferred_at"}`. (Flat form `device.transferred` with `old_device_name/new_device_name/old_device_ref/new_device_ref/occurred_at` also accepted.)
201 `{"duplicate":false,"id"}` · 200 `{"duplicate":true,"id"}` (retry) · 401 bad/missing signature or stale timestamp · 403 wrong product / not configured · 400 bad schema · 404 `customer_not_found` (sender retries).

## 9. Staff API (Nexus UI; session auth + per-product permission) `/api/v1/tally-sl-admin/`
- `products/gstr2tally/customers/` GET (search, page, page_size) rows: `id, name, email, contact_number, tally_serial, user_id, company_name, gstin, license_type, license_plan, license_status, state, activated_at, expires_at, active_device, last_transfer_at, welcome_email_status, activation_email_status`; POST adds a customer (Entry).
- `products/gstr2tally/customers/<id>/` GET detail (customer, tally info, license, devices, device_summary, transfer_history, activation_history, usage, classification) · PATCH name/email/contact_number/company_name/gstin (Edit) · DELETE (Delete).
- `…/customers/<id>/license/<action>/` POST: `activate {"license_plan":"TRIAL|ANNUAL|LIFETIME","trial_days"}` (does NOT email a key; the customer receives a fresh Activation Key only through `activate/request-key/`), `extend-trial {"days"}`, `cancel-trial`, `renew`, `inactivate`, `reissue-key`.
- `…/customers/<id>/device/revoke/` POST (Device Management).
- `…/customers/<id>/credentials/resend/` POST (Entry; only while the customer still has the emailed password, else 409 `password_already_changed`) and `…/credentials/reset-password/` POST (Super Admin + Edit): emails a NEW temporary password to the registered address, signs out all sessions, clears lockout → `{customer, credentials_email:"submitted|failed", email_sent_to:"ab***@x.com", temporary_password?}` (`temporary_password` only when the email failed, shown once).
- `products/gstr2tally/customers/?search=&license_plan=TRIAL|ANNUAL|LIFETIME&state=pending|active|expired|cancelled|inactive` and `products/gstr2tally/customers/export/` POST `{fmt:"xlsx|pdf|print",filters:{search,license_plan,state}}` (View permission).
- `license/reissue-key/` generates a new key for the Superadmin and does NOT email it; the customer receives a key only through Activate User.
- Customer rows also carry `password_status`, `password_changed_at`, `credentials_email_status`.
- `dashboard/?product=gstr2tally` → `categories[{key,label,count}]` (speed/normal/new/one_time), `registrations{total,last_7_days,pending_license,recent_device_transfers}`.
- `usage/<speed|normal|new|one_time>/`, `reports/license-expiry/`, `reports/tally-details/`, `reports/user-performance/` (+ `/export/` xlsx|pdf|print).
- `notifications/`, `notifications/<id>/read/`, `products/gstr2tally/transfers/?recent=1`.
- `python manage.py import_gstr_customers` (dry run; `--apply` only with approval).

## Not in this API / pending
- Real SMTP delivery is not verified (tests use the in-memory mail backend).
- The Windows installer and local Tally connector live in the GSTR2Tally repo and were not rebuilt.
