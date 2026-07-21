from django.db import models


class DriverStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    HOME_TIME = "home_time", "Home Time"
    INACTIVE = "inactive", "Inactive"
    PENDING = "pending", "Pending"
    TERMINATED = "terminated", "Terminated"


class DriverType(models.TextChoices):
    COMPANY_DRIVER = "company_driver", "Company Driver"
    OWNER_OPERATOR = "owner_operator", "Owner Operator"


class Driver(models.Model):
    # TODO: Sync driver records from Pro Transport PostgreSQL database.
    driver_id = models.CharField(
        "Driver ID",
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        help_text="Source Driver ID used for import linking (same value as in Excel).",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=DriverStatus.choices,
        default=DriverStatus.PENDING,
    )
    driver_type = models.CharField(
        max_length=20,
        choices=DriverType.choices,
        default=DriverType.COMPANY_DRIVER,
    )
    hire_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
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
