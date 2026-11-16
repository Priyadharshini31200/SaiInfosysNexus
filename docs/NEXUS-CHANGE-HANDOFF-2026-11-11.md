# Nexus change and deployment handoff ? 11 November 2026

## Status and scope
Local implementation and additive local migration are complete. Nothing was committed, pushed or deployed to Hostinger. `.env` files, customer identities, project slugs, API routes, activation keys, serial numbers, license associations and device cryptography were preserved. Existing untracked tests, documents and logs were not edited. No customer rows were deleted or recreated.

This is not a claim that Hostinger is fixed: there is no live VPS connection, production database identity, deployed branch, Nginx config or systemd unit available in this checkout. Browser login/rendering on Hostinger and real SMTP inbox delivery remain unverified.

## Root causes and evidence
1. `frontend/src/pages/Dashboard.jsx` previously returned an empty `<section>` for every Admin. The dashboard APIs were Superadmin-only. Authorized Admins can now see exactly the project dashboards granted to their account; an unassigned account gets an explicit access message.
2. `accounts.permissions.user_can` previously let global `AdminPermission` rows override per-user/project grants, and an absent Entry row granted Entry by default. Customer List still required View. This both explains an Admin's blank/denied list and prevented account/project isolation. The new evaluator uses explicit scoped grants and denies absent grants. Legacy global rows and audit history remain intact; they no longer authorize requests.
3. Customer List and Dashboard have distinct sources. Customer List returns Nexus ProjectCustomer rows; usage also includes external runtime devices/licenses. GSTR usage previously omitted unmatched Nexus registrations. It now includes them without inventing completed imports. Runtime rows are read-only; they are not imported into Nexus as customer/login/license records.
4. Read-only check against this LOCAL `.\SQLEXPRESS` / `SaiInfosysNexus` database: five ACTIVE `prn-auto-print` customer rows, zero Nexus GSTR rows. Authorized local Customer List API: HTTP 200, five rows, returned IDs exactly equal to SQL IDs. No customer details were printed.
5. Local dashboard/report comparison after changes: GSTR total 1, used 0, not used 1, this-month expiry 0, expired 0; Barcode total 6, used 2, not used 4, this-month expiry 2, expired 0. Both project's Used/Not Used report row totals exactly matched summary counts. Six Barcode usage units versus five Nexus customers is a source difference, not a fabricated sixth customer.
6. The initial ODBC 08001/SSPI failure occurred under the sandbox identity. The same read-only check outside the sandbox succeeded. SQL Server service was running; SQL Browser was stopped. No DB encryption, authentication or `.env` settings were changed. This local sandbox error is NOT evidence of a Hostinger outage.

## Consistent metric definitions
- Used/Not Used are complementary filters of the same project-specific base set used by the usage report, drilldown and export.
- GSTR: existing active licensed devices with completed Tally import jobs; unmatched active Nexus registrations are included and remain Not Used until actual usage exists. Creating a row or merely logging in is not a completed import.
- Barcode AutoPrint: existing non-revoked runtime licenses/customers merged with active Nexus registrations by serial. Actual runtime activation/validation/device activity counts as Used. Nexus first activation/enrollment counts as activation, preserving the runtime activation definition; record creation does not count. Existing runtime identifiers and bindings are untouched.
- Expire: active-status Nexus Trial/1-Year licenses with effective expiry <= now.
- This Month Expiry: active-status Nexus effective expiries within the current Asia/Kolkata calendar month, including those expired earlier in that month. Lifetime and unactivated device trials are excluded.
- Used/Not Used and expiry metrics measure different populations: runtime usage units versus centrally managed Nexus license expiries. Counts are never substituted for each other. An unreadable external DB is shown as unavailable, not zero or fabricated data.
- Existing repository has no separate This Month User metric to replace.
- Dashboard links open filtered Usage/Expiry Reports. The expiry URL uses `state=expired` or the current month; exports receive the displayed record IDs/filters. Customer List retains search and license type filters, adds status and 10/25/50/100 row pagination; export includes all matched rows rather than just the current page.

## Permission matrix
All scoped grants belong to a particular active Admin. Superadmin always retains full access. Missing grants deny access. Configuration is Superadmin-only; grants cannot target Superadmin. Existing Admin/profile/audit rows are preserved.

