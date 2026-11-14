"""Issues an API key for a Tally SL product client (shown once; only the hash is stored).

    python manage.py create_tally_product_key gstr2tally --label "GSTR2Tally agent"
"""
import secrets

from django.core.management.base import BaseCommand, CommandError

from apps.tallysl.client_views import hash_key
from apps.tallysl.models import TallyProduct, TallyProductKey


class Command(BaseCommand):
    help = "Create an X-Product-Key for a registered Tally SL product."

    def add_arguments(self, parser):
        parser.add_argument("product")
        parser.add_argument("--label", default="")

    def handle(self, *args, product, label, **options):
        row = TallyProduct.objects.filter(code=product).first()
        if row is None:
            raise CommandError(f"Unknown product '{product}'. Register it in Nexus first.")
        raw = "tsl_" + secrets.token_urlsafe(32)
        TallyProductKey.objects.create(product=row, label=label[:80], key_hash=hash_key(raw))
        self.stdout.write(f"API key for {product} (store it now; it cannot be shown again):\n{raw}")
