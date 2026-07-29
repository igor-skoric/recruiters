from django.contrib import admin

from drivers.models import Driver


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "driver_id",
        "division",
        "status",
        "employment_status",
        "driver_type",
        "phone",
        "email",
        "hire_date",
        "last_synced_at",
    )
    list_filter = ("status", "employment_status", "driver_type", "division")
    search_fields = ("first_name", "last_name", "driver_id", "phone", "email")
    autocomplete_fields = ("division",)
    readonly_fields = ("last_synced_at",)
    list_per_page = 50
    list_max_show_all = 200
    show_full_result_count = False
