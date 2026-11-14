"""Django settings for Sai Infosys Nexus. All environment specific values come from .env."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in the environment (.env).")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# LAN TEST mode: lets Barcode Auto Print on another PC reach a development server over plain HTTP.
# Development only (refused unless DJANGO_DEBUG=True). This PC's private-range (RFC1918) addresses are added to
# ALLOWED_HOSTS automatically, so a DHCP address change (e.g. .15 -> .13) does not break it. Nothing else is opened up:
# no wildcard host, and `runserver` binds 0.0.0.0 only in this mode (see apps/core/management/commands/runserver.py).
NEXUS_LAN_TEST_MODE = env_bool("NEXUS_LAN_TEST_MODE", False)
if NEXUS_LAN_TEST_MODE:
    if not DEBUG:
        raise ImproperlyConfigured("NEXUS_LAN_TEST_MODE requires DJANGO_DEBUG=True; production must use HTTPS only.")
    import ipaddress
    import socket

    def _local_private_ips():
        found = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                found.add(info[4][0])
        except OSError:
            pass
        try:  # the interface used for the default route (no packet is actually sent)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                found.add(s.getsockname()[0])
        except OSError:
            pass
        return sorted(ip for ip in found if ipaddress.ip_address(ip).is_private and not ip.startswith(("127.", "169.254.")))

    ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS + _local_private_ips()))

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "apps.core",
    "apps.accounts",
    "apps.dashboard",
    "apps.registrations",
    "apps.tallysl",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.NoStoreApiResponsesMiddleware",
    "apps.core.middleware.ProjectApiCorsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------- Database
_db_options = {
    "driver": env("ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
    # Fail fast when SQL Server is unreachable so the API answers 503 JSON instead of hanging until the proxy gives up (502/504).
    "connection_timeout": int(env("DB_CONNECT_TIMEOUT", "15")),
    "extra_params": ";".join(
        [
            f"Encrypt={'yes' if env_bool('DB_ENCRYPT', True) else 'no'}",
            f"TrustServerCertificate={'yes' if env_bool('DB_TRUST_SERVER_CERTIFICATE', False) else 'no'}",
        ]
        + (["Trusted_Connection=yes"] if env_bool("DB_TRUSTED_CONNECTION", False) else [])
    ),
}

DATABASES = {
    "default": {
        "ENGINE": "mssql",
        "NAME": env("DB_NAME", "SaiInfosysNexus"),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", ""),
        "USER": "" if env_bool("DB_TRUSTED_CONNECTION", False) else env("DB_USER", ""),
        "PASSWORD": "" if env_bool("DB_TRUSTED_CONNECTION", False) else env("DB_PASSWORD", ""),
        "OPTIONS": _db_options,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Project databases on the same SQL Server instance, read (never written) by the Super Admin dashboard.
GSTR_DB_NAME = env("GSTR_DB_NAME", "Gstr_db")
PRN_DB_NAME = env("PRN_DB_NAME", "PRNPRINT")

# ---------------------------------------------------------------- Authentication
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.CaseInsensitiveModelBackend"]

# Deliberately empty: any non-empty password is acceptable (no length, complexity, common-word or numeric rules).
# Passwords are case-insensitive: they are casefolded before hashing and checking (apps.accounts.passwords).
AUTH_PASSWORD_VALIDATORS = []

# ---------------------------------------------------------------- Sessions / CSRF
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "nexus_sessionid"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_AGE = int(env("DJANGO_SESSION_COOKIE_AGE", "28800"))
SESSION_SAVE_EVERY_REQUEST = True  # sliding idle timeout

CSRF_COOKIE_NAME = "nexus_csrftoken"
CSRF_COOKIE_HTTPONLY = False  # the SPA reads it and echoes it in X-CSRFToken
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]  # the proxy's plain-HTTP health probe must not be redirected
# Behind a TLS-terminating reverse proxy (IIS ARR / nginx) Django only sees plain HTTP from the proxy. Without these two
# settings SECURE_SSL_REDIRECT loops forever and CSRF origin checks fail on https://. Set DJANGO_BEHIND_PROXY=True in
# production ONLY when the proxy overwrites X-Forwarded-Proto / X-Forwarded-Host itself (see docs/DEPLOYMENT.md).
if env_bool("DJANGO_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
# Browser origins allowed to call the server-to-server project APIs (/api/portal/, /api/device/). Empty = no CORS.
CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ALLOWED_ORIGINS", "")

# ---------------------------------------------------------------- REST framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.core.authentication.SessionAuthentication401"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {"login": env("LOGIN_THROTTLE_RATE", "10/min"), "portal": env("PORTAL_THROTTLE_RATE", "30/min"),
        "device": env("DEVICE_THROTTLE_RATE", "120/min"),
        "device_enroll": env("DEVICE_ENROLL_THROTTLE_RATE", "10/min"),
        "device_public": env("DEVICE_PUBLIC_THROTTLE_RATE", "10/min"),
    },
}

# ---------------------------------------------------------------- Email (credentials, recovery, reset links)
EMAIL_HOST = env("EMAIL_HOST", "").strip()
# A configured EMAIL_HOST means real SMTP, in DEBUG too; without one, DEBUG prints mail to the console
# (reported as "not delivered - no mail server configured"). EMAIL_BACKEND in the environment always wins.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend" if (EMAIL_HOST or not DEBUG) else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "").strip()
# Gmail shows App Passwords in groups of four ("abcd efgh ijkl mnop"); the spaces are not part of it.
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "").replace(" ", "")
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)  # port 465; mutually exclusive with STARTTLS (port 587)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True) and not EMAIL_USE_SSL
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "15"))
MAIL_SEND_ATTEMPTS = int(env("MAIL_SEND_ATTEMPTS", "3"))  # credential emails are tried this many times before being reported as failed
MAIL_RETRY_DELAY_SECONDS = float(env("MAIL_RETRY_DELAY_SECONDS", "2"))  # waits 1x, 2x ... this long between attempts
# Gmail only sends as the signed-in account (or a verified alias), so the sender account is the sensible default.
DEFAULT_FROM_EMAIL = (env("DEFAULT_FROM_EMAIL") or "").strip() or (
    f"Sai Infosys Nexus <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "Sai Infosys Nexus <no-reply@saiinfosys.example>"
)

# ---------------------------------------------------------------- Customer portal (connected projects)
CUSTOMER_TOKEN_MAX_AGE = int(env("CUSTOMER_TOKEN_MAX_AGE", "86400"))  # seconds a customer sign-in token stays valid
PASSWORD_RESET_MAX_AGE = int(env("PASSWORD_RESET_MAX_AGE", "3600"))
# Extra addresses (besides every active Super Admin account with an email) told about each new sign-up request
NEW_REQUEST_NOTIFY_EMAILS = [e.strip() for e in env("NEW_REQUEST_NOTIFY_EMAILS", "").split(",") if e.strip()]
LICENSE_LEASE_DAYS = int(env("LICENSE_LEASE_DAYS", "7"))  # how long an offline client may rely on one status check
# ---------------------------------------------------------------- Signed device licensing (see README)
# Ed25519 private key (base64 of the raw 32 bytes). Environment / secret store ONLY: never the database, the repo or a client.
LICENSE_SIGNING_PRIVATE_KEY = env("LICENSE_SIGNING_PRIVATE_KEY", "")
LICENSE_KEY_ID = env("LICENSE_KEY_ID", "k1")
# PRN validity: an entitlement never outlives first_activated_at + this many days (nor the licence's own earlier expiry). 0 = off.
# Off by default: a customer goes straight to the product dashboard with the emailed credentials and changes the password
# voluntarily (My Profile). Set ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE=1 to make the first sign-in answer password_change_required.
ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE = env("ENFORCE_FIRST_LOGIN_PASSWORD_CHANGE", "0") == "1"
PRN_VALIDITY_DAYS = int(env("PRN_VALIDITY_DAYS", "7"))  # PRN trial length, counted from first device activation
# Trial length per product that starts counting at first device activation (1 Year / Lifetime are never affected).
TRIAL_DAYS_BY_PRODUCT = {"prn-auto-print": PRN_VALIDITY_DAYS}
# A customer's very first computer waits for Super Admin approval (FIRST ACTIVATION request) before it is bound to the licence.
DEVICE_ACTIVATION_APPROVAL_REQUIRED = env_bool("DEVICE_ACTIVATION_APPROVAL_REQUIRED", True)
DEVICE_CLOCK_SKEW_SECONDS = int(env("DEVICE_CLOCK_SKEW_SECONDS", "300"))  # accepted +- on X-Device-Timestamp
DEVICE_NONCE_TTL_SECONDS = int(env("DEVICE_NONCE_TTL_SECONDS", "600"))  # must exceed twice the skew
DEVICE_EVIDENCE_KEY = env("DEVICE_EVIDENCE_KEY", "")  # HMAC key protecting stored hardware hashes; defaults to DJANGO_SECRET_KEY

# Per-account brute-force guard (the portal throttle is per calling server, so it cannot protect one account)
CUSTOMER_MAX_FAILED_LOGINS = int(env("CUSTOMER_MAX_FAILED_LOGINS", "5"))
CUSTOMER_LOCKOUT_MINUTES = int(env("CUSTOMER_LOCKOUT_MINUTES", "15"))

# ---------------------------------------------------------------- Misc
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = False
USE_TZ = True

# Bootstrap accounts (development only)
# Env-configurable; in DEBUG only, an unset value falls back to the documented initial password
# so `seed_initial_users` works on a fresh checkout. Never used when DEBUG is off.
BOOTSTRAP_SUPERADMIN_PASSWORD = env("SUPERADMIN_INITIAL_PASSWORD", "Superadmin" if DEBUG else None)
BOOTSTRAP_ADMIN_PASSWORD = env("ADMIN_INITIAL_PASSWORD", "Admin" if DEBUG else None)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
