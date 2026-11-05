# Sai Infosys Nexus — Phase 1

Django + DRF + SQL Server backend (`backend/`) and React + Vite frontend (`frontend/`).
Scope: Super Admin / Admin authentication, profile, reset password, logout, protected dashboard shell.

## Prerequisites
- Python 3.11+ and Node 20+
- Microsoft SQL Server (e.g. `.\SQLEXPRESS`) and Microsoft ODBC Driver 17 or 18
- An empty database, e.g. `sqlcmd -S .\SQLEXPRESS -E -C -Q "CREATE DATABASE SaiInfosysNexus"`

## Backend
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # then edit: DJANGO_SECRET_KEY, DB_* settings
python manage.py migrate        # creates auth, session, role and permission tables
python manage.py seed_initial_users   # idempotent bootstrap of Superadmin and Admin (also runs after `migrate` while DJANGO_DEBUG=True; create-only, never resets a password)
python manage.py runserver 127.0.0.1:8001   # 8000 is used by Barcode Auto Print on a shared dev machine; keep frontend/.env VITE_API_PROXY_TARGET in step
python manage.py test apps      # backend test suite (uses a temporary test database)
```
- **Windows authentication:** `DB_TRUSTED_CONNECTION=True`, leave `DB_USER`/`DB_PASSWORD` empty.
- **SQL authentication:** `DB_TRUSTED_CONNECTION=False` and set `DB_USER`/`DB_PASSWORD`.
- Named instance: `DB_HOST=.\SQLEXPRESS`, leave `DB_PORT` empty.
- Bootstrap passwords come from `SUPERADMIN_INITIAL_PASSWORD` / `ADMIN_INITIAL_PASSWORD`. Re-running the seed
  never changes an existing user's password. The seed refuses to run with `DJANGO_DEBUG=False` unless `--allow-production` is passed.
  Change both passwords after first login (Reset Password); new passwords must satisfy Django's validators.

**Dev accounts:** `python manage.py seed_initial_users --reset` resets `Superadmin`/`Admin` to `SUPERADMIN_INITIAL_PASSWORD`/`ADMIN_INITIAL_PASSWORD` from `.env` (hashed, correct role, active). It refuses to run when `DJANGO_DEBUG=False`.
**Production accounts:** never use the development passwords. Set strong unique `SUPERADMIN_INITIAL_PASSWORD`/`ADMIN_INITIAL_PASSWORD` in the server's `.env`, run `seed_initial_users --allow-production` once (create-only), then change both at first sign-in or with `manage.py changepassword`. No credentials exist in the frontend build.

## Frontend
```powershell
cd frontend
npm install
copy .env.example .env          # optional: VITE_API_PROXY_TARGET
npm run dev                     # http://localhost:5173 (proxies /api/ to Django)
npm run build
```
Open http://localhost:5173 and sign in as `Superadmin` / `Superadmin` or `Admin` / `Admin` (development defaults).

## API (`/api/`)
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/auth/csrf/` | Set the CSRF cookie |
| POST | `/api/auth/login/` | Sign in (CSRF required, throttled) |
| POST | `/api/auth/logout/` | Sign out |
| GET | `/api/auth/me/` | Current user and role |
| POST | `/api/auth/change-password/` | Change own password, then sign out |
| GET | `/api/dashboard/project-usage/` | Super Admin: Used / Not Used / Expiring This Month counts per project |
| GET | `/api/dashboard/project-usage/<project>/<used\|not-used\|expiring>/` | Super Admin: the customers/devices behind a count (`search`, `page`, `page_size` 10/25/50/100) |

| GET/POST | `/api/projects/<project>/customers/` | List (View) / register (Entry) customers of one project |
| POST | `/api/projects/<project>/activation-key/` | Generate a unique activation key (Entry) |
| GET/PATCH | `/api/projects/<project>/customers/<id>/` | View / edit profile (Edit) |
| POST | `.../customers/<id>/extend-trial/` | `{days}` (Edit) |
| POST | `.../customers/<id>/cancel-trial/`, `.../inactivate/` | Cancel an active trial / inactivate a license (Delete) |

Errors are always `{"message": "...", "errors": {field: ["..."]}}`. Anonymous calls return 401.

