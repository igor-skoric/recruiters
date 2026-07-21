from django.db import migrations, models


def grant_full_role_capabilities(apps, schema_editor):
    Role = apps.get_model("departments", "Role")
    Role.objects.filter(access_level="full").update(
        can_create=True,
        can_edit=True,
        can_delete=True,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("departments", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="can_create",
            field=models.BooleanField(
                default=False,
                help_text="Add and import drivers/trucks.",
                verbose_name="Can create",
            ),
        ),
        migrations.AddField(
            model_name="role",
            name="can_edit",
            field=models.BooleanField(
                default=False,
                help_text="Edit drivers/trucks and manage relay assignments.",
                verbose_name="Can edit",
            ),
        ),
        migrations.AddField(
            model_name="role",
            name="can_delete",
            field=models.BooleanField(
                default=False,
                help_text="Delete drivers and trucks.",
                verbose_name="Can delete",
            ),
        ),
        migrations.AlterField(
            model_name="role",
            name="access_level",
            field=models.CharField(
                choices=[("read", "Read Only"), ("full", "Full Access")],
                default="read",
                help_text="Legacy coarse level. Prefer the capability flags below for new rules.",
                max_length=10,
            ),
        ),
        migrations.RunPython(grant_full_role_capabilities, noop_reverse),
    ]
