from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("integrations", "0008_api_custom_auth")]
    operations = [
        migrations.AddField("integrationlicense", "paid", models.BooleanField(default=False)),
        migrations.AddField("integrationlicense", "paid_at", models.DateTimeField(null=True, blank=True)),
        migrations.AlterField("integrationproject", "trial_days", models.PositiveSmallIntegerField(default=3)),
    ]
