from django.db import models


class DriverStatus(models.TextChoices):
    """Fleet Planner operational / planning status (not Pro Transport employment)."""

    ACTIVE = "active", "Active"
    HOME_TIME = "home_time", "Home Time"
    INACTIVE = "inactive", "Inactive"
    PENDING = "pending", "Pending"
    TERMINATED = "terminated", "Terminated"


class EmploymentStatus(models.TextChoices):
    """Employment status mirrored from Pro Transport (master data only)."""

    ACTIVE = "active", "Active"
    TERMINATED = "terminated", "Terminated"
    INACTIVE = "inactive", "Inactive"
    UNKNOWN = "unknown", "Unknown"


# Soft roster: keep rows in DB, hide inactive PT employment from default lists/forms.
INACTIVE_EMPLOYMENT_STATUSES = frozenset(
    {
        EmploymentStatus.TERMINATED,
        EmploymentStatus.INACTIVE,
    }
)


class DriverType(models.TextChoices):
    COMPANY_DRIVER = "company_driver", "Company Driver"
    OWNER_OPERATOR = "owner_operator", "Owner Operator"


class Driver(models.Model):
    # Stable Pro Transport primary key (drivers.id). Not renamed to avoid
    # breaking imports/UI; semantically this IS the PT identifier.
    driver_id = models.CharField(
        "Pro Transport ID",
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Pro Transport drivers.id (stable external key). Do not match by name.",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=DriverStatus.choices,
        default=DriverStatus.PENDING,
        help_text="Local Fleet Planner operational status (OTR/home time workflow).",
    )
    employment_status = models.CharField(
        max_length=20,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.UNKNOWN,
        help_text="Employment status from Pro Transport master sync.",
    )
    driver_type = models.CharField(
        max_length=20,
        choices=DriverType.choices,
        default=DriverType.COMPANY_DRIVER,
    )
    hire_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    division = models.ForeignKey(
        "companies.CompanyData",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drivers",
        help_text="Pro Transport division (company_data via division_id).",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return self.full_name

    def save(self, *args, **kwargs):
        if self.driver_id is not None:
            self.driver_id = self.driver_id.strip() or None
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @classmethod
    def on_roster(cls):
        """
        Soft PT roster: exclude terminated/inactive employment and owner operators.

        Rows with inactive employment stay in the DB (hidden from default lists).
        Owner operators are out of Fleet Planner scope entirely.
        """
        return cls.objects.exclude(
            employment_status__in=INACTIVE_EMPLOYMENT_STATUSES
        ).exclude(driver_type=DriverType.OWNER_OPERATOR)
