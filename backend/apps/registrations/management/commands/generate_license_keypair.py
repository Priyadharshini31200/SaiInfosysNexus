from django.core.management.base import BaseCommand

from apps.registrations import device_crypto


class Command(BaseCommand):
    help = (
        "Generate a new Ed25519 licence-signing keypair. The PRIVATE key is printed once (to stdout): put it in the server's "
        "secret store as LICENSE_SIGNING_PRIVATE_KEY and nowhere else. Give Barcode Auto Print only the public key and key id."
    )

    def add_arguments(self, parser):
        parser.add_argument("--key-id", default="k1", help="Identifier for this key (k1, k2... for rotation)")

    def handle(self, *args, key_id, **options):
        private, public = device_crypto.generate_keypair()
        self.stdout.write(f"key_id:      {key_id}\npublic_key:  {public}\n")
        self.stdout.write("PRIVATE key (shown once; server environment only, never commit or send it):")
        self.stdout.write(f"LICENSE_SIGNING_PRIVATE_KEY={private}")
        self.stdout.write(f"LICENSE_KEY_ID={key_id}")
