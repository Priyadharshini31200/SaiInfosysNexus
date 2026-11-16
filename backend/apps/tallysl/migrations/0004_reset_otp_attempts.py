from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tallysl", "0003_transfer_events"),
    ]

    operations = [
        migrations.AddField(
            model_name="tallypasswordreset",
            name="attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
