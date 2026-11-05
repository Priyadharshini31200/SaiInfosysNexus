"""Sends ONE test email through the configured mail settings and reports the outcome, so SMTP can be proven before real customers depend on it.

    python manage.py send_test_email you@your-domain.com

It uses the same sender as customer credentials (retries + EmailLog) but carries no customer data, password or key.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.registrations import mailer


class Command(BaseCommand):
    help = "Send a test email through the configured SMTP settings and print the result."

    def add_arguments(self, parser):
        parser.add_argument("recipient")

    def handle(self, *args, **options):
        to = options["recipient"]
        self.stdout.write(
            f"Backend: {settings.EMAIL_BACKEND}\nHost: {settings.EMAIL_HOST or '(none)'}:{settings.EMAIL_PORT} "
            f"TLS={settings.EMAIL_USE_TLS} SSL={settings.EMAIL_USE_SSL} user={'set' if settings.EMAIL_HOST_USER else '(none)'}\nFrom: {settings.DEFAULT_FROM_EMAIL}"
        )
        rows = [("Purpose", "SMTP delivery test"), ("Contains", "no customer data, password or key")]
        status = mailer.deliver(
            "SAI INFOSYS Nexus - email delivery test",
            mailer.render_text("Hello,", "This is a test message from Sai Infosys Nexus.", rows),
            to,
            html=mailer.render_html("Email delivery test", "This is a test message from Sai Infosys Nexus.", rows),
            kind="smtp_test",
        )
        from apps.registrations.models import EmailLog

        log = EmailLog.objects.filter(kind="smtp_test", recipient=to).first()
        self.stdout.write(f"Result: {status} after {log.attempts} attempt(s)" + (f"\nError: {log.error}" if log and log.error else ""))
        if status == "failed":
            raise CommandError("The mail server did not accept the message.")
        if status == "not_delivered":
            raise CommandError("Only a development mail backend is configured (console/file); nothing left this machine.")
        self.stdout.write("The mail server accepted the message. Confirm it arrives in the recipient's inbox (SMTP gives no delivery receipt).")
