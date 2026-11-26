"""READ-ONLY credential/licence diagnostic for every Nexus customer (Universal-integration projects and GSTR2Tally).
Prints counts per project and issue type. It never prints passwords or hashes and never writes to the database."""
from collections import Counter

from django.contrib.auth.hashers import identify_hasher
from django.core.management.base import BaseCommand

from apps.integrations.models import IntegrationCustomer
from apps.tallysl.models import TallyCustomer


def _hash_problem(h):
    if not h:
        return "missing_password_hash"
    try:
        identify_hasher(h)
    except ValueError:
        return "unsupported_hash_format"
    return None


class Command(BaseCommand):
    help = "Read-only: counts of credential / licence inconsistencies per project."

    def handle(self, *args, **opts):
        found, total = Counter(), Counter()
        for c in IntegrationCustomer.objects.select_related("project", "license").iterator():
            p = c.project.code
            total[p] += 1
            for issue in (_hash_problem(c.password_hash), None if c.user_id else "missing_user_id",
                          None if hasattr(c, "license") else "missing_license",
                          "welcome_email_failed" if c.welcome_email_status == "failed" else None,
                          "welcome_email_never_sent" if not c.welcome_email_status else None):
                if issue:
                    found[(p, issue)] += 1
        for c in TallyCustomer.objects.select_related("product").iterator():
            p = c.product.code
            total[p] += 1
            for issue in (_hash_problem(c.password_hash), None if hasattr(c, "license") else "missing_license"):
                if issue:
                    found[(p, issue)] += 1
        self.stdout.write("project, customers")
        for p, n in sorted(total.items()):
            self.stdout.write(f"{p}, {n}")
        self.stdout.write("\nproject, issue, count")
        for (p, issue), n in sorted(found.items()):
            self.stdout.write(f"{p}, {issue}, {n}")
        if not found:
            self.stdout.write("No issues found.")
        self.stdout.write("\nRead-only: nothing was changed.")