## Authorization architecture
`apps/accounts/permissions.py` provides `IsSuperAdmin`, `IsAdminOrSuperAdmin` and the factory
`HasModulePermission("module", Action.VIEW)`. Actions: Entry, View, Edit, Delete, Reports, Export/Print.
`UserModulePermission` stores per-user, per-module grants; Super Admin is always allowed, Admin defaults to Entry only.
Super Admin manages Admin access from the profile menu -> Configuration (a right-side drawer titled
Admin Permissions): one global list (Entry, Edit and Delete Access, plus custom headings), each with an ON/OFF
toggle that applies to every project. "+" opens a Permission Heading text input; empty and duplicate names
(ignoring case and extra spaces) are rejected. Each `AdminPermission` row has a unique `name` and an optional
`action`. A heading that reads "<Action> Access" (e.g. "View Access") is mapped to that action and enforced by
`user_can` / `HasModulePermission(module, Action.X)` for Admins in all projects (403 when OFF). Any other custom
heading is stored but unmapped: it grants nothing until it is mapped to an action in code. An action with no
row keeps the default (Entry allowed, others denied). Run `python manage.py migrate` after pulling.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/config/admin-permissions/` | Super Admin: the list `{permissions: [{id, name, enabled, mapped}]}` |
| POST | `/api/config/admin-permissions/` | Super Admin: add a heading `{name}` as an ON entry |
| PATCH | `/api/config/admin-permissions/<id>/` | Super Admin: switch one entry ON/OFF (`{enabled}`) |

## Super Admin dashboard (project usage)
`apps/dashboard/usage.py` reads each project's own database on the same SQL Server instance (read-only; names in
`GSTR_DB_NAME` / `PRN_DB_NAME`, default `Gstr_db` / `PRNPRINT`). Each project is reduced to one row per registered
customer/device plus the time of its latest successful usage; Used and Not Used (counts and lists) are filters over that
one set, so they always agree. Used = has a success; Not Used = none.

**Expiring This Month** is calculated from the license expiry dates Nexus stores (`ProjectCustomer.expires_at`): Trial and
Licensed customers of the project whose expiry falls in the current calendar month in Asia/Kolkata and whose status is still
Active. A trial that already expired earlier this month counts; cancelled and inactivated licenses and licenses with no expiry
date do not. It is computed on every request, so adding a customer, extending a trial, an expiry passing or a status change is
reflected at once. Its list reuses the Customer List record and View More drawer.

- **GSTR2Tally**: registered = ACTIVE `licensed_device_tbl` rows; a success = a `tally_import_job_tbl` job with status
  `COMPLETED`, attributed to the device through its local agent (or its batch's license when that has one device).
- **Barcode Auto Print**: registered = Customers with their non-revoked Licenses (one row per license); a success = the
  license was activated, validated, or seen by a device activation. Print jobs carry no customer link, so they are not used.

A project whose database cannot be read shows "currently unavailable" on its card; the other project still loads.

## Project registration and license management
One reusable module serves every project. A project is declared once in `frontend/src/layouts/navigation.js`
(`PROJECT_ITEMS`: slug, label, icon) and once in `apps/registrations/models.py` (`PROJECTS`); the sidebar then shows
**Add Customer** and **Customer List** under it, and the same pages, components and API serve it:

| Route | Page |
|---|---|
| `/projects/<slug>/add` | Add Customer (Customer / Login / License details); Save and Cancel both return to the list |
| `/projects/<slug>/customers` | Customer List: search, + Add Customer, View, Edit, Extend / Cancel Trial, Inactivate |
| `/projects/<slug>/customers/<id>/edit` | Edit Customer (profile fields; license, key, User ID and password are fixed) |

Shared components: `ProjectSidebarMenu`, `CustomerForm`, `CustomerList`, `LicenseActions`, `CustomerDetailsView`.

Records live in the Nexus database (`apps/registrations`: `ProjectCustomer`, `IssuedActivationKey`, `LicenseAuditLog`),
scoped by project (every query filters on it), so the same User ID or serial can exist in two projects and a record is
never reachable through another project's URL. They are NOT written into the Gstr_db / PRNPRINT runtime databases:
Dashboard counts keep their own definitions (project runtime usage) and the Barcode Auto Print 7-day runtime rule is
untouched. Connecting a Nexus license to a project runtime needs an explicit license policy first;
`services.is_authorized(customer)` is the single check such an integration should call (active trial or active
license only).

- Activation keys: generated server-side (`secrets`), shown once, stored only as a SHA-256 hash, single use and
  project-bound. Passwords use Django's password hashers and are never returned.
- Trial defaults to 3 days (editable, 1-365). Expiry = activation time + days; nothing resets it on login or reinstall.
  Extend adds days to the current expiry (or to now if expired); cancel only works on an active trial; inactivate on any
  non-inactive license.
- Permissions: Super Admin everything. Admin, only while ON in Configuration -> Admin Permissions: register / generate
  key = Entry, list / view = View, edit / extend trial = Edit (Edit may also load a record to edit it), cancel trial /
  inactivate = Delete. The API enforces it; the UI hides the same actions.
- Every registration, edit, key generation, trial change and inactivation, and every Admin Permission add / ON / OFF,
  is written to `LicenseAuditLog` (who, what, when).
- Migrations are additive (`registrations.0001`); existing records are never touched.

## Connected projects (customer portal API)
Nexus is the central customer, credential and license system. A connected project's **server** calls these endpoints; browsers and
desktop apps never hold the project key.

1. Register the project in `PROJECTS` (`backend/apps/registrations/models.py`) and `frontend/src/layouts/navigation.js`.
2. Issue its key (shown once): `python manage.py create_project_client <project> --reset-url https://<project>/reset-password`
3. The project's backend sends `X-Project-Key: <key>` on every call. The project is taken from the key and must match the URL.
4. After a customer signs in, send their token as `X-Customer-Token`.

