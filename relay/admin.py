from django.contrib import admin

from relay.models import DriverStatusPeriod, RelayAssignment, RelayStatusOverride


@admin.register(RelayStatusOverride)
class RelayStatusOverrideAdmin(admin.ModelAdmin):
    list_display = (
        "truck",
        "driver",
        "cycle_start_date",
        "expected_home_date",
        "next_driver",
        "next_driver_start_date",
        "status_override",
        "updated_by",
        "updated_at",
    )
    list_filter = ("status_override",)
    search_fields = ("truck__unit_number", "notes")
    autocomplete_fields = ("truck", "driver", "next_driver", "updated_by")


@admin.register(RelayAssignment)
class RelayAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "truck",
        "driver",
        "start_date",
        "expected_end_date",
        "actual_end_date",
        "status",
        "cycle_weeks",
        "home_time_weeks",
    )
    list_filter = ("status", "start_date")
    search_fields = (
        "truck__unit_number",
        "driver__first_name",
        "driver__last_name",
        "notes",
    )
    autocomplete_fields = (
        "truck",
        "driver",
        "previous_assignment",
        "next_assignment",
        "created_by",
    )
    date_hierarchy = "start_date"


@admin.register(DriverStatusPeriod)
class DriverStatusPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "driver",
        "status",
        "start_date",
        "end_date",
        "assignment",
        "updated_at",
    )
    list_filter = ("status",)
    search_fields = (
        "driver__first_name",
        "driver__last_name",
        "notes",
        "assignment__truck__unit_number",
    )
    autocomplete_fields = ("driver", "assignment")
    date_hierarchy = "start_date"
