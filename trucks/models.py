from django.db import models


class TruckStatus(models.TextChoices):
    """Fleet Planner operational / planning truck status (not Pro Transport)."""

    AVAILABLE = "available", "Available"
    OTR = "otr", "OTR"
    YARD = "yard", "Yard"
    MAINTENANCE = "maintenance", "Maintenance"
    INACTIVE = "inactive", "Inactive"


class Truck(models.Model):
    # Stable Pro Transport primary key (trucks.id). unit_number is master data, not identity.
    protransport_id = models.CharField(
        "Pro Transport ID",
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Pro Transport trucks.id (stable external key). Do not match by unit_number.",
    )
    unit_number = models.CharField(max_length=50, unique=True)
    vin = models.CharField(max_length=17, blank=True)
    make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TruckStatus.choices,
        default=TruckStatus.AVAILABLE,
        help_text="Local Fleet Planner operational status (OTR/yard/maintenance).",
    )
    # Mirrored from Pro Transport is_active / total-loss style flags (master only).
    source_is_active = models.BooleanField(
        null=True,
        blank=True,
        help_text="Active flag from Pro Transport; never drives RelayAssignment.",
    )
    current_driver = models.ForeignKey(
        "drivers.Driver",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_trucks",
        help_text="Derived cache from ACTIVE RelayAssignment; do not set from sync.",
    )
    division = models.ForeignKey(
        "companies.CompanyData",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trucks",
        help_text="Pro Transport division (company_data via division_id).",
    )
    notes = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["unit_number"]

    def __str__(self) -> str:
        return self.unit_number

    def save(self, *args, **kwargs):
        if self.protransport_id is not None:
            self.protransport_id = str(self.protransport_id).strip() or None
        super().save(*args, **kwargs)
