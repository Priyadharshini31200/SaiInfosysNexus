from django.db import migrations, models


HEADINGS = {
    "entry": "Entry Access",
    "view": "View Access",
    "edit": "Edit Access",
    "delete": "Delete Access",
    "reports": "Reports Access",
    "export_print": "Export / Print Access",
}


def name_and_seed(apps, schema_editor):
    """Give every existing row its heading, and make sure Entry, Edit and Delete are listed
    (Entry ON, Edit and Delete OFF: the earlier defaults, so no Admin's access changes)."""
    Perm = apps.get_model("accounts", "AdminPermission")
    for row in Perm.objects.all():
        row.name = HEADINGS[row.action]
        row.save(update_fields=["name"])
    for action, enabled in (("entry", True), ("edit", False), ("delete", False)):
        Perm.objects.get_or_create(action=action, defaults={"name": HEADINGS[action], "enabled": enabled})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_global_admin_permission"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="adminpermission", name="uniq_admin_permission_action"),
        migrations.AddField(
            model_name="adminpermission",
            name="name",
            field=models.CharField(default="", max_length=60),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="adminpermission",
            name="action",
            field=models.CharField(
                blank=True,
                choices=[
                    ("entry", "Entry"),
                    ("view", "View"),
                    ("edit", "Edit"),
                    ("delete", "Delete"),
                    ("reports", "Reports"),
                    ("export_print", "Export / Print"),
                ],
                max_length=20,
                null=True,
            ),
        ),
        migrations.RunPython(name_and_seed, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="adminpermission",
            constraint=models.UniqueConstraint(fields=("name",), name="uniq_admin_permission_name"),
        ),
        migrations.AddConstraint(
            model_name="adminpermission",
            constraint=models.UniqueConstraint(
                condition=models.Q(("action__isnull", False)), fields=("action",), name="uniq_admin_permission_action"
            ),
        ),
    ]
