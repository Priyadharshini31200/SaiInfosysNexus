# Universal Licensing API – Deployment and Rollback

Scope: new Django app `apps.integrations` plus additive UI. PRN / GSTR / Tally SL code, tables, keys, SMTP and env vars are **not modified**.

## What changed
Backend: `backend/apps/integrations/` (new), one line each in `config/settings.py` (INSTALLED_APPS) and `config/urls.py`.
Frontend: new `api/integrations.js`, `pages/integrations/*`, `components/UniversalProjectsSection.jsx`, `layouts/ProjectManagementMenu.jsx`; small additive edits in `App.jsx` (routes), `Sidebar.jsx`, `navigation.js` (title), `OverallDashboard.jsx` (renders the new section after the existing content).

## Database (additive migration `integrations.0001_initial`)
Creates only: `integrations_integrationproject`, `_credential`, `_customer`, `_license`, `_activationkey`, `_session`, `_idempotency`, `_audit`, `_usageevent`, `_synclog`. No existing table is altered. Unique constraints: customer (project, external id), (project, email), serial number, user id; usage event (project, event id); idempotency (project, endpoint, key).

## Deploy
1. Back up the database. 2. Pull the code. 3. `python manage.py migrate integrations` (only this app). 4. Build the frontend (`npm run build`) and publish `dist`. 5. Restart the Nexus service. 6. Sign in as Super Admin → Project Management → Add / Connect Project (Mothys values in the task). 7. Copy the API secret (shown once) to the Mothys team via a secure channel.
Optional settings (defaults shown): `INTEGRATION_RATE_LIMIT_PER_MIN=300`, `INTEGRATION_ACTIVATION_KEYS_PER_HOUR=3`, `INTEGRATION_ACTIVATION_KEY_MINUTES=15`. Existing SMTP settings are reused unchanged. Set `NEXUS_PUBLIC_API_BASE=https://<host>/nexus/api` (recommended) or make the proxy forward `X-Forwarded-Proto` and `X-Forwarded-Prefix: /nexus` so generated documentation shows the public URL; serve over HTTPS only.
**Multi-worker production:** rate limits use Django's cache, which is per process by default. Set `CACHE_DB_TABLE=nexus_cache` in the environment and run `python manage.py createcachetable` once; all workers then share the counters. Without it each worker counts separately (limits are effectively multiplied by the worker count).

## Rollback
* Soft: disable the integration per project (Project → Disable) – the API answers `INTEGRATION_DISABLED`; PRN/GSTR are unaffected.
* Code: redeploy the previous build (the new routes simply disappear; the sidebar item and dashboard section vanish).
* Data (only if required): `python manage.py migrate integrations zero` drops only the new tables (this deletes Mothys customers/licences created through the API – export first).
