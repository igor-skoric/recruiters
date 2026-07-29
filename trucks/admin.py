from django.contrib import admin

from trucks.models import Truck


@admin.register(Truck)
class TruckAdmin(admin.ModelAdmin):
    list_display = (
        "unit_number",
        "protransport_id",
        "division",
        "status",
        "source_is_active",
        "current_driver",
        "make",
        "model",
        "year",
        "vin",
        "last_synced_at",
    )
    list_filter = ("status", "source_is_active", "make", "division")
    search_fields = ("unit_number", "protransport_id", "vin", "make", "model")
    autocomplete_fields = ("current_driver", "division")
    readonly_fields = ("last_synced_at",)
    list_per_page = 50
    list_max_show_all = 200
    show_full_result_count = False