| Existing menu/module | Module code | Independent grants |
|---|---|---|
| Project Dashboard | `<project>:dashboard` | View |
| Customer / Add Customer / Customer List | `<project>` | View, Entry/Create, Edit, Delete, Extend Trial, Cancel Trial, Inactivate, Approve/Reject, Renew, Export/Print |
| Barcode device management | `prn-auto-print` | Device Transfer/Revoke |
| Reports / License Expiry Report | `<project>:expiry` | Reports/View, Export/Print |
| Reports / Usage Report | `<project>:usage` | Reports/View, Export/Print |

The repository has Dashboard, project Customer pages and Reports; it has no Masters menu, so none was invented. Project groups expand with `+` in Configuration. Parent navigation is visible when an accessible child exists. View grants no mutation rights. Export also requires the corresponding View/Reports permission. Approval-only can review/approve requests without Create access; it cannot create an unrelated customer without a pending request. Pending request status is locked and rechecked inside the registration transaction to prevent duplicate approval races. Customer password reset and activation-key reissue stay Superadmin-only. Device API mutations require their independent scoped Device Transfer/Revoke grant.

Backend checks apply to direct URLs/API calls and all report/export querysets. Revocation is effective on the next backend request. Admin UI refreshes `/auth/me/` every 10 seconds while visible and on refocus; revoked data/actions are hidden. Dashboard/report data refresh alongside permission polling; lists retain audit fingerprint polling. No automatic grants were created for old Admins: Superadmin must deliberately assign access in Configuration after rollout.

## Password/email before and after
Before: own staff password change always required Current Password. Customer profile Edit changed the hash but left password-change tracking stale and emitted no Password Changed notice, so the account could still look eligible for provisioning-email retry. Portal changes likewise had no change notice.

After:
- A Superadmin authenticated through successful login within five minutes enters New Password + Confirm. Older/missing login timestamp requires Current Password, which securely reauthenticates. Admin own changes continue requiring Current Password. All changed staff sessions are ended; other sessions fail their hash checks.
- Superadmin can reset a specific Admin through Configuration and reset customer credentials through the existing authorized flow. Admin Edit permission cannot reset customer passwords.
- Customer Edit updates the canonical Nexus hash, clears temporary status/lockout, records the change timestamp, preserves license/device fields, audits the change and sends a password-free notice after commit. Portal/device password change and reset use the same canonical source and send the same notice. Old password-derived tokens/reset links stop working.
- A delayed initial credentials send is refused if the database hash changed or password_changed_at exists. Retry/resend remains limited to unused provisioning accounts; it never reads back a plaintext password. Existing initial provisioning and explicit temporary-password reset workflows remain intact.
- Password Changed messages include no password. Existing SMTP delivery/retry/logging remains; a failed notice cannot undo the changed password. EmailLog records delivery status. SMTP acceptance is not proof of inbox delivery. Console/file/dummy development backends no longer print or persist credential bodies.
- Blank/whitespace passwords are rejected and new-password requests are bounded to 128 characters. No uppercase/lowercase/digit/symbol pattern is required. Existing case-insensitive password compatibility and Django hashing are preserved.
- Existing initial provisioning/generation and explicit recovery APIs still expose newly generated values in their established one-time flows; stored hashes/old plaintext passwords are never returned. Changing those integration contracts requires client coordination.

## Migration
`accounts.0006_usermodulepermission_can_approve_and_more` adds six false-default granular permission columns (approve, cancel_trial, extend_trial, inactivate, renew, device_transfer), changes only the default of new can_entry rows to false, and updates action choices. It contains no destructive/data migration. Existing per-user grants are preserved. Applied successfully ONLY to the local development DB; `makemigrations --check --dry-run` reports no changes. Test databases also applied it successfully. Hostinger migration has not run.

