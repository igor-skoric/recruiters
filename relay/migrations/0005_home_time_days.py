from django.db import migrations, models


def forwards_weeks_to_days(apps, schema_editor):
    RelayAssignment = apps.get_model("relay", "RelayAssignment")
    for assignment in RelayAssignment.objects.all().iterator():
        weeks = assignment.home_time_weeks or 1
        assignment.home_time_days = max(1, weeks * 7)
        assignment.save(update_fields=["home_time_days"])


def backwards_days_to_weeks(apps, schema_editor):
    RelayAssignment = apps.get_model("relay", "RelayAssignment")
    for assignment in RelayAssignment.objects.all().iterator():
        days = assignment.home_time_days or 7
        assignment.home_time_weeks = max(1, (days + 6) // 7)
        assignment.save(update_fields=["home_time_weeks"])


class Migration(migrations.Migration):
    dependencies = [
        ("relay", "0004_driverstatusperiod"),
    ]

    operations = [
        migrations.AddField(
            model_name="relayassignment",
            name="home_time_days",
            field=models.PositiveSmallIntegerField(default=7),
        ),
        migrations.RunPython(forwards_weeks_to_days, backwards_days_to_weeks),
        migrations.RemoveField(
            model_name="relayassignment",
            name="home_time_weeks",
        ),
    ]
