from django.db import migrations, models


def copy_role_rows(apps, schema_editor):
    """Keep anything saved with the earlier per-project switches: each becomes three list entries."""
    Old = apps.get_model("accounts", "RoleProjectPermission")
    New = apps.get_model("accounts", "AdminPermission")
    for row in Old.objects.filter(role="ADMIN"):
        for action in ("entry", "edit", "delete"):
            New.objects.get_or_create(project=row.project, action=action, defaults={"enabled": getattr(row, f"can_{action}")})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_role_project_permission"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminPermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("project", models.CharField(max_length=50)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("entry", "Entry"),
                            ("view", "View"),
                            ("edit", "Edit"),
                            ("delete", "Delete"),
                            ("reports", "Reports"),
                            ("export_print", "Export / Print"),
                        ],
                        max_length=20,
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="adminpermission",
            constraint=models.UniqueConstraint(fields=("project", "action"), name="uniq_admin_permission"),
        ),
        migrations.RunPython(copy_role_rows, migrations.RunPython.noop),
        migrations.DeleteModel(name="RoleProjectPermission"),
    ]
