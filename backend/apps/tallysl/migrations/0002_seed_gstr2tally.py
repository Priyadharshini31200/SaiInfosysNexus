from django.db import migrations


def seed(apps, schema_editor):
    Product = apps.get_model("tallysl", "TallyProduct")
    Product.objects.get_or_create(
        code="gstr2tally",
        defaults={"name": "GSTR2Tally", "kind": "gstr", "default_trial_days": 3,
                  "activation_link_base": "gstr2tally://activate", "password_reset_url": "gstr2tally://reset-password"},
    )


class Migration(migrations.Migration):
    dependencies = [("tallysl", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
