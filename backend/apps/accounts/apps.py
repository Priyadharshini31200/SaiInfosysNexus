import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def bootstrap_dev_accounts(sender, **kwargs):
    """Development only: after `migrate`, create the Superadmin and Admin accounts if they are missing, so a fresh
    development database can be signed in to straight away. It only ever CREATES: an existing account (and any password
    changed since) is never modified, and nothing happens at normal startup. Does nothing unless DJANGO_DEBUG is on."""
    from django.conf import settings

    if not settings.DEBUG:
        return
    from .constants import BOOTSTRAP_ACCOUNTS, Role
    from .services import ensure_account

    passwords = {Role.SUPERADMIN: settings.BOOTSTRAP_SUPERADMIN_PASSWORD, Role.ADMIN: settings.BOOTSTRAP_ADMIN_PASSWORD}
    for username, role in BOOTSTRAP_ACCOUNTS:
        if not passwords[role]:
            logger.warning("Development account %s not created: its initial password is not set in the environment.", username)
            continue
        _, created = ensure_account(username, role, passwords[role])
        if created:
            logger.warning("Development account %s created (DEBUG is on).", username)


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        post_migrate.connect(bootstrap_dev_accounts, sender=self, dispatch_uid="accounts-bootstrap-dev-accounts")
