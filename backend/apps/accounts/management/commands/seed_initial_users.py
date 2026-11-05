from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.constants import BOOTSTRAP_ACCOUNTS, Role
from apps.accounts.passwords import set_user_password
from apps.accounts.services import ensure_account


class Command(BaseCommand):
    help = (
        "Create the bootstrap Superadmin and Admin accounts if they do not exist. "
        "Idempotent: existing users (and their changed passwords) are never modified. "
        "Development bootstrap only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Permit running with DJANGO_DEBUG=False.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="DEVELOPMENT ONLY: reset Superadmin/Admin to the configured bootstrap passwords, correct role, active. Refused when DEBUG is off.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError("Refusing to seed bootstrap accounts with DEBUG off. Use --allow-production to override.")

        if options["reset"] and not settings.DEBUG:
            raise CommandError("--reset is a development tool and is refused when DJANGO_DEBUG is off. Use `changepassword` in production.")

        passwords = {
            Role.SUPERADMIN: settings.BOOTSTRAP_SUPERADMIN_PASSWORD,
            Role.ADMIN: settings.BOOTSTRAP_ADMIN_PASSWORD,
        }
        User = get_user_model()
        for username, role in BOOTSTRAP_ACCOUNTS:
            password = passwords[role]
            if not password and not User.objects.filter(username__iexact=username).exists():
                raise CommandError(f"Set the initial password for {username} in the environment (.env).")
            user, created = ensure_account(username, role, password)
            if options["reset"] and not created:
                from apps.accounts.models import UserProfile

                set_user_password(user, password)  # normalised, then hashed by Django; never stored in plaintext
                user.is_active = True
                user.save(update_fields=["password", "is_active"])
                UserProfile.objects.filter(user=user).update(role=role)
                self.stdout.write(f"{username}: reset to the bootstrap password (role {role}, active)")
            else:
                self.stdout.write(f"{username}: {'created' if created else 'already exists (unchanged)'}")
