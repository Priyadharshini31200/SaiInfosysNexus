# Nexus production deployment and the HTTP 502 checklist

A **502 Bad Gateway** is never produced by Django. It is the reverse proxy (IIS ARR / nginx) saying *"I could not get a valid answer from the
Nexus process"*. Local development (`runserver`, Vite proxy) was verified to log in with HTTP 200; the 502 can only come from the production
chain below, so work through it in this order. `GET /api/health/` is the tool for this: it separates the three layers.

| `curl https://<host>/api/health/` returns | Meaning | Fix |
|---|---|---|
| `{"status":"ok","database":"ok"}` 200 | Proxy, Django and SQL Server are all fine; login problems are credentials/CSRF, not 502 | see "Login errors" below |
| `{"status":"unavailable",...}` **503 JSON** | Django is up, **SQL Server is not reachable** | section 2 |
| `502` (HTML from the proxy) | The proxy cannot reach Django at all | sections 1, 3, 4 |
| `301/302` loop | HTTPS redirect loop | section 4 |

## 1. The application process must be running (most common 502 cause)
`manage.py runserver` is a development server: it stops when the console closes and must never serve production. Run Nexus under a real
WSGI server as a Windows service:

```powershell
cd D:\SaiInfosysNexus\backend
.\.venv\Scripts\pip install waitress
# test in a console first:
.\.venv\Scripts\waitress-serve --listen=127.0.0.1:8001 config.wsgi:application
# then register it as a service that restarts on failure (e.g. NSSM):
nssm install SaiNexus D:\SaiInfosysNexus\backend\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8001 config.wsgi:application
nssm set SaiNexus AppDirectory D:\SaiInfosysNexus\backend
nssm set SaiNexus AppExit Default Restart
```
Bind to `127.0.0.1` and put **only** the proxy on the public interface. The proxy's upstream port must equal this port.

## 2. SQL Server connectivity (the second most common cause)
- The Windows service account must be allowed to log in. `DB_TRUSTED_CONNECTION=True` uses the *service's* Windows identity (not yours): grant that
  account access (`CREATE LOGIN [DOMAIN\svc] FROM WINDOWS; ALTER ROLE db_owner ADD MEMBER ...` in `SaiInfosysNexus`), **or** use SQL authentication
  (`DB_TRUSTED_CONNECTION=False`, `DB_USER`, `DB_PASSWORD`).
- SQL Server Browser must run for a named instance (`.\SQLEXPRESS`), TCP/IP must be enabled, or set `DB_HOST=host` + `DB_PORT=1433`.
- `ODBC_DRIVER` must match an installed driver (`Get-OdbcDriver`).
- A production certificate: set `DB_TRUST_SERVER_CERTIFICATE=False` once the server has a trusted certificate.
- `DB_CONNECT_TIMEOUT` (default 15 s) makes an unreachable database fail fast: the API then answers **503 JSON** `database_unavailable` instead of hanging
  until the proxy times out with 502/504.

## 3. Proxy configuration
**nginx**
```nginx
server {
    listen 443 ssl;
    server_name nexus.example.com;
    root D:/SaiInfosysNexus/frontend/dist;          # the built React app
    location /api/ {
        proxy_pass http://127.0.0.1:8001;            # must match section 1
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;  # the proxy sets this itself; never pass the client's value
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 60s;
    }
    location / { try_files $uri /index.html; }       # SPA routes
}
```
**IIS (URL Rewrite + ARR):** rule `^api/(.*)` -> `http://127.0.0.1:8001/api/{R:1}`; enable *Preserve host header*; in *Server Variables* allow and set
`HTTP_X_FORWARDED_PROTO = https`. Static files + `index.html` fallback for everything else.

The browser must only talk to **one origin** (the proxy): `/api/` is relative in the SPA (`API_BASE = '/api'`), so the session/CSRF cookies are first-party and
no CORS is involved.

## 4. Django production environment (`backend/.env`)
```
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<long random value>
DJANGO_ALLOWED_HOSTS=nexus.example.com                       # a wrong host -> 400, which some proxies report as 502
DJANGO_CSRF_TRUSTED_ORIGINS=https://nexus.example.com        # exact scheme + host
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_BEHIND_PROXY=True      # trust X-Forwarded-Proto/Host from the proxy; REQUIRED with TLS termination, or SECURE_SSL_REDIRECT loops
DJANGO_SECURE_SSL_REDIRECT=True
```
Only set `DJANGO_BEHIND_PROXY=True` if the proxy overwrites `X-Forwarded-Proto` (as above) and Django is reachable only through it.
Then: `python manage.py migrate`, `python manage.py check --deploy`, `python manage.py seed_initial_users --allow-production` once, **change both
bootstrap passwords at first sign-in**.

## 5. CORS
Nexus's own UI is same-origin: no CORS. The server-to-server project APIs (`/api/portal/`, `/api/device/`) are called by project backends and desktop apps,
which do not use CORS. If a *browser* app on another origin must call them, list its exact origin(s):
`DJANGO_CORS_ALLOWED_ORIGINS=https://app.example.com`. Only those two prefixes ever get CORS headers; staff endpoints never do; credentials are never allowed.

## 6. Email (SMTP)
```
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=<mailbox>
EMAIL_HOST_PASSWORD=<app password>
EMAIL_USE_TLS=True            # 587 STARTTLS;  for port 465 set EMAIL_USE_SSL=True instead
DEFAULT_FROM_EMAIL=SAI INFOSYS <no-reply@your-domain>   # must be an address the SMTP account may send as
MAIL_SEND_ATTEMPTS=3          # credential emails are retried with 2 s, 4 s back-off
```
With `DJANGO_DEBUG=False` and no `EMAIL_HOST`, sends fail and are reported (never silently dropped). Every attempt is recorded in the `EmailLog` table
(`registrations_emaillog`: recipient, kind, status `accepted|not_delivered|failed`, attempts, error text; never the body). `accepted` means the SMTP server took
the message; SMTP gives no delivery receipt. Check recent failures:
```sql
SELECT TOP 50 created_at, kind, recipient, status, attempts, error FROM registrations_emaillog ORDER BY id DESC;
```
When a credentials email fails, the customer is still saved and the Super Admin screen shows the temporary password once so it can be passed on another way.

## Login errors (when health is OK)
| Message | Cause |
|---|---|
| Invalid username or password. | wrong credentials (staff and customers are separate tables) |
| 403 CSRF / "Could not start a secure session" | `DJANGO_CSRF_TRUSTED_ORIGINS` missing the https origin, or `DJANGO_BEHIND_PROXY` not set |
| Too many attempts (429) | login throttle (`LOGIN_THROTTLE_RATE`, 10/min) |
| "The Nexus server is temporarily unavailable" | the SPA saw 502/503/504 from the proxy; use the table at the top |
