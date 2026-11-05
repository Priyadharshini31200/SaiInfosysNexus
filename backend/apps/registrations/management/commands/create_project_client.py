import secrets

from django.core.management.base import BaseCommand, CommandError

from apps.registrations.models import PROJECTS, ProjectClient
from apps.registrations.portal_views import hash_key


class Command(BaseCommand):
    help = "Issue the API key a connected project's backend uses to call the Nexus portal API. Shown once."

    def add_arguments(self, parser):
        parser.add_argument("project", choices=sorted(PROJECTS))
        parser.add_argument("--reset-url", default="", help="Where the project's password-reset page lives (link target in emails)")
        parser.add_argument("--label", default="")
        parser.add_argument("--revoke-existing", action="store_true", help="Deactivate this project's earlier keys")

    def handle(self, *args, project, reset_url, label, revoke_existing, **options):
        if not reset_url:
            self.stderr.write("Warning: no --reset-url, so password-reset emails cannot be sent for this project.")
        raw = "nxk_" + secrets.token_urlsafe(32)
        if revoke_existing:
            ProjectClient.objects.filter(project=project).update(active=False)
        ProjectClient.objects.create(project=project, label=label, key_hash=hash_key(raw), reset_url=reset_url)
        self.stdout.write(f"Project key for {project} (store it in that project's server settings; it is not shown again):\n{raw}")
