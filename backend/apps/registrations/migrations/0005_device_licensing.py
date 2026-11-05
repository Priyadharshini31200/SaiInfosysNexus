import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive only. No existing customer, licence or expiry row is modified (first_activated_at starts empty)."""

    dependencies = [
        ("registrations", "0004_password_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectcustomer",
            name="first_activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="DeviceRegistration",
            fields=[
                ("device_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("project", models.CharField(db_index=True, max_length=40)),
                ("device_public_key", models.CharField(max_length=64)),
                ("fingerprint_hashes", models.TextField()),
                ("device_name", models.CharField(blank=True, default="", max_length=100)),
                ("app_version", models.CharField(blank=True, default="", max_length=30)),
                ("generation", models.PositiveIntegerField(default=1)),
                ("enrolled_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_reason", models.CharField(blank=True, default="", max_length=200)),
                (
                    "customer",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="devices", to="registrations.projectcustomer"),
                ),
            ],
            options={"ordering": ["-enrolled_at"]},
        ),
        migrations.AddConstraint(
            model_name="deviceregistration",
            constraint=models.UniqueConstraint(
                condition=models.Q(("revoked_at__isnull", True)), fields=("customer",), name="uniq_active_device_per_customer"
            ),
        ),
        migrations.CreateModel(
            name="DeviceNonce",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nonce", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "device",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="registrations.deviceregistration"),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="devicenonce",
            constraint=models.UniqueConstraint(fields=("device", "nonce"), name="uniq_device_nonce"),
        ),
    ]