| Method | `/api/portal/<project>/...` | Purpose |
|---|---|---|
| POST | `register-request/` `{name, contact_number, email}` | Sign Up: creates a **pending** request (no license). 409 if the email already has an account or a pending request |
| POST | `register-request/status/` `{request_id, email}` | `pending \| approved \| rejected` (404 unless id and email both match; no other data) |
| POST | `login/` `{username, password}` | Returns `token`, `must_change_password`, `account`, `license`. Passwords are case-insensitive |
| POST | `change-password/` `{current_password, new_password}` + token | Required after first sign-in (temporary password). Old tokens stop working |
| GET | `license/status/` + token | `{authorized, state, license_type, expires_at, checked_at, lease_until}`; 403 `password_change_required` until changed |
| GET | `account/` + token | Account details (never the password) |
| POST | `recover-username/` `{email}` | Emails the User ID. Always answers 200 |
| POST | `forgot-password/` `{identifier}` (email or User ID) | Emails a one-use reset link to the project's `--reset-url` (`?token=...`). Always 200 |
| POST | `reset-password/` `{token, new_password}` | Completes the reset; the link is single-use and expires (`PASSWORD_RESET_MAX_AGE`) |

### Centralized customer passwords
Nexus is the only place a project customer's User ID (= Serial Number), password hash, license and expiry live; the project (e.g. Barcode Auto Print)
keeps none of them and always calls the portal API with its project key (`X-Project-Key`), so access stays inside that project's customers.
Staff (Superadmin/Admin) and customers are separate tables: staff credentials never sign in to the portal and vice-versa.

- **Hashing:** Django password hashers only; nothing returns or displays a password or hash.
- **Rules:** `change-password` and `reset-password` apply the same validators as staff passwords (8+ chars, not common, not numeric-only,
  not like the email); the new password must differ from the current one. A change takes effect in Nexus immediately: the next login uses
  it and tokens issued for the old password stop working. A rejected password changes nothing (an emailed reset link stays usable).
- **Lockout:** 5 wrong passwords in a row lock that account for 15 minutes (`CUSTOMER_MAX_FAILED_LOGINS`, `CUSTOMER_LOCKOUT_MINUTES`); login answers 429
  `account_locked`. A successful sign-in, a customer password change/reset or a Super Admin reset clears it.
- **Customer View More drawer** (Login Details): *Password Status* (`Temporary – customer must change it` / `Set by customer` /
  `Set (change date not recorded)` for pre-existing accounts) and *Password Last Changed* (`ProjectCustomer.password_changed_at`, set only when the customer chooses a password).
- **Legacy / lost-password accounts:** `POST /api/projects/<project>/customers/<id>/reset-password/` (Super Admin only; `{legacy_reference?}`) emails a
  new random temporary password, forces a change at next sign-in, kills old sessions and audits `PASSWORD_RESET_BY_ADMIN` (+ `LEGACY_ACCOUNT_LINKED` with the reference).
  Also in the drawer as **Reset Password**. A legacy local PRN account that has no Nexus record is created through the normal Sign Up -> Approval flow
  (Add Customer accepts its existing 9-digit serial). Nothing is copied from the legacy side and no default development credential is ever imported.
