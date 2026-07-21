from django.contrib import admin

from drivers.models import Driver


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "driver_id",
        "status",
        "driver_type",
        "phone",
        "email",
        "hire_date",
    )
    list_filter = ("status", "driver_type")
    search_fields = ("first_name", "last_name", "driver_id", "phone", "email")
