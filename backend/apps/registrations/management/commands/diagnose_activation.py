import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.registrations import device, device_crypto as dc, portal, services
from apps.registrations.models import PROJECTS, ProjectCustomer


class Command(BaseCommand):
    help = (
        "Explains why a Serial Number + Activation Key would be refused, using redacted output only: it never prints a "
        "password, hash, full key or private key and changes nothing. Use --ask-key to type the key privately and learn only "
        "whether it matches."
    )

    def add_arguments(self, parser):
        parser.add_argument("--serial", required=True, help="User ID / Serial Number as the customer typed it")
        parser.add_argument("--project", default="prn-auto-print")
        parser.add_argument("--ask-key", action="store_true", help="prompt (hidden) for the Activation Key and report only match / no match")

    def handle(self, *args, **opts):
        project, serial = opts["project"], opts["serial"].strip()
        if project not in PROJECTS:
            raise CommandError(f"Unknown project {project!r}.")
        out = self.stdout.write
        db = connection.settings_dict
        out(f"Environment : database={db['NAME']} host={db['HOST']} DEBUG={settings.DEBUG}")
        try:
            dc._private_key()
            out(f"Signing     : configured, key id {settings.LICENSE_KEY_ID}, public key {dc.public_key_b64()[:10]}...")
        except dc.SigningUnavailable:
            out("Signing     : NOT CONFIGURED -> activation answers 503 licensing_unavailable even with correct credentials")
        out(f"Email       : backend={settings.EMAIL_BACKEND.rsplit('.', 2)[-2]} (a console/file backend delivers nothing)")
        out(f"Project     : {project} device licensing={'yes' if project in device.DEVICE_PROJECTS else 'NO - activation is not available'}")

        customer = ProjectCustomer.objects.filter(project=project, serial_number=serial).first()
        if customer is None:
            elsewhere = list(ProjectCustomer.objects.filter(serial_number=serial).exclude(project=project).values_list("project", flat=True))
            by_user = ProjectCustomer.objects.filter(project=project, user_id__iexact=serial).exists()
            out(f"Customer    : NOT FOUND for serial ...{serial[-3:]} in this database/project")
            if elsewhere:
                out(f"              the same serial exists under another product: {', '.join(elsewhere)}")
            if by_user:
                out("              a customer has this as User ID but a different Serial Number")
            out(f"Known       : {ProjectCustomer.objects.filter(project=project).count()} customers in this project here "
                f"(serials ending {', '.join(sorted(c[-3:] for c in ProjectCustomer.objects.filter(project=project).values_list('serial_number', flat=True) if c)[:12]) or '-'})")
            out("Verdict     : this Nexus database does not hold that customer -> check the backend the app is pointed at")
            return

        now = timezone.now()
        out(f"Customer    : found (id {customer.pk}) user_id==serial: {customer.user_id == customer.serial_number}")
        out(f"License     : {customer.get_license_type_display()} status={customer.status} state={device.device_license_state(customer, now)}"
            f" first_activated={'yes' if customer.first_activated_at else 'no'} expires={services.effective_expiry(customer)}")
        out(f"Key on file : ends ...{customer.activation_key_suffix}")
        out(f"Devices     : {customer.devices.filter(revoked_at__isnull=True).count()} active, blocked={customer.device_blocked}")
        out(f"Credentials : temporary password pending={customer.must_change_password}; email status="
            f"{portal.EMAIL_STATUS_LABELS.get(customer.credentials_email_status, 'Not sent')}")
        if opts["ask_key"]:
            typed = getpass.getpass("Activation Key (hidden): ")
            ok = bool(typed) and services._hash(services.normalize_key(typed)) == customer.activation_key_hash
            out(f"Key check   : {'MATCHES' if ok else 'DOES NOT MATCH'} the key on file")
        state = device.device_license_state(customer, now)
        if state != "active":
            out(f"Verdict     : the licence is {state}; activation is refused with that reason")
        elif customer.device_blocked:
            out("Verdict     : device blocked by a Super Admin; a transfer must be authorised")
        else:
            out("Verdict     : serial exists and the licence is active; a refusal can only be a wrong key or another computer already activated")
