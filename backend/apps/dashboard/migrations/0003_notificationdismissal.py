from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0002_projectdashboardvisibility_visible_until_utc"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="NotificationDismissal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(choices=[("universe", "UNIVERSE / integrations"), ("tally", "Tally SL transfers")], max_length=10)),
                ("notification_id", models.PositiveIntegerField()),
                ("dismissed_at_utc", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("user", "source", "notification_id"), name="uniq_notification_dismissal")]},
        ),
    ]
