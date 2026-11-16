# Sai Infosys Nexus - API reference

Nexus is the central authentication and license authority for **Barcode Auto Print** (`prn-auto-print`), **GSTR2Tally** (`gstr2tally`) and future products.
All bodies are JSON. Every error is `{"message": "...", "errors": {field: ["..."]}}` (plus `"code"` where a machine-readable reason exists).
Passwords and password hashes are **never** returned by any endpoint. Customer passwords are stored only as Django password hashes.

Three audiences, three credentials:

| Audience | Base path | Credential |
|---|---|---|
| Nexus staff (Superadmin / Admin), browser | `/api/auth/`, `/api/projects/...` | session cookie + `X-CSRFToken`; role + Admin Permissions enforced |
| A product's **server** (e.g. Barcode backend) | `/api/portal/<project>/...` | `X-Project-Key` (+ `X-Customer-Token` after sign-in). The project is taken from the key; a key for project A never works for project B |
| A product's **desktop app** (Barcode Auto Print) | `/api/device/<project>/...` | device-signed requests, see [DEVICE-LICENSING.md](DEVICE-LICENSING.md) |

Operations: `GET /api/health/` (no auth) -> `200 {"status":"ok","database":"ok"}` or `503 {"status":"unavailable",...}`. See [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Staff authentication
| Method | Path | Notes |
|---|---|---|
| GET | `/api/auth/csrf/` | sets the CSRF cookie |
| POST | `/api/auth/login/` `{username, password}` | `200 {user}`; `400 Invalid username or password.`; throttled `429` |
| POST | `/api/auth/logout/` | |
| GET | `/api/auth/me/` | current user and role |
| POST | `/api/auth/change-password/` | signs the user out |

## 2. Customers and licenses (staff)  `/api/projects/<project>/...`
| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `activation-key/` | Entry | generates a unique, project-bound key (shown once, stored as SHA-256) |
| POST | `serial-number/`, `temporary-password/` | Entry | generate a free serial (9 digits, starts with 7) / random temporary password |
| POST | `customers/` | Entry | create a customer: `name, company_name, email, contact_number, address, gstin?, license_type (TRIAL \| ONE_YEAR \| LICENSED), serial_number, user_id, password, activation_key, trial_days?`. Hashes the password, marks it temporary, emails the credentials. `201` + `email_sent`; `credentials_email_status` = `accepted \| not_delivered \| failed` |
| GET | `customers/`, `customers/<id>/` | View | |
| PATCH | `customers/<id>/` | Edit | |
| POST | `customers/<id>/extend-trial/`, `renew/` | Edit | |
| POST | `customers/<id>/cancel-trial/`, `inactivate/` | Delete | |
| POST | `customers/<id>/reset-password/`, `reissue-activation-key/` | Super Admin | emails the new secret; shown once only if the email failed |

License plans: **Trial** (length fixed per product, never shown to the customer), **1 Year** (365 days), **Lifetime** (`LICENSED`, no expiry). `user_id` = serial number.

## 3. Registration requests and approval (staff)
A product sends a sign-up (section 4). It is stored as **Pending Approval**; **no customer, key or license exists until a staff member approves it.**

| Method | Path | Notes |
|---|---|---|
| GET | `registration-requests/` | pending requests `{results:[{id,name,contact_number,email,status,status_label,created_at,...}]}` |
| GET | `registration-requests/<id>/` | one request, any status (`pending`/`processed`/`rejected`, `status_label` `Pending Approval`/`Approved`/`Rejected`, `reject_reason`) |
| POST | `registration-requests/<id>/approve/` | `{license_type, trial_days?, serial_number?, temporary_password?, company_name?, address?, gstin?}`. Creates the customer, generates the Activation Key (and serial / temporary password unless given), saves, **emails the credentials**. `409` if already processed; `409` on a serial conflict |
| POST | `registration-requests/<id>/reject/` | `{reason?}`. Marks it Rejected; creates nothing, sends nothing. `409` if already processed. The same email may apply again later |

Both need **Entry** permission (Super Admin always). A request of another project is `404`. Anonymous calls are `401`.

## 4. Product server API  `/api/portal/<project>/...`  (header `X-Project-Key`)
| Method | Path | Purpose |
|---|---|---|
| POST | `register-request/` `{name, contact_number, email}` | new customer sign-up -> Pending Approval (`201 {id,status:"pending"}`). `409 account_exists` / `409 request_pending` for duplicates |
| POST | `login/` `{username, password}` | `{token, must_change_password, account, license}`. `401 invalid_credentials`; 5 failures lock 15 min (`429 account_locked`). Never returns a password |
| POST | `change-password/` `{current_password, new_password}` + `X-Customer-Token` | required while `must_change_password` is true |
| GET | `license/status/` + token | `{authorized, state, license_type, expires_at, checked_at, lease_until}`. `403 password_change_required` until the temporary password is replaced |
| GET | `account/` + token | account details (no secrets) |
| POST | `recover-username/`, `forgot-password/`, `reset-password/` | emailed recovery; always `200` so accounts cannot be enumerated |

**Approval status for a sign-up:** `POST register-request/status/` `{request_id, email}` -> `{id, status: "pending"|"approved"|"rejected", status_label, processed_at}`.
Both the id returned by `register-request/` and the email used must match, otherwise `404` (so neither can be guessed from the other). It returns no User ID, key or
password: after `approved`, the customer receives those by email. The Barcode desktop app uses the same call without a project key at
`POST /api/device/prn-auto-print/public/register-request/status/` (throttled).

**License states** (`state`): `active` (authorized), `expired` (Trial / 1 Year past its end), `cancelled` (trial cancelled), `inactive`. A customer of project A is unknown to project B (404/401).

## 5. Device API (Barcode Auto Print desktop)
Signed enroll / activate / login / entitlement flow with Ed25519 entitlements: [DEVICE-LICENSING.md](DEVICE-LICENSING.md). New-customer registration from the app uses
`POST /api/device/prn-auto-print/public/register-request/` `{name, contact_number, email}` (throttled, no signature - the person has no account yet).

## 6. Email
Credentials email contents: Project, User ID, Temporary Password, Serial Number, Activation Key, License Plan, Activation Date; 1 Year adds Expiry Date; Lifetime shows
"Lifetime" and no expiry; Trial shows only "Trial" (the duration is never stated). SAI INFOSYS branded HTML with a plain-text alternative. Up to `MAIL_SEND_ATTEMPTS` tries with
back-off; each send is recorded in `registrations_emaillog`. See DEPLOYMENT.md section 6.

## 7. Configuration (environment)
`DJANGO_*` (host, CSRF, cookies, proxy, CORS), `DB_*` (SQL Server), `EMAIL_*`, `MAIL_SEND_ATTEMPTS`, `ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE` (default `1`), `LICENSE_*`, `CUSTOMER_*`.
Full list with comments: `backend/.env.example`.

## 8. Database migrations
`python manage.py migrate` (additive only; existing customers, keys and hashes are untouched):
`registrations.0010_email_log_request_rejection` adds the `registrations_emaillog` table and `registrations_registrationrequest.reject_reason` (default `''`).
