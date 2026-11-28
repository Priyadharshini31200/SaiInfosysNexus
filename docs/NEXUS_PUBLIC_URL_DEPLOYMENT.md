# Nexus public URL routing and credential links - deployment handoff

## Root cause
`send-docs` built the credential link from the browser `Origin` header (the bare domain) because `NEXUS_PUBLIC_APP_URL` was never defined in settings,
giving `https://saiinfosysmdu.in/developer-credentials?token=...` (Nginx 404). The SPA is built with Vite `base: '/nexus/'` and React Router
`basename = BASE_URL`, so its real route is `/nexus/developer-credentials`.

## Code change
- `backend/config/settings.py`: `NEXUS_PUBLIC_FRONTEND_URL` (falls back to legacy `NEXUS_PUBLIC_APP_URL`) and `NEXUS_FRONTEND_BASE_PATH` (default `/nexus`).
- `backend/apps/integrations/services.py`: `public_frontend_url()` / `developer_credentials_link()` - adds `/nexus` only when the address has no path, collapses `//`, never doubles `/nexus/nexus`, never uses the API base, returns "" for non-http(s).
- `admin_views.py` (send-docs) and `universe.py` (customer setup email) use it. Token payload/format unchanged.
- `frontend/src/pages/DeveloperCredentialsPage.jsx`: `Referrer: no-referrer` + `noindex` while the page is open (token not leaked to referrers). The page only POSTs on the Show Credentials click, so a landing/prefetch never consumes the token.
- Tests: `apps/integrations/test_public_url.py` (new), plus the existing handoff tests (single use, expired, used, invalid, project isolation) now also assert the `/nexus/` link.

Generated link: `https://saiinfosysmdu.in/nexus/developer-credentials?token=<token>`

## Required server setting (production .env of nexus.service)
```
NEXUS_PUBLIC_FRONTEND_URL=https://saiinfosysmdu.in/nexus/
```
(Even without it, the `/nexus` base path is now added to the Origin.) Restart `nexus.service` after editing.

## Nginx
The Nginx files are NOT in this repository (no `deploy/` folder), so they were not inspected or edited. On the VPS find the HTTPS server block for
saiinfosysmdu.in (`grep -rl "location ^~ /nexus/" /etc/nginx/`). Confirm separately that `/api/integrations/v1/` has its own proxy to 127.0.0.1:8004
(`/nexus/api/` and `/api/integrations/v1/` are different public routes; the Django routes live under `/api/`). Only add this exact-path redirect for older mails:
```
location = /developer-credentials {
    add_header Referrer-Policy "no-referrer" always;
    add_header Cache-Control "no-store" always;
    return 302 /nexus/developer-credentials$is_args$args;
}
```
Inside `location ^~ /nexus/` add `add_header Referrer-Policy "no-referrer" always;` only if acceptable for the whole SPA, otherwise leave it (the page sets it itself).
To keep tokens out of access logs, use a log format that logs `$uri` instead of `$request` / `$request_uri` for this vhost, or add
`access_log off;` inside the two credential locations. Do not add a root catch-all.

Backup / test / reload:
```
sudo cp -a /etc/nginx/sites-available/<file> /root/<file>.bak.$(date +%F)
sudo nginx -t && sudo systemctl reload nginx
```

## Git (nothing was pushed or merged)
```
git checkout nexus-web && git add -A backend frontend docs && git commit -m "Fix Nexus public frontend URL for developer credential links"
git push origin nexus-web
git checkout main && git pull && git merge --no-ff nexus-web && git push origin main
```
(`git status` shows stray files at the repo root such as `full.log`, `t3.log`, `al automatic ...`; do not `add -A` blindly - add paths explicitly.)

## VPS
```
cd /path/to/SaiInfosysNexus && git pull origin main
echo 'NEXUS_PUBLIC_FRONTEND_URL=https://saiinfosysmdu.in/nexus/' | sudo tee -a /path/to/backend/.env   # skip if present
sudo systemctl restart nexus.service
cd frontend && npm ci && npm run build            # emits dist/ with base /nexus/
REL=/var/www/nexus-releases/$(date +%Y%m%d%H%M); sudo mkdir -p $REL && sudo cp -a dist/. $REL/
sudo ln -sfn $REL /var/www/nexus-current            # point the Nginx /nexus/ alias/root at nexus-current
# rollback: sudo ln -sfn <previous release dir> /var/www/nexus-current
```
(Adjust paths to the real web root used by `location ^~ /nexus/`.)

## Browser verification
1. `https://saiinfosysmdu.in/nexus/developer-credentials?token=x` -> page renders; Show Credentials -> friendly "invalid" message; refresh works.
2. `https://saiinfosysmdu.in/developer-credentials?token=x` -> 302 to the `/nexus/` URL, token preserved (after the optional redirect).
3. Superadmin -> UNIVERSE -> GSTR001 -> API Integration -> Step 3 -> Send API Documentation; confirm the email link starts with `/nexus/`.
4. Open it once: Show Credentials reveals Client ID + a NEW API Secret; reopening the same link says it was already used.
5. `curl -i https://saiinfosysmdu.in/api/integrations/v1/projects/GSTR001/config` -> 401 JSON (route alive, no credentials).

## Exposed links
Any credential link already emailed with the wrong URL travelled through the email channel; treat unredeemed ones as exposed. After deploy, resend a fresh
link from Step 3 (it invalidates older links when redeemed). No secret was rotated by this change.
