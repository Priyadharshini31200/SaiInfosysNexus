from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tallysl", "0004_reset_otp_attempts")]
    operations = [
        migrations.AddField("tallylicense", "paid", models.BooleanField(default=False)),
        migrations.AddField("tallylicense", "paid_at", models.DateTimeField(null=True, blank=True)),
    ]
