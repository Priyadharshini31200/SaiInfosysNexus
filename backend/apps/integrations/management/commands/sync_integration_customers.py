"""Run the customer pull once for every project that is due (use from an OS scheduler if the in-process scheduler is disabled)."""
from django.core.management.base import BaseCommand

from apps.integrations import scheduler


class Command(BaseCommand):
    help = "Pull new customer registrations for every enabled Universal API project configured for polling."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Ignore the schedule and sync every polling project now.")

    def handle(self, *args, **options):
        for code, status, detail in scheduler.run_due(force=options["force"]):
            (self.stdout if status == "OK" else self.stderr).write(f"{code}: {status} {detail}")
