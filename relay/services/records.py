"""Create/update/delete helpers for driver and truck records."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from drivers.models import Driver
from relay.models import AssignmentStatus, RelayAssignment
from trucks.models import Truck


@transaction.atomic
def delete_driver(driver: Driver) -> None:
    if RelayAssignment.objects.filter(
        driver=driver,
        status__in={AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED},
    ).exists():
        raise ValidationError(
            "Cannot delete this driver while they have active or planned assignments. "
            "Complete or cancel those first."
        )

    Truck.objects.filter(current_driver=driver).update(current_driver=None)
    RelayAssignment.objects.filter(driver=driver).delete()
    driver.delete()


@transaction.atomic
def delete_truck(truck: Truck) -> None:
    if RelayAssignment.objects.filter(
        truck=truck,
        status__in={AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED},
    ).exists():
        raise ValidationError(
            "Cannot delete this truck while it has active or planned assignments. "
            "Complete or cancel those first."
        )

    RelayAssignment.objects.filter(truck=truck).delete()
    truck.delete()
