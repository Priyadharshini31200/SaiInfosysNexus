# PENDING TASK – Mothys Inventory app: implement Nexus device activation

**Owner:** Mothys Inventory application team (no Nexus change required)
**Status:** Pending
**Symptom:** Mothys customers register correctly and appear in Nexus, but their status stays **Not Activated**.

## Why

The Nexus Mothys customer list shows a licence as **Not Activated** when the licence is valid but no device session exists.
For Mothys (a legacy project) a device session is created **only** by `POST /activation/verify`.
`POST /auth/verify` proves the password but never creates a session, so a Mothys app that only registers customers and
calls `auth/verify` leaves every customer as Not Activated, even while they use the app.

`POST /devices/authorize` is **not** for Mothys: Nexus answers `404 NOT_AVAILABLE` for legacy projects.

## Required Mothys application changes

Base URL: `https://saitallyexpert.in/nexus/api/integrations/v1/`. Every call is made from the Mothys backend (never the browser) with the `X-Client-Id` and `X-Client-Secret` headers.

1. **Login:** call `auth/verify` with `user_id`, `password`, `device_id`.
   - `device_authorized: true` means the device already holds the licence: issue the JWT for the stored session.
   - Otherwise, send the user to the Activate User screen.
2. **Activate User screen, "Send Activation Key":** call `activation/request` with `user_id` and `email`. Nexus emails a 9-digit key, valid for 15 minutes.
3. **Activate User screen, "Verify & Activate":** call `activation/verify` with `user_id`, `email`, `activation_key`, `device_id`, `device_name`.
   - **200:** the session is created and Nexus shows the customer as **Active**. Put `session_token` into the Mothys JWT (`nsid`).
   - **409 `TRANSFER_CONFIRMATION_REQUIRED`:** show OK / Cancel, then call `devices/transfer/confirm` with `confirm: true` or `false`.
4. **Enforcement:** on each request or start-up, call `sessions/introspect`; `active: false` means log the user out.
5. **Logout:** call `sessions/logout`.
6. **Usage:** send `usage/events` (APP_OPEN, LOGIN, HEARTBEAT, LOGOUT). Don't block the user on these calls.

Full request and response examples: `docs/MOTHYS-INTEGRATION-HANDOFF.md`, section 6.

## Acceptance

For an approved Mothys customer, once `activation/verify` returns 200 the Nexus status changes from **Not Activated** to **Active**. Production already serves these routes; verified on 2026-11-21: all answer 401 without credentials.
