from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("drivers", "0002_driver_external_id"),
    ]

    operations = [
        migrations.RenameField(
            model_name="driver",
            old_name="external_id",
            new_name="driver_id",
        ),
        migrations.AlterField(
            model_name="driver",
            name="driver_id",
            field=models.CharField(
                blank=True,
                help_text="Source Driver ID used for import linking (same value as in Excel).",
                max_length=64,
                null=True,
                unique=True,
                verbose_name="Driver ID",
            ),
        ),
    ]
