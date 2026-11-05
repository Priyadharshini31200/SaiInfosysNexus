"""Outgoing email for Nexus: branded HTML + plain-text bodies, bounded retries and a delivery log (EmailLog).

Nothing here stores or logs a message body (it can hold a temporary password); only recipient, subject, status,
attempt count and the failure reason are kept.
"""
import logging
import time
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import EmailLog

logger = logging.getLogger(__name__)

BRAND = "SAI INFOSYS"
_LOCAL_MAIL_BACKENDS = ("console", "filebased", "dummy")


def deliver(subject, text, to, *, html=None, kind="", customer=None, project="", attempts=None):
    """Sends one email, retrying transient failures, and records the outcome.

    Returns one of:
      accepted      - handed to a real mail server (SMTP gives no delivery receipt, so this is NOT "delivered")
      not_delivered - a development backend (console / file / in-memory) that never leaves this machine
      failed        - every attempt failed; the reason is in EmailLog.error
    """
    attempts_allowed = max(1, int(attempts or getattr(settings, "MAIL_SEND_ATTEMPTS", 3)))
    delay = float(getattr(settings, "MAIL_RETRY_DELAY_SECONDS", 2))
    error, attempts = "", 0
    for attempts in range(1, attempts_allowed + 1):
        try:
            message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
            if html:
                message.attach_alternative(html, "text/html")
            message.send(fail_silently=False)
            error = ""
            break
        except Exception as exc:  # smtplib / socket / ssl errors all count as a failed attempt
            error = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("Email %r to %s failed (attempt %d/%d): %s", subject, to, attempts, attempts_allowed, type(exc).__name__)
            if attempts < attempts_allowed and delay:
                time.sleep(delay * attempts)
    if error:
        status = EmailLog.FAILED
        logger.error("Email %r to %s FAILED after %d attempt(s): %s", subject, to, attempts, error)
    else:
        status = EmailLog.NOT_DELIVERED if any(n in settings.EMAIL_BACKEND for n in _LOCAL_MAIL_BACKENDS) else EmailLog.SENT
    try:
        EmailLog.objects.create(
            kind=kind[:30], project=project, customer=customer if getattr(customer, "pk", None) else None,
            recipient=to[:254], subject=subject[:200], status=status, attempts=attempts, error=error,
        )
    except Exception:  # the log must never turn a sent mail into an error
        logger.exception("Could not write the email log")
    return status


# ---------------------------------------------------------------- branded HTML
_STYLE = {
    "page": "margin:0;padding:0;background:#f3f5f9;font-family:Segoe UI,Arial,sans-serif;color:#1f2937;",
    "card": "max-width:600px;margin:24px auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;",
    "head": "background:#0b3b8c;color:#ffffff;padding:20px 28px;font-size:20px;font-weight:600;letter-spacing:1px;",
    "body": "padding:24px 28px;font-size:14px;line-height:1.55;",
    "label": "padding:8px 12px;background:#f8fafc;border:1px solid #e5e7eb;font-weight:600;width:38%;font-size:13px;",
    "value": "padding:8px 12px;border:1px solid #e5e7eb;font-size:13px;",
    "foot": "padding:16px 28px;background:#f8fafc;color:#6b7280;font-size:12px;border-top:1px solid #e5e7eb;",
}


def render_html(title, intro, rows, steps=(), notes=()):
    """A SAI INFOSYS branded HTML email. `rows` is [(label, value)]; every value is escaped."""
    table = "".join(
        f'<tr><td style="{_STYLE["label"]}">{escape(label)}</td><td style="{_STYLE["value"]}">{escape(str(value))}</td></tr>'
        for label, value in rows
    )
    step_html = (
        '<p style="margin:18px 0 6px;font-weight:600;">To get started</p><ol style="margin:0;padding-left:20px;">'
        + "".join(f"<li>{escape(s)}</li>" for s in steps) + "</ol>"
        if steps else ""
    )
    note_html = "".join(f'<p style="margin:14px 0 0;color:#4b5563;font-size:13px;">{escape(n)}</p>' for n in notes)
    return (
        f'<!doctype html><html><body style="{_STYLE["page"]}"><div style="{_STYLE["card"]}">'
        f'<div style="{_STYLE["head"]}">{BRAND}</div>'
        f'<div style="{_STYLE["body"]}"><h2 style="margin:0 0 10px;font-size:18px;">{escape(title)}</h2>'
        f'<p style="margin:0 0 16px;">{escape(intro)}</p>'
        f'<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;">{table}</table>'
        f"{step_html}{note_html}</div>"
        f'<div style="{_STYLE["foot"]}">{BRAND} &middot; Sai Infosys Nexus &middot; This is an automated message; do not share your credentials.</div>'
        "</div></body></html>"
    )


def render_text(greeting, intro, rows, steps=(), notes=()):
    lines = [greeting, "", intro, ""]
    lines += [f"{label}: {value}" for label, value in rows]
    if steps:
        lines += ["", "To get started:"] + [f"  {i}. {s}" for i, s in enumerate(steps, 1)]
    for note in notes:
        lines += ["", note]
    lines += ["", BRAND, "Sai Infosys Nexus"]
    return "\n".join(lines)