- Migration `registrations.0004` is additive (`password_changed_at`, `failed_logins`, `locked_until`); existing hashes, licenses and expiry dates are untouched.

### Super Admin approval (one step) and notification
A new sign-up emails every active Super Admin account that has an email address, plus any `NEW_REQUEST_NOTIFY_EMAILS`
(best effort; a mail failure never fails the sign-up). Staff endpoints (session login + CSRF, **Entry** permission; Super Admin always):

| Method | `/api/projects/<project>/registration-requests/...` | Purpose |
|---|---|---|
| GET | `` | Pending requests `{results: [{id, name, contact_number, email, status, created_at, ...}]}` |
| GET | `<id>/` | Open one request (pending or processed) |
| POST | `<id>/approve/` | Creates the customer, issues the license and emails the credentials in one step. 409 if already processed |
| POST | `<id>/reject/` `{reason?}` | Marks the request Rejected; creates and sends nothing. 409 if already processed |

`approve` body: `license_type` (`TRIAL`/`LICENSED`, required); optional `trial_days` (1-365, default 3), `serial_number`
(9 digits starting 7; generated if omitted), `temporary_password` (8+ chars; generated if omitted), `company_name`, `address`, `gstin`.
**Username (User ID) = Serial Number.** The activation key is generated server-side. Response 201: the customer record plus
`email_sent`; only when the email could not be sent it also carries `temporary_password` (once) so it can be passed on another way.

**Flow:** Sign Up -> pending request (shown to Super Admin / Admin with Entry permission above the project's Customer List) ->
*Add Customer* (prefilled; serial number = User ID; temporary password; activation key; Trial/Licensed) -> credentials emailed ->
customer signs in, must change password -> project calls `license/status/`. A cancelled, expired or inactive license returns
`authorized: false`.

**Offline desktop apps (e.g. Barcode Auto Print):** `lease_until` is the latest time the client may rely on a status answer: at most
`LICENSE_LEASE_DAYS` (default 7, matching the existing runtime rule) and never past the license expiry. A revocation therefore reaches an
offline client within that window. The lease is plain data over TLS; tamper-proofing it for offline use needs an asymmetric signature,
which is **not** implemented and needs approval before the 7-day rule is changed or relied on.

**Email:** configure `EMAIL_*` in `.env` (see `.env.example`). In DEBUG, mail prints to the console. Credential emails are branded HTML with a plain-text
twin, retried up to `MAIL_SEND_ATTEMPTS` times, and every send is recorded in `registrations_emaillog` (status, attempts, error; never the body). If sending fails
the customer is still saved and Add Customer reports that the email did not go out. Trial emails never state the trial length; 1 Year shows the expiry date;
Lifetime shows no expiry. The temporary password must be replaced at first sign-in (`ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE`, default on).

**Operations:** `GET /api/health/` checks the process and SQL Server. Production deployment, the HTTP 502 checklist, proxy and CORS settings: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Endpoint reference: [docs/API.md](docs/API.md).

## Admin / Super Admin synchronisation
Admins and Super Admins work on the same SQL Server records (`ProjectCustomer`, one row per project customer); nothing is kept per user.
Every change records who made it (`created_by`, `updated_by`) and writes a `LicenseAuditLog` entry (actor, action, time).

- `GET /api/projects/<project>/sync/` (View, Entry or Edit) and `GET /api/dashboard/sync/` (Super Admin) return a cheap fingerprint
  (newest audit id + customer count). Open Customer List and Dashboard pages poll it every 10 s (paused while the tab is hidden) and
  reload only when it changes, so another session's change appears without a manual refresh.
- `GET /api/projects/<project>/activity/[?customer=<id>&limit=<n>]` (Super Admin only): the audit trail, newest first. It is shown in
  the customer's View More drawer and in the "Recent activity" panel above the Customer List.
- Each action is still checked against the user's Admin Permissions in the API; the fingerprint exposes no customer data.

## Signed device licensing (Barcode Auto Print)
Nexus issues Ed25519-signed, device-bound entitlements and enrolls one computer per licence. Full endpoint, header, payload and key reference:
[docs/DEVICE-LICENSING.md](docs/DEVICE-LICENSING.md). Setup: `python manage.py migrate`, `python manage.py generate_license_keypair`, put the private key in
`LICENSE_SIGNING_PRIVATE_KEY` (server environment only) and give Barcode Auto Print the public key. Super Admin transfers a device with
`POST /api/projects/prn-auto-print/customers/<id>/device/revoke/`.
