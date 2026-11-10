from django.conf import settings
from django.core.management.commands.runserver import Command as RunserverCommand


class Command(RunserverCommand):
    """`manage.py runserver` with Nexus' development port as the default.

    Port 8000 belongs to Barcode Auto Print (PRN) on the shared dev machine; frontend/.env
    (VITE_API_PROXY_TARGET) and PRN's NEXUS_BASE_URL both expect Nexus on 8001. A bare
    `runserver` used to land on 8000, leaving the Vite proxy with nothing to reach (502 on login).
    An explicit address/port argument still wins.

    Binds 127.0.0.1 unless NEXUS_LAN_TEST_MODE=True (development only, enforced in settings), which binds
    0.0.0.0 so another PC on the LAN can reach it. Restrict the port to the LAN with the Windows firewall
    rule in docs/LAN-TEST.md; never expose this plain-HTTP server publicly.
    """

    default_port = "8001"

    @property
    def default_addr(self):
        return "0.0.0.0" if getattr(settings, "NEXUS_LAN_TEST_MODE", False) else "127.0.0.1"

    @default_addr.setter
    def default_addr(self, value):  # Django's BaseCommand/runserver assigns this attribute in some versions
        pass