## Verification
- `npm.cmd run build --prefix frontend`: passed, Vite 101 modules.
- `backend\.venv\Scripts\python.exe backend/manage.py check`: passed, zero issues.
- `git diff --check`: passed (Git emitted existing CRLF normalization warnings only).
- Final focused SQL Server regressions: 17/17 passed in the final combined run; the preceding standalone run passed 16/16 before adding the approval-race regression.
- Earlier combined non-dashboard test run: 102 tests, 99 passed, three failed expectations (two old `Barcode Auto Print` label assertions, one legacy global-Entry access assertion). The in-memory test process used PBKDF2 with reduced iterations only for test speed; production hash settings were not changed.
- Earlier SQL Server dashboard + focused run: 30 tests, 25 passed, five failed old expectations (two dict equality assertions omit the new `expired` field, two old display-name assertions, one old PRN usage assertion excludes activation/enrollment). Actual SQL executed successfully. No pre-existing untracked test file was edited to hide these failures.
- Final combined SQL Server suite: 127 tests, 119 passed, 8 failures exactly matching the changed expectations listed above, zero errors, 41.653 seconds. All 17 new regressions passed. Temporary test DB names were uniquely prefixed `test_NexusCodex_`; Django created and removed only those databases.
- Real local customer API and summary/report count equality verified read-only as described above.
- Not verified: Hostinger data/network/build/service, browser rendered rows/login navigation, real SMTP inbox delivery, and a physical Barcode/print-device end-to-end run. Existing device/password/portal suites exercise their server flows, but are not proof of physical printing.

## Exact production-source staging commands (not executed)
Run from PowerShell in `D:\SaiInfosysNexus`. Current branch was verified as `nexus-web`; remote name `origin` exists. The explicit list excludes `.env`, logs, frontend/dist, existing untracked tests/docs and this handoff. New focused tests remain local as requested.

```powershell
git switch nexus-web
git add -- backend/apps/accounts/constants.py backend/apps/accounts/models.py backend/apps/accounts/permissions.py backend/apps/accounts/serializers.py backend/apps/accounts/services.py backend/apps/accounts/urls.py backend/apps/accounts/views.py backend/apps/dashboard/usage.py backend/apps/dashboard/views.py backend/apps/registrations/device_views.py backend/apps/registrations/mailer.py backend/apps/registrations/models.py backend/apps/registrations/portal.py backend/apps/registrations/report_views.py backend/apps/registrations/serializers.py backend/apps/registrations/services.py backend/apps/registrations/views.py frontend/src/api/config.js frontend/src/components/ConfigurationDrawer.jsx frontend/src/components/CustomerDetailsView.jsx frontend/src/components/CustomerForm.jsx frontend/src/components/CustomerList.jsx frontend/src/components/LicenseActions.jsx frontend/src/components/ProfileDrawer.jsx frontend/src/components/ProjectUsageCard.jsx frontend/src/components/ProjectUsageCard.module.css frontend/src/components/RegistrationRequests.jsx frontend/src/components/ResetPasswordForm.jsx frontend/src/context/AuthContext.jsx frontend/src/layouts/ProjectSidebarMenu.jsx frontend/src/layouts/ReportsSidebarMenu.jsx frontend/src/layouts/Sidebar.jsx frontend/src/layouts/navigation.js frontend/src/pages/Dashboard.jsx frontend/src/pages/LicenseExpiryReportPage.jsx frontend/src/pages/ProjectAddCustomerPage.jsx frontend/src/pages/ProjectCustomersPage.jsx frontend/src/pages/UsageReportPage.jsx backend/apps/accounts/migrations/0006_usermodulepermission_can_approve_and_more.py
git diff --cached --check
git diff --cached --stat
git diff --cached
# Only after reviewing the staged changes:
git commit -m "Fix scoped Nexus access, live metrics and password notifications"
# Only after your explicit approval to push:
git push origin nexus-web
```

## Hostinger discovery and deployment
Exact Hostinger deployment commands cannot be truthfully supplied from this checkout. `docs/DEPLOYMENT.md` contains a Windows/Waitress/NSSM example and a sample Nginx host, not an actual Linux VPS path, Gunicorn unit or deployed branch. The local Vite proxy uses relative `/api`; production must serve API and SPA under the configured same origin. Do not reuse sample identifiers as production values.

On the actual VPS, these read-only commands collect the required settings; they do not deploy:

```bash
pwd
systemctl list-unit-files --type=service | grep -Ei 'nexus|gunicorn'
find /var/www /srv /opt /home -maxdepth 5 -type d -name .git -print 2>/dev/null
sudo nginx -T 2>&1 | grep -nE 'configuration file|server_name|root |proxy_pass'
```

