from django.db import models


class TruckStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    OTR = "otr", "OTR"
    YARD = "yard", "Yard"
    MAINTENANCE = "maintenance", "Maintenance"
    INACTIVE = "inactive", "Inactive"


class Truck(models.Model):
    # TODO: Sync truck records from Pro Transport PostgreSQL database.
    unit_number = models.CharField(max_length=50, unique=True)
    vin = models.CharField(max_length=17, blank=True)
    make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TruckStatus.choices,
        default=TruckStatus.AVAILABLE,
    )
    current_driver = models.ForeignKey(
        "drivers.Driver",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_trucks",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["unit_number"]

    def __str__(self) -> str:
        return self.unit_number
