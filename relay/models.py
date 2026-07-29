from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from trucks.models import TruckStatus


class AssignmentStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class ReviewStatus(models.TextChoices):
    OK = "ok", "OK"
    MISSING_CURRENT_DRIVER = "missing_current_driver", "Missing current driver"
    MISSING_CYCLE_START_DATE = "missing_cycle_start_date", "Missing cycle start date"
    NEEDS_RELAY_PLANNING = "needs_relay_planning", "Needs relay planning"
    IN_YARD = "in_yard", "In yard"
    MAINTENANCE = "maintenance", "Maintenance"


class DriverPeriodStatus(models.TextChoices):
    OTR = "otr", "OTR"
    HOME_TIME = "home_time", "Home Time"
    AVAILABLE = "available", "Available"
    VACATION = "vacation", "Vacation"
    UNAVAILABLE = "unavailable", "Unavailable"
    INACTIVE = "inactive", "Inactive"


class RelayStatusOverride(models.Model):
    """
    Fallback corrections when sync data is incomplete.

    Not the historical source of truth. When ACTIVE/PLANNED RelayAssignment
    exists for a truck, assignment wins for driver, dates, and occupancy.
    Override is used only as a fallback / planning notes layer.
    """

    truck = models.OneToOneField(
        "trucks.Truck",
        on_delete=models.CASCADE,
        related_name="status_override",
    )
    driver = models.ForeignKey(
        "drivers.Driver",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="status_overrides",
    )
    cycle_start_date = models.DateField(null=True, blank=True)
    expected_home_date = models.DateField(null=True, blank=True)
    next_driver = models.ForeignKey(
        "drivers.Driver",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="next_driver_overrides",
    )
    next_driver_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the next driver takes over this truck (start of their OTR cycle).",
    )
    status_override = models.CharField(
        max_length=20,
        choices=TruckStatus.choices,
        blank=True,
    )
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="relay_overrides_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["truck__unit_number"]

    def __str__(self) -> str:
        return f"Override — {self.truck.unit_number}"


class RelayAssignment(models.Model):
    """
    Historical period when a driver operated a truck.

    Intervals are half-open: [start_date, end_date). A new assignment may
    start on the same calendar day the previous one ends without overlapping.
    """

    truck = models.ForeignKey(
        "trucks.Truck",
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    driver = models.ForeignKey(
        "drivers.Driver",
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    start_date = models.DateField()
    expected_end_date = models.DateField()
    actual_end_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.PLANNED,
    )
    cycle_weeks = models.PositiveSmallIntegerField(default=4)
    home_time_days = models.PositiveSmallIntegerField(default=7)
    previous_assignment = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="next_assignments",
    )
    next_assignment = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="previous_assignments",
    )
    notes = models.TextField(blank=True)
    start_date_is_estimated = models.BooleanField(
        default=False,
        help_text="True when start_date came from bootstrap cutover/default and still needs review.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "truck__unit_number"]

    def __str__(self) -> str:
        return f"{self.truck} — {self.driver} ({self.start_date})"

    @property
    def effective_end_date(self):
        """Exclusive end of occupancy: [start_date, effective_end_date)."""
        return self.actual_end_date or self.expected_end_date

    @property
    def expected_home_time_date(self):
        return self.expected_end_date

    @property
    def days_until_home_time(self) -> int | None:
        from django.utils import timezone

        if self.status not in {AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED}:
            return None
        today = timezone.localdate()
        return (self.expected_end_date - today).days

    def clean(self):
        super().clean()
        if self.start_date and self.effective_end_date:
            if self.effective_end_date <= self.start_date:
                raise ValidationError(
                    {"expected_end_date": "End date must be after start date ([start, end))."}
                )
        if self.status != AssignmentStatus.CANCELLED:
            from relay.services.relay_service import validate_assignment_overlap

            validate_assignment_overlap(self)

    def save(self, *args, **kwargs):
        # Enforce overlap rules via admin, shell, and service paths.
        self.full_clean()
        return super().save(*args, **kwargs)


class DriverStatusPeriod(models.Model):
    """Immutable-style history of a driver's operational status over time."""

    driver = models.ForeignKey(
        "drivers.Driver",
        on_delete=models.CASCADE,
        related_name="status_periods",
    )
    status = models.CharField(max_length=20, choices=DriverPeriodStatus.choices)
    start_date = models.DateField()
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Exclusive end date when set ([start_date, end_date)).",
    )
    assignment = models.ForeignKey(
        RelayAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="driver_status_periods",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]

    def __str__(self) -> str:
        end = self.end_date.isoformat() if self.end_date else "open"
        return f"{self.driver} — {self.status} ({self.start_date} → {end})"
