"""Automatic customer synchronisation. A small in-process scheduler thread (started with the web process) calls `run_due()` every minute;
the same function backs `manage.py sync_integration_customers` for an external scheduler. Safe with several workers: `pull_sync` takes an
atomic per-project claim, so a project is never synchronised twice at once, and registration is idempotent per external customer id."""
import logging
import os
import sys
import threading
import time

from django.conf import settings
from django.utils import timezone

from .models import IntegrationApiConfig, IntegrationSyncCheckpoint
from .services import IntegrationError

log = logging.getLogger(__name__)
TICK_SECONDS = 60
_started = False


def run_due(now=None, force=False):
    """Pull every enabled, polling project whose next run is due. Failures are logged (IntegrationSyncLog) and retried with backoff."""
    from . import pull
    now = now or timezone.now()
    out = []
    for cfg in IntegrationApiConfig.objects.filter(sync_mode="POLL", project__enabled=True).select_related("project"):
        cp = IntegrationSyncCheckpoint.objects.filter(project=cfg.project).first()
        if not force and cp and cp.next_run_at and cp.next_run_at > now:
            continue
        try:
            out.append((cfg.project.code, "OK", pull.pull_sync(cfg.project, actor="scheduler")))
        except (IntegrationError, pull.UnsafeUrl) as exc:
            out.append((cfg.project.code, "FAILED", str(exc)))
        except Exception:  # noqa: BLE001 - one project must never stop the scheduler
            log.exception("Scheduled sync of %s crashed", cfg.project.code)
            out.append((cfg.project.code, "CRASHED", ""))
    return out


def _loop():
    time.sleep(15)  # let the process finish starting
    while True:
        try:
            run_due()
        except Exception:  # noqa: BLE001
            log.exception("Customer sync scheduler tick failed")
        time.sleep(TICK_SECONDS)


def should_start():
    """Production: NO thread inside web workers - run ONE `manage.py run_sync_worker` process (Windows service / Task Scheduler / systemd).
    Development convenience: the single `runserver` child process may schedule by itself. NEXUS_SYNC_SCHEDULER=off disables even that;
    =thread forces the thread (only safe with exactly one web process)."""
    mode = str(getattr(settings, "NEXUS_SYNC_SCHEDULER", os.environ.get("NEXUS_SYNC_SCHEDULER", "worker"))).lower()
    if mode in ("0", "off", "false", "no"):
        return False
    argv = sys.argv
    if any(a in argv for a in ("test", "migrate", "makemigrations", "shell", "check", "collectstatic", "run_sync_worker", "sync_integration_customers")):
        return False
    if mode == "thread":
        return True
    return len(argv) > 1 and argv[0].endswith("manage.py") and argv[1] == "runserver" and os.environ.get("RUN_MAIN") == "true"


def start():
    global _started
    if _started or not should_start():
        return
    _started = True
    threading.Thread(target=_loop, name="nexus-customer-sync", daemon=True).start()
