"""Tally SL emails. Uses the existing Nexus mailer (SMTP settings, retries, delivery log) without changing it.
Bodies are never stored or logged: they can contain a temporary password, activation key or reset link."""
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.registrations import mailer

from .models import TallyCustomer


def _send(customer, subject, rows, intro, kind, steps=(), notes=()):
    status = mailer.deliver(
        subject, mailer.render_text(f"Hello {customer.name},", intro, rows, steps, notes), customer.email,
        html=mailer.render_html(subject, intro, rows, steps, notes), kind=kind, project=customer.product.code, attempts=1,
    )
    return status


def mask_email(address):
    """ab***@example.com - enough for a customer to recognise where mail went, never the full address."""
    user, _, domain = (address or "").partition("@")
    return f"{user[:2]}***@{domain}" if domain else "***"


def _fmt(value):
    return timezone.localtime(value).strftime("%d/%m/%Y") if value else "-"


def license_rows(lic):
    rows = [("License Plan", lic.get_license_type_display()), ("Activation Date", _fmt(lic.activated_at))]
    rows.append(("Expiry Date", "Never (Lifetime)" if lic.license_type == "LIFETIME" else _fmt(lic.expires_at)))
    return rows


def send_welcome(customer, raw_password):
    name = customer.product.name
    rows = [
        ("Product", name), ("User ID (Tally Serial Number)", customer.tally_serial), ("Temporary Password", raw_password),
    ]
    steps = [
        f"Open {name} and sign in with the User ID and password above.",
        "You can change the password any time from Profile > Change Password.",
        "Once Sai Infosys activates your license, choose Activate User in the app to receive your Activation Key by email.",
    ]
    status = _send(customer, f"Welcome to {name} - SAI INFOSYS", rows,
                   f"Your {name} account has been created. It is not licensed yet.", "tally_welcome", steps,
                   ["Do not share this password."])
    TallyCustomer.objects.filter(pk=customer.pk).update(welcome_email_status=status)
    return status


def send_license_activated(customer, raw_password):
    """Administrator activated the licence with a temporary password. The Activation Key is NOT included (it goes only to the
    customer on request); the existing password / hash is never sent."""
    name = customer.product.name
    rows = [("Customer Name", customer.name), ("Product", name), ("User ID (Tally Serial Number)", customer.tally_serial),
            ("Temporary Password", raw_password)] + license_rows(customer.license)
    steps = [f"Open {name} and sign in with the User ID and temporary password above.",
             "Change the password any time from Profile > Change Password."]
    return _send(customer, f"Your {name} license is active - SAI INFOSYS", rows, f"Your {name} license has been activated.",
                 "tally_license_activated", steps, ["Do not share this password."])


def send_activation_key(customer, raw_key):
    """Sent ONLY when the customer asks to activate a computer (Activate User). Never at registration, login or staff actions."""
    name = customer.product.name
    minutes = getattr(settings, "TALLY_ACTIVATION_KEY_TTL_SECONDS", 900) // 60
    rows = [("Product", name), ("User ID (Tally Serial Number)", customer.tally_serial), ("Activation Key", raw_key)] + license_rows(customer.license)
    steps = [f"Open {name} on the computer you want to use and choose Activate User.",
             "Enter your User ID, then this Activation Key."]
    return _send(customer, f"Your {name} Activation Key", rows,
                 f"Use this Activation Key to activate {name} on this computer. It works once and expires in {minutes} minutes.",
                 "tally_activation_key", steps,
                 ["Keep the Activation Key private.", "If you did not request this, ignore this email - nothing has changed."])


def send_password_reset(customer, token):
    name = customer.product.name
    base = customer.product.password_reset_url
    link = ""
    if base:
        link = f"{base}{'&' if '?' in base else '?'}{urlencode({'user_id': customer.tally_serial, 'token': token})}"
    minutes = getattr(settings, "PASSWORD_OTP_TTL_SECONDS", 600) // 60
    rows = [("User ID", customer.tally_serial), ("Verification Code (OTP)", token)]
    if link and len(token) > 6:  # legacy long-token links only; a 6-digit OTP is never put in a URL
        rows.append(("Reset Link", link))
    return _send(customer, f"Reset your {name} password", rows,
                 f"Use this 6-digit code to choose a new password. It works once and expires in {minutes} minutes.",
                 "tally_password_reset", notes=["Never share this code. If you did not ask for it, ignore this email."])


def send_activation_code(customer, code):
    name = customer.product.name
    rows = [("User ID", customer.tally_serial), ("Verification Code", code)]
    base = customer.product.activation_link_base
    if base:
        rows.append(("Activation Link", f"{base}{'&' if '?' in base else '?'}{urlencode({'user_id': customer.tally_serial, 'code': code}, quote_via=quote)}"))
    minutes = getattr(settings, "TALLY_CODE_TTL_SECONDS", 600) // 60
    return _send(customer, f"{name} device verification code", rows,
                 f"Use this code to activate {name} on a new computer. It works once and expires in {minutes} minutes.",
                 "tally_activation_code", notes=["If you did not request this, ignore this email - nothing has changed."])


def send_device_replaced(customer, new_device_name):
    return _send(customer, f"{customer.product.name}: license moved to a new device",
                 [("User ID", customer.tally_serial), ("New Device", new_device_name or "New computer"),
                  ("When", timezone.localtime().strftime("%d/%m/%Y %H:%M"))],
                 "Your license was activated on a new device and the previous device was signed out.",
                 "tally_device_replaced", notes=["If this was not you, contact Sai Infosys immediately."])


def send_password_changed(customer):
    return _send(customer, f"{customer.product.name} password changed", [("User ID", customer.tally_serial)],
                 "Your password was changed.", "tally_password_changed",
                 notes=["If you did not do this, use Forgot Password and contact Sai Infosys."])


def notify_superadmins_new_customer(customer):
    from apps.accounts.constants import Role

    recipients = set(get_user_model().objects.filter(is_active=True, profile__role=Role.SUPERADMIN).exclude(email="").values_list("email", flat=True))
    recipients.update(getattr(settings, "NEW_REQUEST_NOTIFY_EMAILS", []))
    for to in sorted(recipients):
        mailer.deliver(
            f"New {customer.product.name} registration",
            f"{customer.name} ({customer.email}, {customer.contact_number}) registered with Tally Serial {customer.tally_serial}.\n"
            f"Open Nexus -> Tally SL Products -> {customer.product.name} to activate the license.\n\nSai Infosys Nexus",
            to, kind="tally_new_customer", project=customer.product.code, attempts=1,
        )
