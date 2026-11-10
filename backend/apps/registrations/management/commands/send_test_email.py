"""Checks the outgoing mail configuration and sends one test email through the same path as customer credentials.

    python manage.py send_test_email you@example.com

Shows the settings in use (never the password), signs in to the SMTP server on its own first so a wrong
App Password is reported clearly, then sends a short test message via mailer.deliver (logged in EmailLog).
"""
import smtplib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from apps.registrations import mailer


class Command(BaseCommand):
    help = "Verify SMTP settings and send a test email to the given address."

    def add_arguments(self, parser):
        parser.add_argument("recipient")

    def handle(self, *args, recipient, **options):
        try:
            validate_email(recipient)
        except ValidationError:
            raise CommandError(f"Not a valid email address: {recipient}")

        s = settings
        self.stdout.write(f"Backend      : {s.EMAIL_BACKEND}")
        self.stdout.write(f"Host / port  : {s.EMAIL_HOST or '(not set)'}:{s.EMAIL_PORT}  TLS={s.EMAIL_USE_TLS} SSL={s.EMAIL_USE_SSL}")
        self.stdout.write(f"User         : {s.EMAIL_HOST_USER or '(not set)'}")
        self.stdout.write(f"Password     : {'set (' + str(len(s.EMAIL_HOST_PASSWORD)) + ' chars)' if s.EMAIL_HOST_PASSWORD else '(not set)'}")
        self.stdout.write(f"From         : {s.DEFAULT_FROM_EMAIL}")

        if not mailer.is_configured():
            raise CommandError(
                "No mail server is configured: set EMAIL_HOST, EMAIL_HOST_USER and EMAIL_HOST_PASSWORD in backend/.env "
                "and restart the backend."
            )

        if "smtp" in s.EMAIL_BACKEND:
            if not (s.EMAIL_HOST_USER and s.EMAIL_HOST_PASSWORD):
                raise CommandError(
                    "EMAIL_HOST_USER and EMAIL_HOST_PASSWORD must both be set in backend/.env (Gmail needs a signed-in sender), "
                    "then restart the backend."
                )
            self.stdout.write("Signing in to the SMTP server ...")
            try:
                connection = get_connection(fail_silently=False)
                connection.open()
                connection.close()
            except smtplib.SMTPAuthenticationError as exc:
                raise CommandError(
                    "SMTP sign-in was refused. For Gmail, EMAIL_HOST_PASSWORD must be a 16-character App Password "
                    "(Google Account > Security > 2-Step Verification > App passwords), not the normal Gmail password. "
                    f"Server said: {mailer._safe_error(exc)}"
                )
            except Exception as exc:
                raise CommandError(f"Could not connect to {s.EMAIL_HOST}:{s.EMAIL_PORT}: {mailer._safe_error(exc)}")
            self.stdout.write(self.style.SUCCESS("Signed in."))

        text = mailer.render_text(
            "Hello,", "This is a test message from Sai Infosys Nexus. If you can read it, customer credential emails will be delivered.", []
        )
        html = mailer.render_html(
            "Nexus email test", "This is a test message from Sai Infosys Nexus. If you can read it, customer credential emails will be delivered.", []
        )
        status = mailer.deliver("Sai Infosys Nexus - email test", text, recipient, html=html, kind="smtp_test", attempts=1)
        if status != "accepted":
            raise CommandError(f"Test email was not sent (status: {status}). See the registrations EmailLog table for the reason.")
        self.stdout.write(self.style.SUCCESS(f"Test email sent to the SMTP server for {recipient}. Check that inbox (and Spam)."))
