from django.contrib import admin

from trucks.models import Truck


@admin.register(Truck)
class TruckAdmin(admin.ModelAdmin):
    list_display = (
        "unit_number",
        "status",
        "current_driver",
        "make",
        "model",
        "year",
        "vin",
    )
    list_filter = ("status", "make")
    search_fields = ("unit_number", "vin", "make", "model")
    autocomplete_fields = ("current_driver",)
