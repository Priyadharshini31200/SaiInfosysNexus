from django.db import migrations, models


def collapse_to_global(apps, schema_editor):
    """Per-project rows become one row per action (ON only if every project had it ON), then the
    Entry and Edit headings are guaranteed to exist. Entry is ON and Edit OFF, the earlier default."""
    Perm = apps.get_model("accounts", "AdminPermission")
    merged = {}
    for row in Perm.objects.order_by("id"):
        merged[row.action] = merged.get(row.action, True) and row.enabled
    Perm.objects.all().delete()
    for action, enabled in merged.items():
        Perm.objects.create(action=action, enabled=enabled)
    for action, enabled in (("entry", True), ("edit", False)):
        Perm.objects.get_or_create(action=action, defaults={"enabled": enabled})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_admin_permission"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="adminpermission", name="uniq_admin_permission"),
        migrations.RunPython(collapse_to_global, migrations.RunPython.noop),
        migrations.RemoveField(model_name="adminpermission", name="project"),
        migrations.AddConstraint(
            model_name="adminpermission",
            constraint=models.UniqueConstraint(fields=("action",), name="uniq_admin_permission_action"),
        ),
    ]
