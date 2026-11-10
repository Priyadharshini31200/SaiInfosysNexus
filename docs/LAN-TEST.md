# Nexus LAN TEST mode (development only, plain HTTP)

Lets Barcode Auto Print on another PC reach Nexus on the development PC. **Never** use this on a public network or in
production: production is HTTPS only (see `DEPLOYMENT.md`) and Django refuses `NEXUS_LAN_TEST_MODE` when `DJANGO_DEBUG=False`.

## Root cause of `NEXUS_UNREACHABLE` (found 2026-11-10)
1. Nothing was listening on port 8001 (the server was not running).
2. `runserver` defaulted to `127.0.0.1`, so even when running it was invisible to other PCs.
3. `ALLOWED_HOSTS` held only `localhost,127.0.0.1`, so a request to `http://<LAN IP>:8001` would have been refused (HTTP 400).
4. The dev PC's DHCP address moved from 192.168.1.15 to 192.168.1.13, so PRN's saved URL pointed at a dead address.

## Configure (backend/.env, git-ignored)
    DJANGO_DEBUG=True
    NEXUS_LAN_TEST_MODE=True

In this mode `runserver` binds `0.0.0.0:8001` and this PC's private LAN IPs are added to `ALLOWED_HOSTS` automatically
(no wildcard), so a changed DHCP address needs no edit. Better still, give the PC a DHCP reservation / static IP.

## Start
    cd D:\SaiInfosysNexus\backend
    .\.venv\Scripts\python.exe manage.py runserver --noreload

(An explicit `manage.py runserver 127.0.0.1:8001` still wins and keeps it local-only.)

## Windows firewall (run once in an ADMINISTRATOR PowerShell) - LAN only, Private profile only
    New-NetFirewallRule -DisplayName "Nexus LAN TEST 8001" -Direction Inbound -Protocol TCP -LocalPort 8001 `
      -Action Allow -Profile Private -RemoteAddress LocalSubnet

Remove when finished: `Remove-NetFirewallRule -DisplayName "Nexus LAN TEST 8001"`.

## Verify from the PRN PC
    Test-NetConnection 192.168.1.13 -Port 8001
    Invoke-WebRequest http://192.168.1.13:8001/api/device/prn-auto-print/public/keys/ -UseBasicParsing

Expected: `TcpTestSucceeded : True` and HTTP 200 with `{"keys":[{"key_id":...,"algorithm":"Ed25519"}]}`.
Then set PRN's Nexus URL to `http://192.168.1.13:8001` (PRN keeps the URL configurable).

## Production (Hostinger VPS, not deployed yet)
`DJANGO_DEBUG=False`, `NEXUS_LAN_TEST_MODE` unset, real domain in `DJANGO_ALLOWED_HOSTS` and
`DJANGO_CSRF_TRUSTED_ORIGINS` (https://), `DJANGO_BEHIND_PROXY=True` behind the TLS proxy, secure cookies on, and
`LICENSE_SIGNING_PRIVATE_KEY` / SMTP password only in the server's environment. HTTP is redirected to HTTPS
(`SECURE_SSL_REDIRECT`, HSTS); there is no HTTP fallback.
