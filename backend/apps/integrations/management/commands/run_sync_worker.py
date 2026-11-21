"""The supported production scheduler for UNIVERSE customer synchronisation: ONE long-running process.

    python manage.py run_sync_worker            # loop forever (run it as a Windows service / Task Scheduler "at startup" task / systemd unit)
    python manage.py run_sync_worker --once     # one pass (for an external scheduler such as cron)

Safe to restart at any time (state lives in the database), safe to start twice by mistake (per-project atomic claim), per-project intervals,
retry with backoff, failure logging and a failure notification after 3 consecutive failures."""
import time

from django.core.management.base import BaseCommand

from apps.integrations import scheduler


class Command(BaseCommand):
    help = "Run the UNIVERSE customer synchronisation worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit.")
        parser.add_argument("--tick", type=int, default=scheduler.TICK_SECONDS, help="Seconds between passes (default 60).")

    def handle(self, *args, **options):
        while True:
            for code, status, detail in scheduler.run_due():
                (self.stdout if status == "OK" else self.stderr).write(f"{code}: {status} {detail}")
            if options["once"]:
                return
            time.sleep(max(options["tick"], 5))
