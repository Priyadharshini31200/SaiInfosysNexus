from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.registrations import device_crypto


class Command(BaseCommand):
    help = "Print the public key (and key id) matching the configured LICENSE_SIGNING_PRIVATE_KEY. Never prints the private key."

    def handle(self, *args, **options):
        try:
            public = device_crypto.public_key_b64()
        except device_crypto.SigningUnavailable as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"key_id:     {settings.LICENSE_KEY_ID}\npublic_key: {public}")
