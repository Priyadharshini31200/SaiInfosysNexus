from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive only: existing customers keep their password hash, license and expiry untouched."""

    dependencies = [
        ("registrations", "0003_updated_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectcustomer",
            name="password_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="projectcustomer",
            name="failed_logins",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="projectcustomer",
            name="locked_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
