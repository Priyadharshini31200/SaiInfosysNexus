from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = "apps.integrations"
    label = "integrations"
    verbose_name = "Universal Licensing Integrations"

    def ready(self):
        from . import scheduler
        scheduler.start()  # automatic customer synchronisation (no-op in tests, migrations and one-off commands)
