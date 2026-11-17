"""Registers Mothys Inventory Management in the Project Registry (no credentials: a Super Admin generates them in Nexus)."""
from django.db import migrations


def seed(apps, schema_editor):
    P = apps.get_model("integrations", "IntegrationProject")
    P.objects.get_or_create(code="MOTHYS", defaults=dict(
        name="Mothys Inventory Management", frontend_url="https://inventory.tallysolutionstamilnadu.com",
        backend_url="https://inventory.tallysolutionstamilnadu.com/api", framework="FastAPI", auth_method="JWT Bearer",
        registration_method="API", tally_serial_required=False, serial_prefix="9", serial_length=9, activation_key_length=9,
        device_limit=1, default_license_type="TRIAL", trial_days=3, transfer_policy="SELF", auto_sync=True, usage_tracking=True,
        welcome_email_mode="ON_REGISTER", activation_email_mode="ON_REQUEST"))


class Migration(migrations.Migration):
    dependencies = [("integrations", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