After selecting the actual service/repository from that output, inspect privately `systemctl cat <actual-service>`, `systemctl show <actual-service> -p WorkingDirectory -p ExecStart -p EnvironmentFiles -p User`, `git -C <actual-repo> branch --show-current`, `git -C <actual-repo> rev-parse HEAD`, and `git -C <actual-repo> status --short`. Do not paste environment secrets. Actual virtualenv, environment source, SQL endpoint/database, service user, frontend build/public path, Nginx site and previous revision must be confirmed before preparing executable deployment commands. The task remains blocked on those live details; no guessed `cd`, restart, copy, migration or reload command is presented as verified.

Once verified and explicitly approved: take a restorable SQL backup and snapshot of service/Nginx/frontend artifacts; record previous application revision; fetch/review nexus-web; install only pinned required dependencies if changed; run the actual virtualenv's Django checks and additive migration; build frontend; publish to the confirmed public directory with stale-asset handling; restart the confirmed service and reload Nginx only after `nginx -t`. Configure Admin grants as Superadmin before reopening Admin access. Do not run bootstrap reset/seed or regenerate signing keys on production.

## Rollback and post-deployment verification
No live rollback was needed because no live deployment occurred. For a future deployment, restore the recorded prior application revision and matching frontend artifact through the confirmed existing release procedure; restart the discovered service and validate Nginx. Keep the additive permission columns and legacy global permission rows intact rather than dropping columns or deleting grants. Test rollback compatibility with the prior release first; if a database restore is necessary, use the verified pre-deployment backup in the approved maintenance window and account for writes since that backup. Do not truncate/recreate customer tables.

After deployment, verify `/api/health/` 200 and DB ok; session/CSRF/login; Superadmin profile change/relogin; Admin grant/deny/403 and revocation on a second session; SQL customer IDs equal List API rows; Used/Not Used and expiry counts equal filtered report/export rows; search/status/license/pagination; Add/Edit/inactivate/extend/cancel/approval sync; initial temporary credentials once; password change notice without password; old password/token rejected and new password accepted through portal and Barcode source; SMTP acceptance/failure logs; license signature/device enrollment/transfer/inactivation; physical printing with an approved test account/device. Compare counts against the actual production DB, never against this local database.

## Changed production files
- `backend/apps/accounts/constants.py`
- `backend/apps/accounts/models.py`
- `backend/apps/accounts/permissions.py`
- `backend/apps/accounts/serializers.py`
- `backend/apps/accounts/services.py`
- `backend/apps/accounts/urls.py`
- `backend/apps/accounts/views.py`
- `backend/apps/dashboard/usage.py`
- `backend/apps/dashboard/views.py`
- `backend/apps/registrations/device_views.py`
- `backend/apps/registrations/mailer.py`
- `backend/apps/registrations/models.py`
- `backend/apps/registrations/portal.py`
- `backend/apps/registrations/report_views.py`
- `backend/apps/registrations/serializers.py`
- `backend/apps/registrations/services.py`
- `backend/apps/registrations/views.py`
- `frontend/src/api/config.js`
- `frontend/src/components/ConfigurationDrawer.jsx`
- `frontend/src/components/CustomerDetailsView.jsx`
- `frontend/src/components/CustomerForm.jsx`
- `frontend/src/components/CustomerList.jsx`
- `frontend/src/components/LicenseActions.jsx`
- `frontend/src/components/ProfileDrawer.jsx`
- `frontend/src/components/ProjectUsageCard.jsx`
- `frontend/src/components/ProjectUsageCard.module.css`
- `frontend/src/components/RegistrationRequests.jsx`
- `frontend/src/components/ResetPasswordForm.jsx`
- `frontend/src/context/AuthContext.jsx`
- `frontend/src/layouts/ProjectSidebarMenu.jsx`
- `frontend/src/layouts/ReportsSidebarMenu.jsx`
- `frontend/src/layouts/Sidebar.jsx`
- `frontend/src/layouts/navigation.js`
- `frontend/src/pages/Dashboard.jsx`
- `frontend/src/pages/LicenseExpiryReportPage.jsx`
- `frontend/src/pages/ProjectAddCustomerPage.jsx`
- `frontend/src/pages/ProjectCustomersPage.jsx`
- `frontend/src/pages/UsageReportPage.jsx`
- `backend/apps/accounts/migrations/0006_usermodulepermission_can_approve_and_more.py`

Local regression file: `backend/apps/accounts/test_scoped_access.py` (not included in the staging command). This handoff is also local.
