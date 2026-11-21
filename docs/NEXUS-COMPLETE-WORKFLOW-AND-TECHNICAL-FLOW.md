# Nexus – Complete Workflow, Technical Flow and ERD

**Products covered:** Barcode AutoPrint (PRN) · GSTR2Tally · Mothys Inventory (and any future "universal" project)
**Stack:** Django + DRF backend (`backend/`), React + Vite SPA (`frontend/`), SQL Server database `SaiInfosysNexus`
**Source of truth:** this document was written from the code (`models.py`, `services.py`, `device.py`, `urls.py`, `App.jsx`) on branch `nexus-web`. Where a fact comes from code, the file is named so it can be checked.

> Diagrams are Mermaid. They render on GitHub, and in VS Code with the extension **"Markdown Preview Mermaid Support"** (open the preview with `Ctrl+Shift+V`).

---

## Contents
1. [Big picture](#1-big-picture)
2. [Common foundation (staff login, roles, security)](#2-common-foundation)
3. [Barcode AutoPrint (PRN)](#3-barcode-autoprint-prn)
4. [GSTR2Tally](#4-gstr2tally)
5. [Mothys Inventory (Universal Integration)](#5-mothys-inventory-universal-integration)
6. [Dashboard and reports](#6-dashboard-and-reports)
7. [Frontend map](#7-frontend-map)
8. [Side-by-side comparison](#8-side-by-side-comparison)
9. [Combined ERD](#9-combined-erd)
10. [Known gaps and things to verify](#10-known-gaps-and-things-to-verify)

---

## 1. Big picture

Nexus is the **central licensing and customer-management server** for Sai Infosys products. Each product (the customer's own app) calls Nexus to register customers, verify logins, check licences and report usage. Sai Infosys staff use the Nexus web portal to approve customers, activate licences, manage devices, and see dashboards.

### 1.1 Three licensing families (one Django app each)

| Product | Django app | URL prefix | Who calls Nexus | How the product proves itself | Licence enforced where |
|---|---|---|---|---|---|
| **Barcode AutoPrint** (`prn-auto-print`) | `registrations` | `/api/device/prn-auto-print/…` and `/api/portal/prn-auto-print/…` | Windows agent on the customer's PC | **Ed25519 device signature** on every request + customer token | **On the customer PC**, via a Nexus-**signed entitlement** (offline lease up to 7 days) |
| **GSTR2Tally** (`gstr2tally`) | `tallysl` | `/api/v1/tally-sl/gstr2tally/…` | Local GSTR client that reads TallyPrime | `X-Product-Key` header + customer **bearer session token** | **On Nexus**, checked per session / licence status call |
| **Mothys Inventory** (`MOTHYS`) and future projects | `integrations` | `/api/integrations/v1/…` | The project's own **backend** (FastAPI), server-to-server | `X-Client-Id` + `X-Client-Secret` | **On Nexus**; the project backend introspects the session |

Staff-side APIs: `/api/auth/…`, `/api/config/…`, `/api/projects/<p>/…` (PRN), `/api/v1/tally-sl-admin/…` (GSTR), `/api/integrations/admin/…` (Mothys), `/api/dashboard/…`.

### 1.2 Architecture

```mermaid
flowchart LR
    subgraph Staff["Sai Infosys staff (browser)"]
        SPA["Nexus React SPA<br/>(Super Admin / Admin)"]
    end

    subgraph Customers["Customer side"]
        PRN["Barcode AutoPrint agent<br/>(Windows, Ed25519 key)"]
        GSTR["GSTR2Tally client<br/>(reads TallyPrime)"]
        MOT["Mothys FastAPI backend<br/>(+ React front end)"]
    end

    subgraph Nexus["Nexus backend (Django REST)"]
        ACC["accounts<br/>login, roles, permissions"]
        REG["registrations<br/>PRN licensing"]
        TAL["tallysl<br/>GSTR licensing"]
        INT["integrations<br/>universal licensing"]
        DASH["dashboard<br/>usage + expiry"]
        MAIL["mailer<br/>(SMTP, EmailLog)"]
    end

    DB[("SQL Server<br/>SaiInfosysNexus")]
    GDB[("Gstr_db<br/>read-only")]
    PDB[("PRNPRINT<br/>legacy, read-only")]

    SPA -- "session cookie + CSRF" --> ACC
    SPA --> REG
    SPA --> TAL
    SPA --> INT
    SPA --> DASH

    PRN -- "device-signed requests" --> REG
    GSTR -- "X-Product-Key + bearer" --> TAL
    MOT -- "X-Client-Id / Secret" --> INT

    REG --> DB
    TAL --> DB
    INT --> DB
    ACC --> DB
    DASH --> DB
    DASH -. "three-part names" .-> GDB
    DASH -. "three-part names" .-> PDB

    REG --> MAIL
    TAL --> MAIL
    INT --> MAIL
```

### 1.3 Isolation rule

The three product families are **deliberately separate tables and code**. `tallysl_*` tables never join to `registrations` tables, and `integration_*` tables never join to either (stated in the docstrings of `tallysl/models.py` and `integrations/models.py`). Only these are shared: Django `User`, `UserProfile`, and the staff permission model. `ProjectCustomer.project` (PRN) is a **string code**, not a foreign key.

---

## 2. Common foundation

### 2.1 Staff roles and permissions

| Role (`accounts.constants.Role`) | Meaning |
|---|---|
| `SUPERADMIN` | Full control. Only role that can approve first activations, transfer or revoke devices, rotate project credentials, set permissions. |
| `ADMIN` | Has **only what a Super Admin grants**. Default grant is nothing (`DEFAULT_ADMIN_ACTIONS = frozenset()`). |

Permissions are **per user, per module/project**:

* **PRN and generic modules** – `UserModulePermission` (one row per user + module) with flags: entry, view, edit, delete, extend_trial, cancel_trial, inactivate, approve, device_transfer, renew, reports, export_print.
* **GSTR2Tally** – `TallyAdminPermission` (one row per user + product) with flags: view, entry, edit, delete, license_activation, license_management, reports, device_management.
* **Global switch list** – `AdminPermission` (a heading ON/OFF for the whole Admin role).

### 2.2 Staff login flow

```mermaid
sequenceDiagram
    autonumber
    actor S as Staff user
    participant UI as React SPA
    participant API as Django /api/auth
    participant DB as SQL Server

    UI->>API: GET /auth/csrf/
    API-->>UI: sets nexus_csrftoken cookie
    S->>UI: username + password
    UI->>API: POST /auth/login/ (X-CSRFToken)
    API->>DB: case-insensitive user lookup, check password hash
    alt wrong password
        API-->>UI: 401 (throttled 10/min, lockout)
    else ok
        API->>DB: create session row (SESSION_ENGINE = db)
        API-->>UI: sets nexus_sessionid (HttpOnly, SameSite=Lax)
        UI->>API: GET /auth/me/
        API-->>UI: role + module permissions
        UI->>UI: menus and routes shown by permission
    end
```

* Session timeout is **sliding** (`SESSION_SAVE_EVERY_REQUEST`, default 8 h).
* Anonymous requests get **401**; CSRF or permission denial gets **403**, so the SPA can tell them apart (`core/authentication.py`).
* Passwords are **case-insensitive** (casefolded before hashing) and have no complexity rules, by design (`settings.py`).

### 2.3 Security conventions used by every product

| Concern | What the code does |
|---|---|
| Secrets at rest | Passwords, activation keys, product keys, client secrets, session tokens are stored **only as hashes** (SHA-256 or HMAC). Raw values are shown or emailed once. |
| Lockout | Wrong passwords counted (`failed_logins`, `locked_until`); 15-minute lockout (`CUSTOMER_LOCKOUT_MINUTES`). |
| Throttling | Per-IP / per-client rates in `REST_FRAMEWORK.DEFAULT_THROTTLE_RATES` (login, portal, device, device_enroll, device_public). |
| Anti-enumeration | Wrong serial and wrong key give the **same** error; forgot-password / recover-username answer neutrally. |
| Audit | Every sensitive step is written to an append-only audit table (`LicenseAuditLog`, `TallyAuditLog`, `IntegrationAudit`). |
| Email | `EmailLog` records who/what/status. Bodies are never stored. `accepted` means "handed to the mail server", **not** "delivered". |
| Errors | `{"message","code","errors"}` (PRN, GSTR) · `{"ok":false,"error":{"code","message"},"request_id"}` (Mothys). |

### 2.4 Staff ERD (shared tables)

```mermaid
erDiagram
    AUTH_USER ||--o| USER_PROFILE : "has role"
    AUTH_USER ||--o{ USER_MODULE_PERMISSION : "granted"
    AUTH_USER ||--o{ TALLY_ADMIN_PERMISSION : "granted"
    TALLY_PRODUCT ||--o{ TALLY_ADMIN_PERMISSION : "scoped to"

    AUTH_USER {
        int id PK
        string username
        string password_hash
    }
    USER_PROFILE {
        int id PK
        int user_id FK
        string role "SUPERADMIN or ADMIN"
    }
    USER_MODULE_PERMISSION {
        int id PK
        int user_id FK
        string module "project code or menu"
        bool can_entry
        bool can_view
        bool can_edit
        bool can_delete
        bool can_approve
        bool can_device_transfer
        bool can_renew
        bool can_reports
    }
    ADMIN_PERMISSION {
        int id PK
        string name UK
        string action UK
        bool enabled
    }
    TALLY_ADMIN_PERMISSION {
        int id PK
        int user_id FK
        int product_id FK
        bool can_view
        bool can_license_activation
        bool can_license_management
        bool can_device_management
        bool can_reports
    }
```

---

## 3. Barcode AutoPrint (PRN)

Project code `prn-auto-print`. App: `backend/apps/registrations`. Detailed contract: `docs/DEVICE-LICENSING.md`.

### 3.1 Business workflow (what happens, in order)

```mermaid
flowchart TD
    A["Prospect asks for access<br/>(public register-request from the app/site)"] --> B["RegistrationRequest = PENDING"]
    B --> C{"Super Admin / Admin<br/>reviews"}
    C -- Reject --> R["REJECTED + reason<br/>no customer, no licence"]
    C -- Approve --> D["Choose package<br/>Trial / 1 Year / Lifetime"]
    D --> E["Customer record created<br/>Serial = User ID (9 digits, starts 7)<br/>temporary password + Activation Key"]
    E --> F["Credentials email sent<br/>(User ID, temp password, key, steps)"]
    F --> G["Customer installs agent<br/>and enters Serial + Activation Key"]
    G --> H{"Is this the licence's<br/>very first computer?"}
    H -- Yes --> I["FIRST_ACTIVATION request = PENDING<br/>customer sees 'waiting for approval'"]
    I --> J{"Super Admin decision"}
    J -- Reject --> K["Activation rejected"]
    J -- Approve --> L["Computer bound to licence<br/>validity clock starts (once)"]
    H -- "No, same PC (reinstall)" --> L
    L --> M["Customer signs in: User ID + password<br/>must change temporary password"]
    M --> N["Normal use: hourly entitlement refresh,<br/>usage reports (start / print / heartbeat)"]
    N --> O{"Licence ends?"}
    O -- "Trial/1 Year ended" --> P["Renew (1 Year) or Extend Trial<br/>by authorised staff"]
    P --> N
    O -- "Customer changes PC" --> Q["Transfer request (own or other licence)<br/>Super Admin approves, old PC revoked"]
    Q --> L
```

**Package rules** (`registrations/models.py`, `device.py`, `docs/DEVICE-LICENSING.md`):

| Package | Stored value | Validity | Notes |
|---|---|---|---|
| Trial | `TRIAL` | first activation + **7 days** (`TRIAL_DAYS_BY_PRODUCT`) | Longer only after authorised **Extend Trial** |
| 1 Year | `ONE_YEAR` | first activation + 365 days | Moves only through **Renew** (recorded in `LicenseRenewal`) |
| Lifetime | `LICENSED` (kept for back-compat) | no expiry (`expires_at = null`) | Never in expiry reports |

Status values: `ACTIVE`, `TRIAL_CANCELLED`, `INACTIVE`. Device states shown to staff: `not_activated`, `active`, `revoked`, `transfer_pending`.

### 3.2 Technical flow – registration to activation

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant A as PRN agent (PC)
    participant N as Nexus /api/device + /api/projects
    participant DB as SQL Server
    actor SA as Super Admin
    participant UI as Nexus SPA

    C->>A: fills sign-up
    A->>N: POST device/prn-auto-print/public/register-request/
    N->>DB: INSERT RegistrationRequest (PENDING, unique per project+email)
    SA->>UI: opens Registration Requests
    UI->>N: POST projects/prn-auto-print/registration-requests/{id}/approve/
    N->>DB: BEGIN. generate serial, activation key (hash), temp password (hash)
    N->>DB: INSERT ProjectCustomer + IssuedActivationKey, request=PROCESSED
    N->>DB: INSERT LicenseAuditLog
    N-->>C: credentials email (EmailLog written)
    Note over N,UI: if email not accepted, response returns password and key ONCE for hand-over

    C->>A: enters Serial + Activation Key
    A->>A: generate Ed25519 keypair, collect hardware hashes (smbios, machine, v3)
    A->>N: POST device/prn-auto-print/activate/
    N->>DB: match serial + key hash, check licence state
    alt first computer and approval required
        N->>DB: INSERT DeviceTransferRequest (FIRST_ACTIVATION, PENDING)
        N-->>A: 403 activation_pending
        SA->>UI: Device Transfer Requests
        UI->>N: POST device-transfer-requests/{id}/approve/
        N->>DB: lock customer, bind device, set first_activated_at, start trial/1-year clock
        N->>DB: audit DEVICE_ENROLLED
        A->>N: activate/ again (or signed login)
    end
    N->>DB: INSERT DeviceRegistration (generation = max + 1)
    N-->>A: device_id + signed entitlement + license (no token)
    A->>N: signed POST login/ (User ID + password)
    N-->>A: customer token bound to this device
    opt must_change_password
        A->>N: signed POST change-password/
        N->>DB: store new hash, revoke old tokens
    end
```

### 3.3 Technical flow – every signed request and the offline lease

Every call after enrolment carries:

`X-Device-Id`, `X-Device-Timestamp` (UTC, ±5 min), `X-Device-Nonce` (single use), `X-Device-Signature` = Ed25519 over
`METHOD \n PATH \n TIMESTAMP \n NONCE \n sha256(body)`; customer calls also send `X-Customer-Token`.

```mermaid
sequenceDiagram
    autonumber
    participant A as PRN agent
    participant N as Nexus
    participant DB as SQL Server

    loop hourly while online
        A->>N: signed POST entitlement/ (fingerprint)
        N->>DB: look up DeviceRegistration, load public key
        N->>N: verify signature, timestamp window, nonce
        N->>DB: INSERT DeviceNonce (unique per device+nonce, replay blocked)
        N->>DB: compare HMAC of fingerprint with stored, check licence state
        alt licence valid
            N-->>A: entitlement {payload, sig, key_id} + license
            Note over A: valid_until = min(issued_at + 7 days, expires_at)
            A->>A: verify with PINNED public key, allow use until valid_until
        else expired / cancelled / inactive
            N-->>A: 403 license_expired / license_cancelled / account_inactive
        else device was transferred
            N-->>A: 403 device_revoked
        end
    end
    A->>N: signed POST usage/ (start, print, heartbeat, login)
    N->>DB: update last_used_at, use_count
```

* **Licence vs lease:** the *licence* end (`expires_at`) is set once at first activation and changes only by extension or renewal. The *lease* (`valid_until`) is a short 7-day permission slip the agent must keep renewing online.
* **Signing:** Ed25519 private key lives only in the server environment (`LICENSE_SIGNING_PRIVATE_KEY`). The agent pins the matching public key; rotation is by `key_id`.
* Background `entitlement/` refreshes and activation do **not** count as usage; login, start, print and heartbeat do.

### 3.4 Technical flow – device change

```mermaid
flowchart TD
    S["Customer cannot sign in on a PC"] --> W{"Why?"}
    W -- "PC is another customer's active device<br/>403 device_bound_to_other / wrong_device_account" --> T1["POST transfer-request<br/>(username, password, fingerprint)"]
    W -- "Own licence active on a different PC<br/>409 device_limit" --> T1
    T1 --> P["DeviceTransferRequest TRANSFER = PENDING<br/>(one pending per device + customer)"]
    P --> D{"Super Admin only"}
    D -- Reject --> RJ["Nothing changes, customer may ask again"]
    D -- Approve --> AP["One transaction, row-locked:<br/>old DeviceRegistration.revoked_at set<br/>(kept as history), request APPROVED"]
    AP --> NX["New PC signs in or activates<br/>generation + 1, dates unchanged"]
    NX --> OLD["Old PC: next call gets 403 device_revoked<br/>keeps printing only until its lease ends"]

    SA["Super Admin proactively"] --> RV["Revoke device (optional block)<br/>kind = REVOKE, recorded APPROVED"]
    SA --> TR["Authorise transfer<br/>lifts block, frees slot"]
```

An **Activation Key or Serial alone never moves a licence** – a transfer needs the customer's password as proof of ownership.

### 3.5 State machines

```mermaid
stateDiagram-v2
    direction LR
    [*] --> PENDING_REQUEST: sign-up
    PENDING_REQUEST --> REJECTED: staff rejects
    PENDING_REQUEST --> PROCESSED: staff approves, customer created
    PROCESSED --> Active
    state Active {
        [*] --> NotActivated
        NotActivated --> ActivationPending: first PC asks
        ActivationPending --> DeviceActive: Super Admin approves
        ActivationPending --> NotActivated: rejected
        DeviceActive --> Revoked: revoke / transfer
        Revoked --> DeviceActive: new PC enrolls, generation+1
    }
    Active --> Expired: trial or 1-year end passes
    Expired --> Active: Renew / Extend Trial
    Active --> TRIAL_CANCELLED: cancel trial
    Active --> INACTIVE: inactivate
```

### 3.6 ERD – Barcode AutoPrint

```mermaid
erDiagram
    AUTH_USER ||--o{ ISSUED_ACTIVATION_KEY : "created_by"
    AUTH_USER ||--o{ PROJECT_CUSTOMER : "created_by / updated_by"
    PROJECT_CUSTOMER ||--o{ LICENSE_RENEWAL : "renewals"
    PROJECT_CUSTOMER ||--o{ DEVICE_REGISTRATION : "devices"
    PROJECT_CUSTOMER ||--o{ DEVICE_TRANSFER_REQUEST : "to_customer"
    PROJECT_CUSTOMER |o--o{ DEVICE_TRANSFER_REQUEST : "from_customer"
    DEVICE_REGISTRATION |o--o{ DEVICE_TRANSFER_REQUEST : "device in the way"
    DEVICE_REGISTRATION ||--o{ DEVICE_NONCE : "used nonces"
    PROJECT_CUSTOMER |o--o{ REGISTRATION_REQUEST : "becomes"
    PROJECT_CUSTOMER |o--o{ LICENSE_AUDIT_LOG : "about"
    PROJECT_CUSTOMER |o--o{ EMAIL_LOG : "about"
    AUTH_USER |o--o{ LICENSE_AUDIT_LOG : "actor"
    PROJECT_CLIENT }o..o{ PROJECT_CUSTOMER : "same project code (logical)"

    PROJECT_CUSTOMER {
        int id PK
        string project "prn-auto-print or gstr2tally (string, not FK)"
        string name
        string company_name
        string license_type "TRIAL, ONE_YEAR, LICENSED=Lifetime"
        string user_id "UK with project"
        string serial_number "UK with project when set"
        string password_hash
        bool must_change_password
        string email
        string gstin
        string status "ACTIVE, TRIAL_CANCELLED, INACTIVE"
        int trial_days
        string activation_key_hash UK
        string activation_key_suffix
        datetime activated_at
        datetime first_activated_at "set once"
        datetime expires_at "null = Lifetime"
        bool device_blocked
        int failed_logins
        datetime locked_until
        string credentials_email_status
        datetime created_at
    }
    ISSUED_ACTIVATION_KEY {
        int id PK
        string project
        string key_hash UK
        string key_suffix
        bool used
        int created_by FK
    }
    LICENSE_RENEWAL {
        int id PK
        int customer_id FK
        datetime previous_expires_at
        datetime new_expires_at
        int renewed_by FK
        string note
    }
    DEVICE_REGISTRATION {
        uuid device_id PK
        int customer_id FK
        string project
        string device_public_key "Ed25519, base64"
        text fingerprint_hashes "HMAC of smbios, machine, v3"
        string device_name
        string app_version
        int generation
        datetime enrolled_at
        datetime last_seen_at
        datetime last_used_at
        int use_count
        datetime revoked_at "null = active (1 active per customer)"
        string revoked_reason
    }
    DEVICE_TRANSFER_REQUEST {
        int id PK
        string project
        string kind "FIRST_ACTIVATION, TRANSFER, REVOKE"
        uuid device_id FK "registration in the way"
        int from_customer_id FK
        int to_customer_id FK
        string requesting_device_name
        string device_public_key
        text fingerprint_hashes
        string status "PENDING, APPROVED, REJECTED"
        datetime requested_at
        datetime decided_at
        string decided_by
        string decision_reason
    }
    DEVICE_NONCE {
        int id PK
        uuid device_id FK
        string nonce "UK with device"
        datetime created_at
    }
    REGISTRATION_REQUEST {
        int id PK
        string project
        string name
        string contact_number
        string email
        string status "PENDING, PROCESSED, REJECTED"
        string reject_reason
        int customer_id FK
        datetime processed_at
    }
    EMAIL_LOG {
        int id PK
        string kind
        string project
        int customer_id FK
        string recipient
        string status "accepted, not_delivered, failed"
        int attempts
    }
    LICENSE_AUDIT_LOG {
        int id PK
        string project
        int customer_id FK
        string action
        string detail
        int actor_id FK
        string actor_name
        datetime created_at
    }
    PROJECT_CLIENT {
        int id PK
        string project
        string key_hash UK
        string reset_url
        bool active
    }
```

**Database rules that matter:** `uniq_active_device_per_customer` (one non-revoked device per customer) · `uniq_customer_user_id_per_project` · `uniq_customer_serial_per_project` · `uniq_pending_request_per_project_email` · `uniq_pending_first_activation_per_customer` · `uniq_pending_transfer_per_device_customer` · `uniq_device_nonce`.

### 3.7 Endpoint map (PRN)

| Caller | Endpoints (under `/api/`) |
|---|---|
| Public (rate-limited 10/min/IP) | `device/prn-auto-print/public/{register-request, register-request/status, forgot-password, recover-username, reset-password, keys}` |
| Agent, no signature (credentials in body) | `device/…/enroll/`, `activate/`, `transfer-request/`, `transfer-request/status/` |
| Agent, **signed** | `device/…/entitlement/`, `login/`, `usage/`, `license/status/`, `account/`, `change-password/`, `register-request/`, `forgot-password/`, `recover-username/` |
| Connected project backend (project key) | `portal/prn-auto-print/{register-request, login, forgot-password, reset-password, change-password, license/status, account, …}` |
| Staff (session) | `projects/<p>/customers/…`, `registration-requests/…`, `activation-key/`, `serial-number/`, `temporary-password/`, `customers/<id>/{extend-trial, cancel-trial, inactivate, renew, renewals, reset-password, resend-credentials, reissue-activation-key, device, device/revoke, device/transfer}`, `device-transfer-requests/…`, `activity/`, `sync/`, `reports/license-expiry`, `reports/usage` |

---

## 4. GSTR2Tally

Product code `gstr2tally`. App: `backend/apps/tallysl` (isolated `tallysl_*` tables). Detailed contract: `docs/TALLY-SL-API-CONTRACT.md`, `docs/TALLY-SL-LICENSING.md`.

Key identity rule: **Tally Serial Number = Product Serial Number = User ID.** The GSTR client **reads** it from TallyPrime; Nexus never generates it.

### 4.1 Business workflow

```mermaid
flowchart TD
    A["Customer opens GSTR2Tally<br/>client reads Tally serial from TallyPrime"] --> B["Sign Up: name, email, contact, tally_serial"]
    B --> C["TallyCustomer + TallyLicense = PENDING<br/>no access yet"]
    C --> D["Temporary password emailed<br/>Super Admins notified<br/>customer visible in Nexus at once"]
    D --> E{"Super Admin (or staff with<br/>'License Activation')"}
    E --> F["Activate licence: choose plan<br/>Trial (default 3 days) / Annual (365) / Lifetime"]
    F --> G["Licence ACTIVE + Nexus Activation Key generated<br/>and emailed (hash stored)"]
    G --> H["Customer: Activate User screen<br/>enters User ID, asks for key"]
    H --> I["New single-use key emailed (15 min)<br/>only for ACTIVE licences"]
    I --> J["Customer enters key -> Verify"]
    J --> K{"Licence on another PC?"}
    K -- No --> M["Confirm: this PC enrolled"]
    K -- Yes --> L["Warning: old PC will be signed out<br/>customer chooses OK or Cancel"]
    L --> M
    M --> N["Session token issued, GSTR app opens<br/>login + open usage recorded"]
    N --> O["Client reports Tally details<br/>(edition, TSS, expiry...)"]
    N --> P["Client confirms its own device transfers<br/>-> Nexus stores a notification-only event"]
    N --> Q["Staff monitors: dashboard, reports,<br/>expiry, performance, notifications"]
```

Notable differences from PRN: **no per-device cryptographic signing and no Super Admin approval of a new device** – the customer confirms a device change themselves after proving the emailed key. Nexus is told about completed transfers by the app (`TallyTransferEvent`); it does not approve them.

### 4.2 Technical flow – registration to session

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant G as GSTR2Tally client
    participant N as Nexus /api/v1/tally-sl/gstr2tally
    participant DB as SQL Server
    actor SA as Super Admin
    participant UI as Nexus SPA

    G->>N: GET identify/ (X-Product-Key)
    N->>DB: match TallyProductKey.key_hash, active
    N-->>G: product code, name, server_time

    C->>G: Sign Up
    G->>N: POST register/ {name, email, contact_number, tally_serial}
    N->>DB: INSERT TallyCustomer (password hash, must_change_password)
    N->>DB: INSERT TallyLicense (PENDING)
    N-->>C: welcome email with temporary password
    N-->>SA: notification + TallyAuditLog REGISTERED

    SA->>UI: Customers > Activate licence
    UI->>N: POST tally-sl-admin/products/gstr2tally/customers/{id}/license/activate/
    N->>DB: lock customer, set type, status ACTIVE, expires_at
    N->>DB: store activation key hash + suffix + issued_at
    N-->>C: email with Nexus Activation Key

    C->>G: Activate User, enters User ID
    G->>N: POST activate/request-key/
    N->>DB: licence active? 60 s apart? under 5 per hour?
    N->>DB: INSERT TallyVerificationCode (keyed hash, expires +15 min), older ones consumed
    N-->>C: new single-use key by email (always neutral reply to client)
    C->>G: types key
    G->>N: POST activate/verify/ {user_id, activation_key}
    N->>DB: compare hash bound to customer id, attempts under 5
    N-->>G: single-use ticket (valid 10 min), requires_confirmation flag
    G->>N: POST activate/confirm/ {ticket, device, confirm_transfer}
    N->>DB: BEGIN, lock ticket + customer, re-check licence
    alt other device active and not confirmed
        N-->>G: 409 confirmation_required (ticket stays valid)
    else
        N->>DB: revoke old TallyDevice, sessions revoked (device_replaced)
        N->>DB: INSERT TallyDevice (generation+1), TallyDeviceTransfer, consume ticket
        N->>DB: INSERT TallySession (token hash)
        N-->>G: bearer token + license payload + account
    end
```

### 4.3 Technical flow – daily use, password reset, transfer notice

```mermaid
sequenceDiagram
    autonumber
    participant G as GSTR client
    participant N as Nexus
    participant DB as SQL Server

    G->>N: POST auth/login/ {user_id, password, device{client_device_id, fingerprint}}
    N->>DB: check password, lockout, licence state
    N->>DB: match device by keyed hash of fingerprint
    N-->>G: token + license {authorized, state, expires_at}

    loop while app is open
        G->>N: POST auth/refresh/ (extend session)
        G->>N: GET license/status/ (bearer)
        G->>N: POST usage/open/ (kind open or login)
        N->>DB: INSERT TallyUsageEvent (occurred_at, IST day)
        G->>N: POST tally-info/ (edition, TSS expiry, availability per field)
        N->>DB: UPSERT TallyInfo
    end

    Note over G,N: Forgot password
    G->>N: POST auth/forgot-password/
    N->>DB: INSERT TallyPasswordReset (token hash), OTP emailed
    G->>N: POST auth/verify-otp/ then auth/reset-password/
    N->>DB: attempts capped (OTP_MAX_ATTEMPTS), mark used, revoke sessions

    Note over G,N: App-performed device transfer
    G->>N: POST events/device-transfer/ {event_id, old/new device refs}
    N->>DB: INSERT TallyTransferEvent (unique event_id, idempotent)
    N-->>N: bell notification for staff (read_at / read_by)
```

**Licence state** (`tallysl/services.license_state`): `pending` → `active` → `expired` (type ≠ Lifetime and `expires_at` ≤ now) · `cancelled` (TRIAL_CANCELLED) · `inactive`.

**Plans** (`activate_license`): Trial = N days (1–365, default `TallyProduct.default_trial_days = 3`) · Annual = 365 days (stored as `LICENSED`) · Lifetime = no expiry.

**Staff licence actions:** activate, extend trial, cancel trial, renew, inactivate, reissue key, revoke device; credentials: resend / reset (`credentials/<action>/`). Each needs the matching `TallyAdminPermission` flag.

### 4.4 ERD – GSTR2Tally

```mermaid
erDiagram
    TALLY_PRODUCT ||--o{ TALLY_PRODUCT_KEY : "keys"
    TALLY_PRODUCT ||--o{ TALLY_CUSTOMER : "customers (PROTECT)"
    TALLY_PRODUCT ||--o{ TALLY_TRANSFER_EVENT : "events"
    TALLY_PRODUCT ||--o{ TALLY_ADMIN_PERMISSION : "staff grants"
    TALLY_CUSTOMER ||--|| TALLY_LICENSE : "one licence"
    TALLY_CUSTOMER ||--o| TALLY_INFO : "tally details"
    TALLY_CUSTOMER ||--o{ TALLY_DEVICE : "devices"
    TALLY_CUSTOMER ||--o{ TALLY_DEVICE_TRANSFER : "device history"
    TALLY_CUSTOMER ||--o{ TALLY_TRANSFER_EVENT : "app-reported transfers"
    TALLY_CUSTOMER ||--o{ TALLY_VERIFICATION_CODE : "activation keys"
    TALLY_CUSTOMER ||--o{ TALLY_SESSION : "sessions"
    TALLY_CUSTOMER ||--o{ TALLY_PASSWORD_RESET : "resets"
    TALLY_CUSTOMER ||--o{ TALLY_USAGE_EVENT : "usage"
    TALLY_CUSTOMER |o--o{ TALLY_AUDIT_LOG : "audit"
    TALLY_DEVICE |o--o{ TALLY_SESSION : "bound to"
    TALLY_DEVICE |o--o{ TALLY_USAGE_EVENT : "on device"
    TALLY_DEVICE |o--o| TALLY_INFO : "reported_device"
    TALLY_DEVICE |o--o{ TALLY_DEVICE_TRANSFER : "from / to"
    AUTH_USER |o--o{ TALLY_LICENSE : "activated_by"

    TALLY_PRODUCT {
        int id PK
        string code UK "gstr2tally"
        string name
        string kind
        bool active
        int default_trial_days "3"
        string password_reset_url
        string activation_link_base
    }
    TALLY_PRODUCT_KEY {
        int id PK
        int product_id FK
        string key_hash UK "X-Product-Key"
        bool active
    }
    TALLY_CUSTOMER {
        int id PK
        int product_id FK
        string tally_serial "UK with product = User ID"
        string name
        string email
        string contact_number
        string company_name
        string gstin
        string password_hash
        bool must_change_password
        int failed_logins
        datetime locked_until
        string welcome_email_status
        datetime registered_at
    }
    TALLY_LICENSE {
        int id PK
        int customer_id FK "one-to-one"
        string license_type "TRIAL, LICENSED, LIFETIME"
        string status "PENDING, ACTIVE, TRIAL_CANCELLED, INACTIVE"
        int trial_days
        datetime activated_at
        datetime expires_at "null = Lifetime or not started"
        string activation_key_hash
        string activation_key_suffix
        datetime activation_key_issued_at
        int activated_by FK
    }
    TALLY_INFO {
        int id PK
        int customer_id FK "one-to-one"
        string edition
        string tss_details
        int virtual_user_limit
        string license_administrator
        string gateway_server
        date tss_expiry_date "Tally expiry, not Nexus"
        text availability "verified or unavailable per field"
        int reported_device_id FK
    }
    TALLY_DEVICE {
        uuid device_id PK
        int customer_id FK
        string client_device_id
        string fingerprint_hash "keyed hash only"
        string device_name
        int generation
        datetime enrolled_at
        datetime revoked_at "1 active per customer"
        string revoked_reason
    }
    TALLY_DEVICE_TRANSFER {
        int id PK
        int customer_id FK
        string kind "ENROLLED, REPLACED, REVOKED"
        uuid from_device_id FK
        uuid to_device_id FK
        string performed_by
        string ip_address
    }
    TALLY_TRANSFER_EVENT {
        int id PK
        string event_id UK "idempotency"
        int product_id FK
        int customer_id FK
        string old_device_ref
        string new_device_ref
        string status "COMPLETED"
        datetime occurred_at
        datetime read_at
        string read_by
    }
    TALLY_VERIFICATION_CODE {
        int id PK
        int customer_id FK
        string code_hash
        int attempts
        datetime expires_at
        datetime verified_at
        string ticket_hash
        datetime ticket_expires_at
        datetime consumed_at
    }
    TALLY_SESSION {
        int id PK
        int customer_id FK
        uuid device_id FK
        string token_hash UK
        datetime expires_at
        datetime revoked_at
        string revoked_reason
    }
    TALLY_PASSWORD_RESET {
        int id PK
        int customer_id FK
        string token_hash UK
        datetime expires_at
        datetime used_at
        int attempts
    }
    TALLY_USAGE_EVENT {
        int id PK
        int customer_id FK
        uuid device_id FK
        string kind "open or login"
        datetime occurred_at
        date day "IST calendar day"
    }
    TALLY_USAGE_RULE {
        int id PK "single row, pk=1"
        int new_user_days "7"
        int speed_window_days "30"
        int speed_min_active_days "15"
    }
    TALLY_AUDIT_LOG {
        int id PK
        int product_id FK
        int customer_id FK
        string action
        string actor
        string ip_address
    }
    TALLY_ADMIN_PERMISSION {
        int id PK
        int user_id FK
        int product_id FK
        bool can_view
        bool can_license_activation
        bool can_license_management
        bool can_device_management
        bool can_reports
    }
```

### 4.5 Endpoint map (GSTR2Tally)

| Caller | Endpoints (under `/api/v1/tally-sl/<product>/` unless noted) |
|---|---|
| Product client (`X-Product-Key` only) | `identify/`, `register/`, `auth/login/`, `auth/forgot-password/`, `auth/reset-password/`, `auth/verify-otp/`, `auth/resend-credentials/`, `activate/request-key/`, `activate/verify/`, `activate/confirm/` |
| Product client + bearer token | `auth/refresh/`, `auth/logout/`, `auth/change-password/`, `session/`, `account/`, `license/status/`, `tally-info/`, `usage/open/`, `events/device-transfer/` |
| Staff (session + per-product permission), under `/api/v1/tally-sl-admin/` | `access/`, `products/`, `permissions/`, `dashboard/`, `notifications/`, `products/<p>/{customers, transfers}`, `customers/<id>/{credentials/<action>, license/<action>, device/revoke}`, `usage/<category>/`, `reports/{license-expiry, user-performance, tally-details}` (+ `/export/`) |

Management commands: `create_tally_product_key`, `import_gstr_customers`.

---

## 5. Mothys Inventory (Universal Integration)

Project code `MOTHYS` (seeded by migration `integrations/0002_seed_mothys`). App: `backend/apps/integrations`. Detailed contract: `docs/MOTHYS-INTEGRATION-HANDOFF.md`, `docs/UNIVERSAL-LICENSING-API-REFERENCE.md`.

**A new project is data, not code:** Super Admin creates an `IntegrationProject` row (URLs, serial format, trial days, device limit, transfer policy, email modes, usage rules), Nexus issues a `client_id` + one-time secret, and the project's backend calls the 12 universal APIs.

### 5.1 Business workflow

```mermaid
flowchart TD
    A["Mothys customer signs up<br/>in the Mothys app"] --> B["Mothys BACKEND calls<br/>POST /customers/register"]
    B --> C["Nexus creates customer<br/>Serial = User ID (9 digits, starts 9)<br/>temporary password hashed"]
    C --> D{"Licence type requested"}
    D -- "Trial (default)" --> E["Licence ACTIVE immediately<br/>trial days = project setting (default 30, Mothys 3)"]
    D -- "1 Year / Lifetime" --> F["PENDING_APPROVAL<br/>Super Admin must approve"]
    F --> E
    E --> G["Welcome email:<br/>Serial, User ID, temp password, type, expiry, login URL<br/>(no activation key)"]
    G --> H["Customer logs in to Mothys<br/>Mothys backend calls POST /auth/verify"]
    H --> I["Customer must change password<br/>POST /auth/change-password"]
    I --> J["Activate User screen: User ID + registered email"]
    J --> K["POST /activation/request<br/>9-digit key emailed (15 min, 3 per hour)<br/>wrong email: rejected, nothing sent"]
    K --> L["POST /activation/verify<br/>key + device_id + device_name"]
    L --> M{"Licence active on<br/>another device?"}
    M -- No --> N["Session created, key consumed"]
    M -- Yes --> O["409 TRANSFER_CONFIRMATION_REQUIRED<br/>'License Already Active on Another Device'"]
    O --> P["Customer chooses OK or Cancel<br/>POST /devices/transfer/confirm"]
    P -- OK --> Q["Old sessions revoked (device_transfer)<br/>new session created"]
    P -- Cancel --> R["Previous device stays authorised"]
    N --> S["Mothys uses POST /sessions/introspect on each request<br/>POST /usage/events, POST /sessions/logout"]
    Q --> S
    S --> T["Super Admin: dashboard, customers, reports,<br/>approve / extend / cancel / renew / suspend"]
```

No Super Admin step and no extra email OTP is needed for a **device change** here (`transfer_policy = SELF`); a project can set `DISABLED` to forbid transfers.

### 5.2 Technical flow – the 12 universal APIs

```mermaid
sequenceDiagram
    autonumber
    actor U as Mothys user
    participant M as Mothys FastAPI backend
    participant N as Nexus /api/integrations/v1
    participant DB as SQL Server

    Note over M,N: every call: X-Client-Id + X-Client-Secret, HTTPS, 300 req/min
    N->>DB: authenticate_client (hash compare), project from credential
    N->>N: project_code in body must equal the credential's project, else 403 PROJECT_MISMATCH

    U->>M: sign up
    M->>N: POST customers/register (Idempotency-Key)
    N->>DB: idem_lookup (same key + same body hash returns stored response)
    N->>DB: INSERT IntegrationCustomer + IntegrationLicense, generate serial and temp password
    N-->>U: welcome email
    N-->>M: ok data {user_id, serial_number, license}
    N->>DB: IntegrationSyncLog (OK or FAILED, retry-able by admin)

    U->>M: login
    M->>N: POST auth/verify {user_id, password}
    N->>DB: check hash, lockout, licence state
    N-->>M: ok + license payload + must_change_password
    M->>N: POST licenses/validate

    U->>M: Activate User
    M->>N: POST activation/request {user_id, email}
    N->>DB: INSERT IntegrationActivationKey (hash, +15 min), email sent
    U->>M: enters key
    M->>N: POST activation/verify {user_id, password, activation_key, device_id, device_name}
    N->>DB: BEGIN lock licence and key, check attempts under 5
    alt another device holds the licence
        N->>DB: store transfer_hash + pending device on the key (10 min)
        N-->>M: 409 TRANSFER_CONFIRMATION_REQUIRED + transfer_token
        M->>N: POST devices/transfer/confirm {transfer_token, confirm}
        N->>DB: revoke all ACTIVE sessions, create new session, key used_at
    else free
        N->>DB: INSERT IntegrationSession (token hash), key used_at, usage DEVICE_ACTIVATION
    end
    N-->>M: session_token (returned once)

    loop each request from the app
        M->>N: POST sessions/introspect {session_token}
        N-->>M: active true or false + reason (revoked, license_expired...)
        M->>N: POST usage/events {events[1..100]} (event_id makes retries idempotent)
    end
    M->>N: POST sessions/logout
```

**Integration verification gate:** a project only becomes `ACTIVE` once it has really exercised `register`, `auth_verify`, `activation_verify`, `session_introspect`, `usage_event` (`IntegrationProject.REQUIRED_STEPS`, recorded in `verified_steps`).

**Licence states:** `PENDING_APPROVAL` · `ACTIVE` · `EXPIRED` (derived) · `SUSPENDED` · `CANCELLED`. Status shown to staff: pending, not_activated (valid licence, no device ever activated), active, expired, cancelled, inactive.

**Usage classes** (`integrations/usage.classify`, first match wins, non-overlapping): New (created ≤ `new_days`, default 7) → Not Used (no activity ever) → One-time (1 distinct day ever) → Speed (≥ `speed_min_days` = 15 distinct days in last 30) → Normal. Activity = `APP_OPEN`, `LOGIN`, `HEARTBEAT`, `DEVICE_ACTIVATION` (logout and transfer are not usage).

### 5.3 ERD – Mothys / universal integrations

```mermaid
erDiagram
    INTEGRATION_PROJECT ||--o{ INTEGRATION_CREDENTIAL : "client credentials"
    INTEGRATION_PROJECT ||--o{ INTEGRATION_CUSTOMER : "customers (PROTECT)"
    INTEGRATION_PROJECT ||--o{ INTEGRATION_IDEMPOTENCY : "replay protection"
    INTEGRATION_PROJECT |o--o{ INTEGRATION_AUDIT : "audit"
    INTEGRATION_PROJECT ||--o{ INTEGRATION_SYNC_LOG : "sync log"
    INTEGRATION_PROJECT ||--o{ INTEGRATION_USAGE_EVENT : "usage"
    INTEGRATION_CUSTOMER ||--|| INTEGRATION_LICENSE : "one licence"
    INTEGRATION_LICENSE ||--o{ INTEGRATION_ACTIVATION_KEY : "keys"
    INTEGRATION_LICENSE ||--o{ INTEGRATION_SESSION : "sessions (devices)"
    INTEGRATION_LICENSE ||--o{ INTEGRATION_USAGE_EVENT : "usage"
    INTEGRATION_SESSION |o--o{ INTEGRATION_USAGE_EVENT : "during session"

    INTEGRATION_PROJECT {
        int id PK
        string code UK "MOTHYS"
        string name
        string frontend_url
        string backend_url
        string auth_method "JWT Bearer"
        bool tally_serial_required "false for Mothys"
        string serial_prefix "9"
        int serial_length "9"
        int activation_key_length "9"
        int device_limit "1"
        string default_license_type
        int trial_days
        string transfer_policy "SELF or DISABLED"
        bool usage_tracking
        text usage_rules "JSON new_days, speed_min_days"
        string status "SETUP_PENDING, ACTIVE, DISABLED"
        text verified_steps "JSON of exercised API steps"
        string last_sync_status
    }
    INTEGRATION_CREDENTIAL {
        int id PK
        int project_id FK
        string client_id UK
        string secret_hash "SHA-256 of secret"
        string secret_hint
        bool active
        datetime revoked_at
    }
    INTEGRATION_CUSTOMER {
        int id PK
        int project_id FK
        string external_customer_id "UK with project"
        string name
        string email "UK with project"
        string contact_number
        string serial_number UK
        string user_id UK "= serial"
        string password_hash
        bool must_change_password
        int failed_logins
        datetime locked_until
        bool active
        string welcome_email_status
    }
    INTEGRATION_LICENSE {
        int id PK
        int customer_id FK "one-to-one"
        string license_type "TRIAL, ONE_YEAR, LIFETIME"
        string status "PENDING_APPROVAL, ACTIVE, SUSPENDED, CANCELLED"
        datetime starts_at
        datetime expires_at "null = lifetime or not approved"
    }
    INTEGRATION_ACTIVATION_KEY {
        int id PK
        int license_id FK
        string key_hash
        datetime expires_at "+15 min"
        datetime used_at
        int attempts "max 5"
        string transfer_hash "set while awaiting confirmation"
        string pending_device_id
        string pending_device_name
        datetime pending_expires_at "+10 min"
    }
    INTEGRATION_SESSION {
        int id PK
        int license_id FK
        string token_hash UK
        string device_id
        string device_name
        string status "ACTIVE or REVOKED"
        string revoked_reason
        datetime last_seen_at
    }
    INTEGRATION_USAGE_EVENT {
        int id PK
        int project_id FK
        int license_id FK
        int session_id FK
        string device_id
        string event_type "APP_OPEN, LOGIN, HEARTBEAT, DEVICE_ACTIVATION, LOGOUT, DEVICE_TRANSFER"
        datetime occurred_at
        string event_id "UK with project"
    }
    INTEGRATION_IDEMPOTENCY {
        int id PK
        int project_id FK
        string endpoint
        string key "UK with project+endpoint"
        string request_hash
        int status_code
        text response "never holds a secret"
    }
    INTEGRATION_AUDIT {
        int id PK
        int project_id FK
        string action
        string subject
        bool ok
        string actor
        string ip
    }
    INTEGRATION_SYNC_LOG {
        int id PK
        int project_id FK
        string external_customer_id
        string event
        string status "OK, FAILED, RETRIED"
        string error_code
        text payload "no credentials"
        int retries
    }
```

### 5.4 Endpoint map (Mothys / universal)

| Caller | Endpoints (under `/api/integrations/`) |
|---|---|
| Project backend (`X-Client-Id` + `X-Client-Secret`) | `v1/customers/{register, update, import}`, `v1/auth/{verify, change-password}`, `v1/licenses/validate`, `v1/activation/{request, verify}`, `v1/devices/transfer/confirm`, `v1/sessions/{introspect, logout}`, `v1/usage/events`, `v1/projects/<code>/config` |
| Super Admin (session) | `admin/dashboard/`, `admin/projects/…` (create, detail, rotate-credentials, test-connection, docs/<kind>, customers, audit), `admin/projects/<code>/{dashboard, product-customers, product-customers/<id>/<action>, reports/<kind>, export}`, `admin/licenses/<id>/approve/`, `admin/customers/<id>/resend-welcome/`, `admin/failed-sync/` + `/<id>/retry/` |

---

## 6. Dashboard and reports

`backend/apps/dashboard/usage.py` reduces every project to **one row per registered customer/device with its latest successful usage time**. Used and Not Used are filters over that single set, so the summary always equals the lists and nothing is counted twice.

```mermaid
flowchart LR
    subgraph Sources
        NX["Nexus tables<br/>(ProjectCustomer, DeviceRegistration,<br/>Tally*, Integration*)"]
        GS["Gstr_db (read-only)<br/>licensed_device_tbl,<br/>tally_import_job_tbl (COMPLETED jobs)"]
        PR["PRNPRINT (legacy, read-only,<br/>optional and verified)"]
    end
    NX --> BASE["base set: ONE row per customer/device<br/>+ last_used"]
    GS --> BASE
    PR --> BASE
    BASE --> SUM["Summary tiles<br/>Used / Not Used / Expiring this month / Expired"]
    BASE --> LST["Category lists<br/>paged 10/25/50/100, searchable"]
    SUM --> UI["Super Admin dashboard"]
    LST --> UI
```

| Project | "Registered" means | "Used" means |
|---|---|---|
| GSTR2Tally | ACTIVE devices in `licensed_device_tbl` (+ unmatched ACTIVE Nexus registrations) | a `COMPLETED` import job attributed to the device |
| Barcode AutoPrint | ACTIVE-status Nexus customers (authoritative), merged by serial with legacy PRN rows only when that DB is configured, readable and verified | licence activated/validated, or the enrolled device really reported use (never mere record creation) |
| Mothys / universal | customers with licences | any activity event (see 5.2) |

**Expiring This Month** uses `expires_at` in the current calendar month, Asia/Kolkata, for ACTIVE Trial and 1-Year customers only (Lifetime excluded).

**Reports** (all with Export): License Expiry (all products), Usage, User Performance, Tally Details (GSTR), Customer Details (Mothys). The dashboard polls `GET /api/dashboard/sync/` and per-project `…/sync/` – a cheap version fingerprint (newest audit id + customer count) – to refresh open screens.

---

## 7. Frontend map

React Router v6 in `frontend/src/App.jsx`. Public: `/login`. Everything else is behind `ProtectedRoute` inside `AppLayout`; menu items come from `layouts/navigation.js` and the `/auth/me` permissions.

| Area | Routes | API client |
|---|---|---|
| Overall | `/dashboard` | `api/dashboard.js` |
| Barcode (generic project pages) | `/projects/:slug/{add, customers, customers/:id/edit}` · `/reports/license-expiry` · `/reports/usage` | `api/registrations.js`, `api/reports.js` |
| GSTR2Tally | `/tally/:product/{dashboard, customers, customers/:id, transfers, usage/:category}` · `/tally/reports/{license-expiry, tally-details, user-performance}` | `api/tallysl.js`, `context/TallyAccessContext.jsx` |
| Mothys / universal | `/product/:code/{dashboard, customers, customers/:id}` · `/mothys/reports/{license-expiry, customer-details, user-performance}` | `api/integrations.js` |
| Project management (Super Admin, flag-gated) | `/project-management`, `/add`, `/status`, `/:code/integration` | `api/integrations.js` |
| Components worth knowing | `DeviceManagement`, `DeviceTransferRequests`, `RegistrationRequests`, `LicenseActions`, `ReissueActivationKey`, `ResendCredentials`, `ApprovalGate`, `ConfigurationDrawer` | |

---

## 8. Side-by-side comparison

| Topic | Barcode AutoPrint | GSTR2Tally | Mothys / universal |
|---|---|---|---|
| Django app | `registrations` | `tallysl` | `integrations` |
| Customer table | `ProjectCustomer` (project = string) | `TallyCustomer` (FK product) | `IntegrationCustomer` (FK project) |
| User ID | Nexus-generated serial (9 digits, starts 7) | **Tally serial from TallyPrime** | Nexus-generated serial (9 digits, starts 9) |
| Sign-up creates | A *request*; staff approve to create customer | Customer + **PENDING** licence immediately | Customer immediately; Trial ACTIVE, 1Y/Lifetime PENDING_APPROVAL |
| Who activates licence | Super Admin/Admin on approval | Super Admin (or "License Activation" permission) | Automatic for Trial; Super Admin approves others |
| Activation key | Issued at approval, emailed with credentials; Serial + Key at first run | Emailed on **customer request** (15 min, single use) | Emailed on customer request (9 digits, 15 min, 3/hr, 5 tries) |
| Trial length | 7 days (fixed per product) | 3 days default, set per activation (1–365) | Project setting (Mothys 3, default 30) |
| Device proof | Ed25519 signature + HMAC hardware hashes | Client device id + keyed fingerprint hash | `device_id` string from the project |
| New PC allowed by | **Super Admin approval** (first activation and any transfer) | Customer confirms after key | Customer confirms (policy SELF) |
| Where licence is enforced | On the PC from signed entitlement (7-day lease, hourly refresh) | Nexus session + status calls | Nexus session introspection |
| Caller credential | Device keypair (+ `ProjectClient` key for portal) | `X-Product-Key` | `X-Client-Id` + `X-Client-Secret` |
| Usage source | `DeviceRegistration.last_used_at / use_count` + legacy | `TallyUsageEvent` + `Gstr_db` jobs | `IntegrationUsageEvent` |
| Staff permission model | `UserModulePermission` | `TallyAdminPermission` | Super Admin only |
| Audit table | `LicenseAuditLog` | `TallyAuditLog` | `IntegrationAudit` + `IntegrationSyncLog` |

---

## 9. Combined ERD

High-level only (full columns are in sections 2.4, 3.6, 4.4 and 5.3). Dotted lines are **logical** links (matched by code or serial, no foreign key).

```mermaid
erDiagram
    AUTH_USER ||--o| USER_PROFILE : role
    AUTH_USER ||--o{ USER_MODULE_PERMISSION : grants
    AUTH_USER ||--o{ TALLY_ADMIN_PERMISSION : grants

    %% Barcode AutoPrint
    PROJECT_CUSTOMER ||--o{ DEVICE_REGISTRATION : devices
    PROJECT_CUSTOMER ||--o{ DEVICE_TRANSFER_REQUEST : requests
    PROJECT_CUSTOMER ||--o{ LICENSE_RENEWAL : renewals
    PROJECT_CUSTOMER |o--o{ REGISTRATION_REQUEST : "from sign-up"
    DEVICE_REGISTRATION ||--o{ DEVICE_NONCE : nonces
    AUTH_USER |o--o{ LICENSE_AUDIT_LOG : actor
    PROJECT_CUSTOMER |o--o{ LICENSE_AUDIT_LOG : about

    %% GSTR2Tally
    TALLY_PRODUCT ||--o{ TALLY_CUSTOMER : customers
    TALLY_PRODUCT ||--o{ TALLY_PRODUCT_KEY : keys
    TALLY_PRODUCT ||--o{ TALLY_ADMIN_PERMISSION : scoped
    TALLY_CUSTOMER ||--|| TALLY_LICENSE : licence
    TALLY_CUSTOMER ||--o| TALLY_INFO : "tally details"
    TALLY_CUSTOMER ||--o{ TALLY_DEVICE : devices
    TALLY_CUSTOMER ||--o{ TALLY_SESSION : sessions
    TALLY_CUSTOMER ||--o{ TALLY_USAGE_EVENT : usage
    TALLY_CUSTOMER ||--o{ TALLY_TRANSFER_EVENT : "transfer notices"
    TALLY_CUSTOMER ||--o{ TALLY_VERIFICATION_CODE : keys
    TALLY_CUSTOMER ||--o{ TALLY_PASSWORD_RESET : resets

    %% Mothys / universal
    INTEGRATION_PROJECT ||--o{ INTEGRATION_CREDENTIAL : credentials
    INTEGRATION_PROJECT ||--o{ INTEGRATION_CUSTOMER : customers
    INTEGRATION_CUSTOMER ||--|| INTEGRATION_LICENSE : licence
    INTEGRATION_LICENSE ||--o{ INTEGRATION_ACTIVATION_KEY : keys
    INTEGRATION_LICENSE ||--o{ INTEGRATION_SESSION : sessions
    INTEGRATION_LICENSE ||--o{ INTEGRATION_USAGE_EVENT : usage

    %% Cross-family logical links
    PROJECT_CUSTOMER }o..o{ TALLY_CUSTOMER : "dashboard merges by serial (no FK)"
    PROJECT_CUSTOMER }o..o{ INTEGRATION_CUSTOMER : "none, isolated by design"
```

---

## 10. Known gaps and things to verify

These come from the repository itself, not from guesses:

1. **Mothys handoff status** (`docs/MOTHYS-INTEGRATION-HANDOFF.md`): all 12 APIs passed the automated run on a local SQL Server, but the **Mothys FastAPI side was not built**, real SMTP delivery of the welcome email, browser checks of the Nexus screens, and production deployment were **not verified**.
2. **GSTR2Tally deployment** (`docs/TALLY-SL-LICENSING.md`): at the time that document was written the Tally SL v1 API was **local only**; the live server returned 404 for `identify/`. Re-check production before relying on it.
3. **Project Management menu** (`App.jsx`): routes are present but flag-gated, and the handoff notes describe it as on hold. Treat as not part of the live workflow until enabled.
4. **Email "accepted" ≠ delivered.** SMTP gives no receipt. When a mail server did not accept the credentials email, Nexus returns the temporary password and key once so staff can hand them over.
5. **PRN legacy database** (`PRNPRINT`) is optional. If it is unreadable the dashboard falls back to Nexus-only counts and says it is incomplete (`dashboard/usage.legacy_state`).
6. **Changing `DEVICE_EVIDENCE_KEY`** (defaults to `DJANGO_SECRET_KEY`) makes every stored PRN hardware hash unmatchable, so every device would need re-enrolment.
7. Working tree currently has **uncommitted edits** to the `integrations` module and docs (see `git status`); this document describes the code as it is on disk now.

### Source index

| Topic | Files |
|---|---|
| PRN models / logic | `backend/apps/registrations/{models,services,device,device_crypto,portal,device_views,portal_views,views}.py` |
| GSTR models / logic | `backend/apps/tallysl/{models,services,client_views,admin_views,events}.py` |
| Universal models / logic | `backend/apps/integrations/{models,services,views,admin_views,product_views,usage,spec}.py` |
| Staff auth / roles | `backend/apps/accounts/{models,constants,permissions,views}.py` |
| Dashboard | `backend/apps/dashboard/{usage,views}.py` |
| Settings | `backend/config/settings.py` (DB, sessions, throttles, licence keys, lockout) |
| URL maps | `backend/config/urls.py` and each app's `urls.py` |
| Frontend | `frontend/src/App.jsx`, `layouts/navigation.js`, `api/*.js`, `pages/{tally,product,integrations}/` |
| Existing detail docs | `docs/DEVICE-LICENSING.md`, `docs/TALLY-SL-API-CONTRACT.md`, `docs/TALLY-SL-LICENSING.md`, `docs/MOTHYS-INTEGRATION-HANDOFF.md`, `docs/UNIVERSAL-LICENSING-API-REFERENCE.md` |
