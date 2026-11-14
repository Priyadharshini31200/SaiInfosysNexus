# Tally SL — production release checklist (prepared, NOT executed)

Nothing here has been run against production, and **no VPS access was available** while this was prepared (no SSH configuration or key on the
workstation). Only public, unauthenticated GETs were made. Anything marked `<…>` must be filled in from the read-only discovery in §2.
Branch `nexus-web`, HEAD `6f56628` (identical to `origin/nexus-web`; `origin/main` is at `6512fb0`, a merge of PR #7 from `nexus-web`).

## 0. What public evidence says about production (read-only GETs on 2026-11-14)
| Observation | Conclusion |
|---|---|
| `Server: nginx/1.24.0 (Ubuntu)`, HTTPS with HSTS | production is **Ubuntu Linux + nginx**; the Windows/NSSM/IIS commands in `docs/DEPLOYMENT.md` do **not** apply |
| `GET /nexus/api/health/` → 200 `{"status":"ok","database":"ok"}` | Django and SQL Server are up |
| `GET /nexus/api/portal/prn-auto-print/login/` → 401 `Invalid or missing project key` | PRN API reachable and behaving normally |
| `GET /nexus/api/v1/tally-sl/gstr2tally/identify/` → 404 (Django HTML page) | nginx already forwards `/nexus/api/…` (incl. `/v1/`) to Django; the Tally API is simply **not deployed** |
| `GET /nexus/` → 200 HTML | SPA served from nginx |

Not knowable from here: systemd unit name, Gunicorn working directory, deployed commit, DB host/name/backup location, pending migrations, `.env` content.

## 1. Local Git release (prepared, not executed)
**Branch:** `nexus-web` (work is on top of `6f56628`). **Commit contents (exactly these):**
```
M  backend/config/settings.py                     (+1: "apps.tallysl" in INSTALLED_APPS)
M  backend/config/urls.py                         (+1: include("apps.tallysl.urls"))
A  backend/apps/tallysl/{__init__,access,admin_views,apps,client_views,emails,models,services,urls}.py
A  backend/apps/tallysl/management/{__init__.py,commands/__init__.py,commands/create_tally_product_key.py}
A  backend/apps/tallysl/migrations/{__init__,0001_initial,0002_seed_gstr2tally}.py
A  backend/apps/tallysl/{tests.py,test_integration_flow.py}        (self-contained; no dependency on other test modules)
M  frontend/src/{App.jsx,main.jsx}
M  frontend/src/components/ConfigurationDrawer.jsx
M  frontend/src/layouts/{Sidebar,ProjectSidebarMenu,ReportsSidebarMenu}.jsx, navigation.js
A  frontend/src/api/tallysl.js, context/TallyAccessContext.jsx, layouts/TallySidebarMenu.jsx
A  frontend/src/pages/tally/{Tally.module.css,TallyShared,TallyDashboardPage,TallyCustomersPage,TallyCustomerDetailPage,TallyReportPage,TallyUsageListPage}.jsx
A  docs/TALLY-SL-LICENSING.md, docs/TALLY-SL-DEPLOYMENT.md
```
**Excluded on purpose:** `backend/.env` (ignored), `frontend/dist`, `node_modules`, `.venv` (ignored); `backend/nexus-8001.*.log`; the two stray root files
(`customer licensing and device transfer features…`, `tatusgit statusq`); the untracked PRN test modules (`apps/{accounts,dashboard,registrations}/test*.py`, `tests.py`);
`frontend/tests/`; other `docs/*`. No PRN source file is touched (`apps/registrations`, `apps/accounts`, `apps/dashboard`: zero changes). Secret scan of the release paths: clean.
```bash
cd /d/SaiInfosysNexus && git checkout nexus-web
git add backend/apps/tallysl backend/config/settings.py backend/config/urls.py \
        frontend/src/api/tallysl.js frontend/src/context/TallyAccessContext.jsx frontend/src/layouts/TallySidebarMenu.jsx frontend/src/pages/tally \
        frontend/src/App.jsx frontend/src/main.jsx frontend/src/components/ConfigurationDrawer.jsx \
        frontend/src/layouts/Sidebar.jsx frontend/src/layouts/ProjectSidebarMenu.jsx frontend/src/layouts/ReportsSidebarMenu.jsx frontend/src/layouts/navigation.js \
        docs/TALLY-SL-LICENSING.md docs/TALLY-SL-DEPLOYMENT.md
git status --short | grep -E '^[AM]'     # review: only the list above, nothing under apps/registrations|accounts|dashboard
git commit -m "Add Tally SL central licensing API (additive) and GSTR2Tally UI"
git tag tallysl-v1.0.0
git push origin nexus-web --tags          # then PR nexus-web -> main as usual
```
(Run only after approval. `__pycache__` is ignored by the repository's `.gitignore`; check `git status` shows none.)

## 2. Production read-only pre-flight (run on the VPS by someone with access; none of these change anything)
```bash
# --- identify OS / service / path / commit
cat /etc/os-release | head -2
systemctl list-unit-files --type=service | grep -Ei 'nexus|gunicorn'
sudo nginx -T 2>&1 | grep -nE 'server_name|root |location|proxy_pass' | head -30
find /var/www /srv /opt /home -maxdepth 5 -type d -name .git 2>/dev/null
# after you pick the Nexus unit (do not guess; do not touch other hosted projects):
UNIT=<nexus-unit>
systemctl show $UNIT -p WorkingDirectory -p ExecStart -p User -p EnvironmentFiles
APP=<WorkingDirectory>/..           # repo root that contains backend/ and frontend/
git -C $APP rev-parse --abbrev-ref HEAD; git -C $APP rev-parse HEAD; git -C $APP status --short | head
# --- health + PRN reachability
curl -s https://saitallyexpert.in/nexus/api/health/
curl -s -o /dev/null -w "%{http_code}\n" https://saitallyexpert.in/nexus/api/portal/prn-auto-print/login/      # expect 401
# --- migrations (run as the service user, inside its virtualenv; both are read-only)
cd $APP/backend && source <venv>/bin/activate        # the venv the service uses
python manage.py showmigrations registrations tallysl accounts
python manage.py migrate --plan                       # must list ONLY the migrations in §3
# --- the LAN-test guard, without printing any other secret
awk -F= '$1=="NEXUS_LAN_TEST_MODE"{print $1"="$2}' .env ; grep -c '^NEXUS_LAN_TEST_MODE=' .env
awk -F= '$1=="DJANGO_DEBUG"{print $1"="$2}' .env
# --- database name only (not credentials) and backup destination
awk -F= '$1=="DB_NAME"||$1=="DB_HOST"{print $1"="$2}' .env
```
Expected: `DJANGO_DEBUG=False`; no `NEXUS_LAN_TEST_MODE` line (or `False`) — with `DEBUG=False` and `NEXUS_LAN_TEST_MODE=True` Django refuses to start; the plan contains nothing beyond §3.

## 3. Pending migrations and risk
| Migration | Owner | Effect | PRN risk |
|---|---|---|---|
| `registrations.0012_device_request_kinds` — *only if production lacks it* | existing PRN code (commit `eee0a0c`) | adds 4 NOT-NULL columns **with DB defaults** to `registrations_devicetransferrequest`; makes `device_id`, `from_customer_id` nullable (drops/recreates their FK + index); adds filtered unique index `uniq_pending_first_activation_per_customer` | additive and backward-compatible (existing rows → `kind='TRANSFER'`; old code still inserts via defaults; index cannot be violated by existing data). Brief lock on that one small table → apply with the app stopped. **Not reversible on SQL Server** (reverse fails: filtered index depends on `device_id`) — rollback = restore backup |
| `tallysl.0001_initial` | new | 14 `tallysl_*` tables, indexes, constraints; FKs to `auth_user` only | none |
| `tallysl.0002_seed_gstr2tally` | new | 1 row in `tallysl_tallyproduct` | none |

Rehearsal on a scratch database (PRN migrations ≤0011 + sample PRN rows → plain `migrate` → `migrate tallysl zero`): PRN rows byte-identical at every step. **Any other migration in `migrate --plan` = STOP and review.**

## 4. Backup and restore verification (approval required; do before any write)
SQL Server on Linux (`mssql-tools18`); adjust host/login to the production `.env` values. If the database is on another server, run the same T-SQL there.
```bash
STAMP=$(date +%Y%m%d_%H%M)
BK=/var/opt/mssql/backup                 # a directory the SQL Server service account can write; use the real backup destination
SQL="/opt/mssql-tools18/bin/sqlcmd -S <host> -U <login> -C"        # password via prompt / SQLCMDPASSWORD in your shell only; never in logs
$SQL -Q "BACKUP DATABASE [<db>] TO DISK = N'$BK/<db>_pre_tallysl_$STAMP.bak' WITH COMPRESSION, CHECKSUM, INIT, STATS = 10;"
$SQL -Q "RESTORE VERIFYONLY FROM DISK = N'$BK/<db>_pre_tallysl_$STAMP.bak' WITH CHECKSUM;"
# restore under another name and compare PRN record counts
$SQL -Q "RESTORE FILELISTONLY FROM DISK = N'$BK/<db>_pre_tallysl_$STAMP.bak';"        # note the two logical names -> <data> and <log>
$SQL -Q "RESTORE DATABASE [<db>_verify] FROM DISK = N'$BK/<db>_pre_tallysl_$STAMP.bak'
         WITH MOVE '<data>' TO N'/var/opt/mssql/data/<db>_verify.mdf', MOVE '<log>' TO N'/var/opt/mssql/data/<db>_verify_log.ldf', STATS = 10;"
$SQL -Q "SELECT 'live' src, (SELECT COUNT(*) FROM [<db>].dbo.registrations_projectcustomer) customers, (SELECT COUNT(*) FROM [<db>].dbo.registrations_deviceregistration) devices,
                (SELECT COUNT(*) FROM [<db>].dbo.registrations_devicetransferrequest) transfers, (SELECT COUNT(*) FROM [<db>].dbo.registrations_licenseauditlog) audit
         UNION ALL SELECT 'restored', (SELECT COUNT(*) FROM [<db>_verify].dbo.registrations_projectcustomer), (SELECT COUNT(*) FROM [<db>_verify].dbo.registrations_deviceregistration),
                (SELECT COUNT(*) FROM [<db>_verify].dbo.registrations_devicetransferrequest), (SELECT COUNT(*) FROM [<db>_verify].dbo.registrations_licenseauditlog);"
$SQL -Q "DROP DATABASE [<db>_verify];"
# also snapshot the app: revision, .env, frontend dist, nginx site
git -C $APP rev-parse HEAD > ~/nexus_prev_commit_$STAMP.txt
sudo tar czf ~/nexus_files_$STAMP.tgz $APP/backend/.env <frontend dist dir> /etc/nginx/sites-enabled/<site>   # keep private (contains secrets)
```
**Gate:** `RESTORE VERIFYONLY` succeeds, `live` and `restored` rows match, and the verify database is dropped. Otherwise NO-GO.

## 5. Deployment sequence (after approval; Ubuntu / nginx / systemd, per §2 discovery)
```bash
# 0. maintenance window (1–3 min). PRN desktop clients rely on signed offline leases and retry.
# 1. code (fast-forward only; record the previous commit from §4)
cd $APP && git fetch --tags && git checkout tallysl-v1.0.0     # or: git pull --ff-only origin nexus-web
cd backend && source <venv>/bin/activate
pip install -r requirements.txt                                 # no new packages expected
python manage.py check --deploy
python manage.py showmigrations registrations tallysl
python manage.py migrate --plan                                 # re-confirm: only §3
# 2. frontend (base '/nexus/'): build and publish exactly the way the current SPA is published
cd ../frontend && npm ci && npm run build        # then copy dist/ to the nginx root used today (keep the previous dist as dist.prev)
# 3. stop -> migrate -> start (only the Nexus unit; no other hosted project)
sudo systemctl stop $UNIT
cd ../backend && python manage.py migrate
python manage.py showmigrations tallysl                         # both [X]
sudo systemctl start $UNIT && sleep 3 && systemctl is-active $UNIT
# 4. verify (§7)
```
No `nginx` edit/reload, no `.env` edit, no dependency change is part of this release.

## 6. Product key provisioning (secure) — only after §7 steps 1–3 pass
```bash
cd $APP/backend && source <venv>/bin/activate
python manage.py create_tally_product_key gstr2tally --label "GSTR2Tally .NET client"    # prints once; only the SHA-256 is stored
```
Do **not** redirect output, tee into logs, run inside `script`/tmux logging, or paste into tickets/chat. Copy into the password manager and hand it to the .NET
developer over a secure channel. The key is a public *product identifier* for distributed EXEs (real protection is login + emailed code + Activation Key + device binding; see
`TALLY-SL-LICENSING.md` §6) but must still stay out of source control. Rotation/abuse: issue a new one, ship it, then
`UPDATE tallysl_tallyproductkey SET active = 0 WHERE id = <old>;`.

## 7. Live verification (the Tally API is NOT live today; "available" only after step 3 returns 200)
```bash
B=https://saitallyexpert.in/nexus
curl -s $B/api/health/                                                             # 200 ok/ok
curl -s -i $B/api/v1/tally-sl/gstr2tally/identify/ | head -1                       # 401 (was 404 before the release)
curl -s -H "X-Product-Key: <key>" $B/api/v1/tally-sl/gstr2tally/identify/          # 200 {"api_version":"v1","product":{"code":"gstr2tally",…}}
curl -s -o /dev/null -w "%{http_code}\n" $B/api/portal/prn-auto-print/login/       # still 401 (PRN unchanged)
sudo journalctl -u $UNIT -n 50 --no-pager | grep -iE 'error|traceback' || echo "no errors"
```
UI: Superadmin sign-in → sidebar (Barcode AutoPrint submenus, PRN Customer List loads, GSTR2Tally › Customers, Reports › GSTR/PRN), Configuration (top-right) opens with both accordions.
PRN: one real PRN status check from a test PC. SMTP: `python manage.py send_test_email <your address>` (prints settings without the password); Tally mail kinds in `registrations_emaillog`: `tally_welcome`, `tally_activation_code`, `tally_activation_key`, `tally_password_reset`, `tally_device_replaced`, `tally_password_changed`, `tally_new_customer`.

## 8. Rollback
| Situation | Action |
|---|---|
| UI/API misbehaves, data fine | `git checkout <previous commit from §4>`; restore previous `dist`; `sudo systemctl restart $UNIT`. `tallysl_*` tables stay inert (PRN never references them). |
| Disable Tally API only | `UPDATE tallysl_tallyproductkey SET active = 0;` → Tally clients get 401, PRN unaffected |
| Remove Tally tables | stop unit → (new code still present) `python manage.py migrate tallysl zero` (drops only `tallysl_*`; rehearsed) → previous code → start unit |
| `registrations.0012` suspected | do **not** run `migrate registrations 0011` (fails on SQL Server). Restore the §4 backup with the unit stopped (`RESTORE DATABASE [<db>] FROM DISK=… WITH REPLACE`), previous code, start, rerun PRN checks. Registrations made after the backup are lost by a restore. |
| Service will not start | `journalctl -u $UNIT -n 100`; the usual cause is a `.env` containing `NEXUS_LAN_TEST_MODE=True` with `DEBUG=False`; fix only that line, or roll back the code. |

## 9. Endpoints for the external .NET developer (LOCAL-verified; not live)
Base `https://saitallyexpert.in/nexus/api/v1/tally-sl/gstr2tally/` — `X-Product-Key` on all, `Authorization: Bearer` on (B).
`GET identify/` · `POST register/` · `POST auth/login/` · `POST auth/logout/`(B) · `POST auth/forgot-password/` · `POST auth/reset-password/` · `POST auth/change-password/`(B) · `POST auth/refresh/`(B) ·
`GET session/`(B) · `GET account/`(B) · `GET license/status/`(B) · `POST tally-info/`(B) · `POST usage/open/`(B) · `POST activate/request-code/` · `POST activate/verify/` · `POST activate/confirm/`.
Contracts, JSON and C# examples: `docs/TALLY-SL-LICENSING.md`.
